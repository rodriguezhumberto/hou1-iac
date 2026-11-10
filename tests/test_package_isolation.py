"""CDN DNS drift must not require broader or persistent test egress."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import yaml
from helpers import ROOT, run_plays

FILES = ROOT / 'ansible/playbooks/roles/rehearsal_gates/files'
spec = importlib.util.spec_from_file_location('package_isolation', FILES / 'isolation.py')
isolation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isolation)


def config():
    return dict(environment='test', deny_cidrs=['192.0.2.1/32', '2001:db8::1/128'],
                test_addresses=['198.51.100.1'], allow_local_cidrs=['198.51.100.1/32', '::1/128'],
                allow_update_cidrs=['203.0.113.1/32', '2001:db8:2::1/128'])


class PackageIsolationTest(unittest.TestCase):
    def test_target_dns_answers_drive_both_pins_and_bounded_rules(self):
        def dns(host, port, type):
            return [(socket.AF_INET, type, 6, '', ('65.9.148.5', port)),
                    (socket.AF_INET6, type, 6, '', ('2606:4700::1111', port, 0, 0))]
        with patch.object(isolation.socket, 'getaddrinfo', side_effect=dns):
            hosts = isolation.package_targets(config())
        self.assertEqual(set(hosts), set(isolation.PACKAGE_HOSTS))
        self.assertNotIn('65.9.148.5/32', config()['allow_update_cidrs'])
        batch = isolation.package_batch(hosts, set())
        for address in ['65.9.148.5', '2606:4700::1111']:
            self.assertIn(f'{address} timeout 1800s', batch)
        self.assertNotIn('container_forward', batch)
        self.assertNotIn('flush ruleset', batch)
        for line in batch.splitlines():
            if 'insert rule' in line:
                self.assertIn('daddr != @denied', line)
                self.assertIn('tcp dport { 80, 443 }', line)
        subsequent = isolation.package_batch(hosts, {'packages4', 'packages6'})
        self.assertNotIn('insert rule', subsequent)

    def test_dns_failure_private_denied_and_production_answers_make_no_changes(self):
        for address in ['127.0.0.1', '10.0.0.1', '192.0.2.1', '::1', 'fe80::1', '2606:4700::1111']:
            cfg = config()
            if address == '2606:4700::1111':
                cfg['deny_cidrs'].append(address + '/128')
            with self.subTest(address=address), patch.object(
                isolation.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]
            ), patch.object(isolation, 'load_batch') as nft, self.assertRaises(AssertionError):
                isolation.open_packages(cfg)
            nft.assert_not_called()
        with patch.object(isolation.socket, 'getaddrinfo', side_effect=socket.gaierror), \
                self.assertRaises(socket.gaierror):
            isolation.package_targets(config())
        with patch.object(isolation.socket, 'getaddrinfo') as dns, self.assertRaises(AssertionError):
            isolation.package_targets({**config(), 'environment': 'production'})
        dns.assert_not_called()

    def test_lease_cleanup_preserves_hosts_and_rejects_other_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hosts_file, receipt = root / 'hosts', root / 'receipt'
            original = '127.0.0.1 localhost\n# operator mapping\n8.8.4.4 unrelated.example\n\n'
            hosts_file.write_text(original)
            inode = hosts_file.stat().st_ino
            with patch.object(isolation, 'PACKAGE_HOSTS_FILE', hosts_file), \
                    patch.object(isolation, 'PACKAGE_APT_FILE', root / 'apt.conf'), \
                    patch.object(isolation, 'PACKAGE_RECEIPT', receipt), \
                    patch.object(isolation, 'package_targets', return_value={'download.docker.com': ['65.9.148.5']}), \
                    patch.object(isolation, 'package_sets', return_value={'packages4', 'packages6'}), \
                    patch.object(isolation, 'load_batch') as nft, contextlib.redirect_stdout(io.StringIO()):
                isolation.open_packages(config())
                token = json.loads(receipt.read_text())['token']
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
                self.assertIn('65.9.148.5 download.docker.com', hosts_file.read_text())
                self.assertEqual((root / 'apt.conf').read_text(), isolation.PACKAGE_APT_CONFIG)
                with self.assertRaises(AssertionError):
                    isolation.close_packages('wrong-token')
                with self.assertRaises(AssertionError):
                    isolation.open_packages(config())
                isolation.close_packages(token)
                self.assertFalse(receipt.exists())
                self.assertFalse((root / 'apt.conf').exists())
                self.assertEqual(hosts_file.read_text(), original)
                self.assertEqual(hosts_file.stat().st_ino, inode)
                self.assertNotIn('add element', nft.call_args.args[0])

    def test_partial_open_rolls_back_and_cleanup_failure_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts_file, receipt = Path(directory) / 'hosts', Path(directory) / 'receipt'
            hosts_file.write_text('127.0.0.1 localhost\n')
            with patch.object(isolation, 'PACKAGE_HOSTS_FILE', hosts_file), \
                    patch.object(isolation, 'PACKAGE_APT_FILE', Path(directory) / 'apt.conf'), \
                    patch.object(isolation, 'PACKAGE_RECEIPT', receipt), \
                    patch.object(isolation, 'package_targets', return_value={'download.docker.com': ['65.9.148.5']}), \
                    patch.object(isolation, 'package_sets', return_value={'packages4', 'packages6'}), \
                    patch.object(isolation, 'load_batch', side_effect=[RuntimeError('open failed'), None]):
                with self.assertRaisesRegex(RuntimeError, 'open failed'):
                    isolation.open_packages(config())
                self.assertFalse(receipt.exists())
                receipt.write_text(json.dumps({'token': 'fixture'}))
                with patch.object(isolation, 'load_batch', side_effect=RuntimeError('cleanup failed')):
                    with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                        isolation.close_packages('fixture')
                self.assertTrue(receipt.exists())

    def test_ansible_always_closes_lease_when_package_module_fails(self):
        tasks = yaml.safe_load((FILES.parent / 'tasks/package-access.yml').read_text())
        lease = tasks[-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'closed'
            package_tasks = root / 'packages.yml'
            package_tasks.write_text(yaml.safe_dump([{'ansible.builtin.fail': {'msg': 'expected-package-failure'}}]))
            # Keep the real wrapper's block/always/when semantics; replace only
            # privileged I/O so this regression never changes the test machine.
            lease['block'][0] = {'ansible.builtin.debug': {'msg': 'fixture helper'}}
            lease['block'][1]['ansible.builtin.command'] = {'argv': ['python3', '-c', 'print(\'{"token":"fixture"}\')']}
            lease['always'][0]['ansible.builtin.command'] = {
                'argv': ['python3', '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)]}
            result = run_plays([dict(variables={'rehearsal_package_config': 'fixture',
                                               'rehearsal_package_tasks': str(package_tasks)}, tasks=[lease])])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('expected-package-failure', result.stdout)
            self.assertTrue(marker.exists(), result.stdout + result.stderr)

    def test_borrowed_wrapper_keeps_calling_role_path_and_skips_production_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            role = Path(directory) / 'fixture-role'
            (role / 'tasks').mkdir(parents=True)
            (role / 'tasks/main.yml').write_text(yaml.safe_dump([{
                'ansible.builtin.include_tasks': str(FILES.parent / 'tasks/package-access.yml'),
                'vars': {'rehearsal_package_tasks': '{{ role_path }}/tasks/packages.yml'},
            }]))
            (role / 'tasks/packages.yml').write_text(yaml.safe_dump([
                {'ansible.builtin.set_fact': {'fixture_package_tasks_ran': True}},
            ]))
            result = run_plays([dict(variables={'cms_environment': 'production'}, tasks=[
                {'ansible.builtin.include_role': {'name': str(role)}},
                {'ansible.builtin.assert': {'that': [
                    'fixture_package_tasks_ran | bool', "rehearsal_package_config == ''",
                    'rehearsal_package_lease is skipped',
                ]}},
            ])])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
