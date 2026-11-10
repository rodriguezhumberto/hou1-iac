"""Private pulls remain possible under test isolation without permanent egress."""
import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import yaml
from helpers import ROLES, run_plays
from test_package_isolation import config, isolation


class RegistryIsolationTest(unittest.TestCase):
    def test_fixed_target_pins_and_host_https_lease_share_dns_answers(self):
        answers = [(socket.AF_INET, 1, 6, '', ('140.82.113.33', 443)),
                   (socket.AF_INET6, 1, 6, '', ('2606:50c0:8000::154', 443, 0, 0))]
        with patch.object(isolation.socket, 'getaddrinfo', return_value=answers) as dns:
            hosts = isolation.endpoint_targets(config(), isolation.REGISTRY_HOSTS)
        self.assertEqual(set(hosts), {'ghcr.io', 'pkg-containers.githubusercontent.com'})
        self.assertEqual({call.args[0] for call in dns.call_args_list}, set(hosts))
        batch = isolation.endpoint_batch(hosts, set(), 'registry', isolation.REGISTRY_TTL_SECONDS, '443')
        for address in ['140.82.113.33', '2606:50c0:8000::154']:
            self.assertIn(f'{address} timeout 1800s', batch)
        self.assertNotIn('container_forward', batch)
        self.assertNotIn('flush ruleset', batch)
        for line in batch.splitlines():
            if 'insert rule' in line:
                self.assertIn('daddr != @denied', line)
                self.assertTrue(line.endswith('tcp dport 443 accept'))

    def test_production_private_denied_or_failed_dns_cannot_open_registry_access(self):
        scenarios = [(config(), '127.0.0.1'), (config(), '192.0.2.1'),
                     ({**config(), 'environment': 'production'}, '140.82.113.33'),
                     ({**config(), 'deny_cidrs': config()['deny_cidrs'] + ['140.82.113.33/32']}, '140.82.113.33')]
        for cfg, address in scenarios:
            with self.subTest(cfg=cfg, address=address), patch.object(isolation.socket, 'getaddrinfo',
                    return_value=[(2, 1, 6, '', (address, 443))]), \
                    patch.object(isolation, 'load_batch') as nft, self.assertRaises(AssertionError):
                isolation.open_registry(cfg)
            nft.assert_not_called()
        with patch.object(isolation.socket, 'getaddrinfo', side_effect=socket.gaierror), \
                patch.object(isolation, 'load_batch') as nft, self.assertRaises(socket.gaierror):
            isolation.open_registry(config())
        nft.assert_not_called()

    def test_cleanup_preserves_other_hosts_pins_and_exclusive_lease_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hosts_file = root / 'hosts'; receipt = root / 'receipt'
            original = '127.0.0.1 localhost\n' + isolation.PACKAGE_BEGIN + '\n8.8.4.4 deb.debian.org\n' + isolation.PACKAGE_END + '\n'
            hosts_file.write_text(original); inode = hosts_file.stat().st_ino
            with patch.object(isolation, 'PACKAGE_HOSTS_FILE', hosts_file), \
                    patch.object(isolation, 'REGISTRY_RECEIPT', receipt), \
                    patch.object(isolation, 'endpoint_targets', return_value={'ghcr.io': ['140.82.113.33']}), \
                    patch.object(isolation, 'package_sets', return_value={'registry4', 'registry6'}), \
                    patch.object(isolation, 'load_batch') as nft, contextlib.redirect_stdout(io.StringIO()):
                isolation.open_registry(config()); token = json.loads(receipt.read_text())['token']
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
                self.assertIn('140.82.113.33 ghcr.io', hosts_file.read_text())
                with self.assertRaises(AssertionError): isolation.close_registry('wrong-owner')
                with self.assertRaises(AssertionError): isolation.open_registry(config())
                isolation.close_registry(token)
                self.assertEqual(hosts_file.read_text(), original)
                self.assertEqual(hosts_file.stat().st_ino, inode)
                self.assertFalse(receipt.exists())
                self.assertNotIn('add element', nft.call_args.args[0])

    def test_partial_open_cleans_up_and_failed_cleanup_retains_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hosts_file = root / 'hosts'; receipt = root / 'receipt'
            hosts_file.write_text('127.0.0.1 localhost\n')
            with patch.object(isolation, 'PACKAGE_HOSTS_FILE', hosts_file), \
                    patch.object(isolation, 'REGISTRY_RECEIPT', receipt), \
                    patch.object(isolation, 'endpoint_targets', return_value={'ghcr.io': ['140.82.113.33']}), \
                    patch.object(isolation, 'package_sets', return_value={'registry4', 'registry6'}), \
                    patch.object(isolation, 'load_batch', side_effect=[RuntimeError('open failed'), None]):
                with self.assertRaisesRegex(RuntimeError, 'open failed'): isolation.open_registry(config())
                self.assertFalse(receipt.exists())
                receipt.write_text(json.dumps({'token': 'fixture'}))
                with patch.object(isolation, 'load_batch', side_effect=RuntimeError('cleanup failed')):
                    with self.assertRaisesRegex(RuntimeError, 'cleanup failed'): isolation.close_registry('fixture')
                self.assertTrue(receipt.exists())

    def test_ansible_closes_registry_lease_when_login_fails(self):
        tasks = yaml.safe_load((ROLES / 'application_image/tasks/registry-command.yml').read_text())
        block = next(task for task in tasks if 'block' in task)
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'closed'
            block['block'] = [
                {'ansible.builtin.set_fact': {'application_image_isolation_config': 'fixture',
                     'application_image_registry_lease': {'rc': 0, 'stdout': '{"token":"fixture"}'}}},
                {'ansible.builtin.fail': {'msg': 'expected-login-failure'}},
            ]
            cleanup = block['always'][0]['always'][0]
            cleanup['ansible.builtin.command'] = {'argv': ['python3', '-c',
                'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)]}
            result = run_plays([dict(variables={'application_image_docker_config': {'path': directory + '/auth'}}, tasks=[block])])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('expected-login-failure', result.stdout)
            self.assertTrue(marker.exists(), result.stdout + result.stderr)

    def test_ansible_production_never_opens_test_lease(self):
        result = run_plays([dict(variables={'cms_environment': 'production'}, tasks=[
            {'ansible.builtin.include_role': {'name': 'application_image', 'tasks_from': 'registry-access'}},
            {'ansible.builtin.assert': {'that': ["application_image_isolation_config == ''", "application_image_registry_lease is skipped", "application_image_registry_lease.rc | default(-1) == -1"]}},
        ])])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
