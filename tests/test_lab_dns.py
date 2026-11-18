"""Test-only DNS boundaries, native resolution and removal handoffs."""

import copy
import grp
import os
from pathlib import Path
import pwd
import runpy
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest

from ansible.errors import AnsibleFilterError
from jinja2 import Template
import yaml

from helpers import ROLES, ROOT, run_play
import test_environment_targeting as targeting

plan = runpy.run_path(str(ROOT / 'ansible/playbooks/filter_plugins/lab_dns.py'))['lab_dns_plan']
NAMESPACE = 'lab-test.example.invalid'
RECORDS = [{'name': 'auth-test.' + NAMESPACE, 'address': '192.168.50.11'},
           {'name': 'staff-test.' + NAMESPACE, 'address': '192.168.50.11'}]


def make_plan(**changes):
    values = dict(namespace=NAMESPACE, address='192.168.50.11', records=RECORDS,
                  upstreams=['1.1.1.1'], management_url='https://netbird-test.' + NAMESPACE)
    return plan(**{**values, **changes})


class LabDnsTest(unittest.TestCase):
    def test_private_input_boundaries_and_dependency_cycles(self):
        self.assertEqual(make_plan()['records'], RECORDS)
        for changes in [dict(namespace='example.invalid'), dict(address='100.90.0.10'),
                dict(address='0.0.0.0'), dict(upstreams=['192.168.50.11']),
                dict(upstreams=['100.91.0.10']), dict(upstreams=['127.0.0.1']),
                dict(management_url='https://netbird.example.invalid'), dict(records=[]),
                dict(records=[{'name': 'other.example.invalid', 'address': '192.168.50.11'}]),
                dict(records=[{'name': 'auth-test.' + NAMESPACE, 'address': '100.90.0.10'}]),
                dict(records=RECORDS + [RECORDS[0]]),
                dict(records=[{'name': 'bad\nserver.' + NAMESPACE, 'address': '192.168.50.11'}])]:
            with self.subTest(changes=changes), self.assertRaises(AnsibleFilterError):
                make_plan(**changes)
        self.assertEqual(make_plan(records=[], state='absent')['records'], [])

    def test_production_and_ambiguous_inventory_are_rejected_without_connections(self):
        guard = yaml.safe_load((ROLES / 'lab_dns/tasks/guard.yml').read_text())[0]
        values = dict(lab_dns_state='present', netbird_server_group='test-servers', netbird_subnet='100.91.0.0/16')
        for groups, changes, valid in [(['test_env'], {}, True), (['prod_env'], {}, False),
                (['test_env', 'prod_env'], {}, False), (['test_env'], {'netbird_subnet': '100.90.0.0/16'}, False),
                (['test_env'], {'netbird_server_group': 'prod-servers'}, False)]:
            with self.subTest(groups=groups, changes=changes):
                fixture = targeting.EnvironmentTargetingTest()
                inventory = fixture.inventory(groups)
                result, connected = fixture.run_fixture([{'hosts': 'fixture', 'gather_facts': False,
                    'vars': {**values, **changes}, 'tasks': [guard]}], inventory)
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
                self.assertFalse(connected, result.stdout + result.stderr)

    def test_unmanaged_default_performs_no_host_operations(self):
        result = run_play({}, [{'ansible.builtin.include_role': {'name': 'lab_dns'}}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('changed=0', result.stdout)

    def fixture_tasks(self, source, root, *, public_ready=True, docker_uses_resolver=False):
        """Keep real file operations; replace privileged service operations and probes."""
        tasks = yaml.safe_load((ROLES / ('lab_dns/tasks/' + source)).read_text())
        owner = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name

        def adapt(task):
            value = copy.deepcopy(task)
            for key in ('block', 'rescue', 'always'):
                if key in value:
                    value[key] = [adapt(item) for item in value[key]]
            for module in ('file', 'slurp', 'stat', 'copy', 'blockinfile'):
                options = value.get('ansible.builtin.' + module)
                if options:
                    for key in ('path', 'src', 'dest'):
                        if key in options and str(options[key]).startswith('/'):
                            options[key] = str(root / options[key].lstrip('/'))
                    if module == 'file' and options.get('path') == '{{ item }}':
                        value['loop'] = [str(root / item.lstrip('/')) for item in value['loop']]
                    if 'owner' in options: options['owner'] = owner
                    if 'group' in options: options['group'] = group
            if 'ansible.builtin.systemd_service' in value:
                value.pop('ansible.builtin.systemd_service')
                value['ansible.builtin.debug'] = {'msg': 'fixture service operation'}
            if 'ansible.builtin.service_facts' in value:
                value.pop('ansible.builtin.service_facts')
                value['ansible.builtin.set_fact'] = {'ansible_facts': {'services': {'hou-lab-dns.service': {}}}}
            if value['name'] == 'Read Docker DNS configuration before stopping the resolver':
                result = '["192.168.50.11"]' if docker_uses_resolver else '[]'
                value['ansible.builtin.command'] = {'argv': ['/usr/bin/printf', result]}
            if value['name'] == 'Verify public resolution through the remaining configuration':
                value['ansible.builtin.command'] = {'argv': ['/bin/true' if public_ready else '/bin/false']}
            return value
        return [adapt(task) for task in tasks]

    def test_removal_preserves_provider_files_and_repeated_removal_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['etc/systemd/resolved.conf.d/90-hou-lab-dns.conf',
                         'etc/systemd/system/hou-lab-dns.service', 'etc/hou-lab-dns.conf']:
                target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text('owned lab file')
            provider = root / 'etc/systemd/resolved.conf.d/99-provider.conf'; provider.write_text('provider settings')
            link = root / 'etc/resolv.conf'; link.symlink_to(provider)
            tasks = self.fixture_tasks('absent.yml', root)
            for _ in range(2):
                result = run_play({'lab_dns_listen_address': '192.168.50.11'}, tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(provider.read_text(), 'provider settings')
            self.assertTrue(link.is_symlink())
            self.assertFalse((root / 'etc/hou-lab-dns.conf').exists())
            self.assertFalse((root / 'etc/systemd/resolved.conf.d/90-hou-lab-dns.conf').exists())

    def test_failed_dns_handoff_and_remaining_docker_consumers_preserve_resolver(self):
        for public_ready, docker_uses in [(False, False), (True, True)]:
            with self.subTest(public_ready=public_ready, docker_uses=docker_uses), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); dropin = root / 'etc/systemd/resolved.conf.d/90-hou-lab-dns.conf'
                dropin.parent.mkdir(parents=True); dropin.write_text('original lab resolver\n')
                resolver = root / 'etc/hou-lab-dns.conf'; resolver.write_text('working DNS')
                tasks = self.fixture_tasks('absent.yml', root, public_ready=public_ready, docker_uses_resolver=docker_uses)
                result = run_play({'lab_dns_listen_address': '192.168.50.11'}, tasks)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(dropin.read_text(), 'original lab resolver\n')
                self.assertEqual(resolver.read_text(), 'working DNS')

    def test_legacy_hosts_block_removal_preserves_other_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); hosts = root / 'etc/hosts'; hosts.parent.mkdir()
            original = '127.0.0.1 localhost\n192.168.50.99 unrelated.example.invalid\n'
            hosts.write_text(original + '# BEGIN hou-lab bootstrap\n192.168.50.11 auth-test.' + NAMESPACE + '\n# END hou-lab bootstrap\n')
            tasks = self.fixture_tasks('present.yml', root)
            task = next(task for task in tasks if task['name'] == 'Remove only the old managed bootstrap hosts block')
            result = run_play({}, [task, task])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(hosts.read_text(), original)

    def test_native_dns_serves_both_protocols_forwards_public_and_removes_records(self):
        binary = os.environ.get('DNSMASQ_BIN') or shutil.which('dnsmasq')
        if not binary:
            self.skipTest('Install dnsmasq-base for native lab DNS checks')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            unit = root / 'hou-lab-dns.service'
            unit.write_text((ROLES / 'lab_dns/templates/hou-lab-dns.service.j2').read_text().replace('/usr/sbin/dnsmasq', binary))
            checked = subprocess.run(['systemd-analyze', 'verify', str(unit)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            def free_port():
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0)); return listener.getsockname()[1]
            def start(name, config):
                target = root / name; target.write_text(config)
                checked = subprocess.run([binary, '--test', '--conf-file=' + str(target)], capture_output=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                process = subprocess.Popen([binary, '--keep-in-foreground', '--conf-file=' + str(target)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                def stop():
                    if process.poll() is None: process.terminate()
                    process.communicate(timeout=5)
                self.addCleanup(stop)
                port = int(next(line.split('=')[1] for line in config.splitlines() if line.startswith('port=')))
                for _ in range(60):
                    self.assertIsNone(process.poll(), 'Native lab resolver exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1): return process
                    except OSError: threading.Event().wait(0.03)
                self.fail('Native lab resolver did not become ready')
            upstream_port, port = free_port(), free_port()
            user, group = pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
            start('upstream.conf', f'port={upstream_port}\nlisten-address=127.0.0.1\nbind-interfaces\n'
                  f'no-resolv\nno-hosts\npid-file=\nuser={user}\ngroup={group}\n'
                  f'host-record=public.example.invalid,192.0.2.90\nhost-record=staff-test.{NAMESPACE},192.0.2.91\n')
            def render(records):
                desired = make_plan(records=records)
                desired.update(address='127.0.0.1', upstreams=[f'127.0.0.1#{upstream_port}'])
                config = Template((ROLES / 'lab_dns/templates/dnsmasq.conf.j2').read_text()).render(lab_dns_desired=desired)
                return config.replace('port=53', f'port={port}').replace('user=nobody', 'user=' + user).replace('group=nogroup', 'group=' + group)
            def query(name, tcp=False):
                labels = b''.join(bytes([len(part)]) + part.encode() for part in name.split('.')) + b'\0'
                data = struct.pack('!6H', 1234, 0x100, 1, 0, 0, 0) + labels + struct.pack('!2H', 1, 1)
                with socket.socket(type=socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as client:
                    client.settimeout(2); client.connect(('127.0.0.1', port))
                    if not tcp:
                        client.send(data); return client.recv(4096)
                    client.sendall(struct.pack('!H', len(data)) + data)
                    length = struct.unpack('!H', client.recv(2))[0]
                    response = b''
                    while len(response) < length:
                        block = client.recv(length - len(response))
                        self.assertTrue(block, 'Truncated TCP DNS response')
                        response += block
                    return response
            process = start('lab.conf', render(RECORDS))
            for tcp in (False, True):
                self.assertIn(socket.inet_aton(RECORDS[0]['address']), query(RECORDS[0]['name'], tcp))
                self.assertIn(socket.inet_aton('192.0.2.90'), query('public.example.invalid', tcp))
            process.terminate(); process.communicate(timeout=5)
            start('lab.conf', render(RECORDS[:1]))
            for tcp in (False, True):
                self.assertEqual(query(RECORDS[1]['name'], tcp)[3] & 15, 3)


if __name__ == '__main__':
    unittest.main()
