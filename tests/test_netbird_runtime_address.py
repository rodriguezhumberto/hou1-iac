"""Exercise declared address verification with the actual SSH template and VPN defaults."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from helpers import ROLES, ROOT, run_play

import yaml


class RuntimeAddressTest(unittest.TestCase):
    def test_declared_address_configures_ssh_without_switching_management(self):
        template = str(ROLES / 'security/templates/sshd_config.j2')
        variables = dict(netbird_enabled=True, netbird_ip='100.90.0.23',
                         netbird_after={'stdout': json.dumps({'netbirdIp': '100.90.0.23/16'})},
                         netbird_interface_state={'stdout': json.dumps([
                             {'addr_info': [{'local': '100.90.0.23'}]}])},
                         netbird_server_peer={'ip': '100.90.0.23'},
                         ssh_config_before={'content': base64.b64encode(b'Port 22\n').decode()})
        tasks = [
            {'ansible.builtin.import_tasks': str(ROLES / 'netbird_client/tasks/verify-address.yml')},
            {'ansible.builtin.set_fact': {'rendered': "{{ lookup('template', '" + template + "') }}"}},
            {'ansible.builtin.assert': {'that': [
                "netbird_ip == '100.90.0.23'",
                "'ListenAddress 100.90.0.23' in rendered",
                "'ListenAddress 10.10.0.10' in rendered",
                "management_ip == '10.10.0.10'",
                "staff_vpn_subnet == '100.90.0.0/16'",
            ]}},
        ]
        result = run_play(variables, tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        variables['netbird_after']['stdout'] = json.dumps({'netbirdIp': '100.90.0.24/16'})
        result = run_play(variables, tasks)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_waits_for_address_propagation_and_rejects_a_stale_client(self):
        main = yaml.safe_load((ROLES / 'netbird_client/tasks/main.yml').read_text())
        waits = [task for task in main if task.get('register') in
                 ('netbird_after', 'netbird_interface_state') and 'until' in task
                 and 'declared address' in task['name']]
        self.assertEqual(len(waits), 2)
        for task in waits:
            task.update(retries=2, delay=0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for binary in ('netbird', 'ip'):
                script = root / binary
                script.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
count = pathlib.Path(os.environ['ADDRESS_FIXTURE_DIRECTORY']) / (name + '.count')
n = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
ip = '100.90.0.23' if n > 1 and os.environ['ADDRESS_FIXTURE_STALE'] == 'false' else '100.90.0.99'
if name == 'netbird':
    print(json.dumps({'netbirdIp': ip + '/16', 'management': {'connected': True}}))
else:
    print(json.dumps([{'addr_info': [{'local': ip}]}]))
''')
                script.chmod(0o755)
            tasks = [{'environment': {
                'PATH': str(root) + ':' + os.environ['PATH'],
                'ADDRESS_FIXTURE_DIRECTORY': str(root),
                'ADDRESS_FIXTURE_STALE': '{{ fixture_stale }}'},
                'block': waits + [{'ansible.builtin.import_tasks': str(
                    ROLES / 'netbird_client/tasks/verify-address.yml')}]}]
            for stale in ('false', 'true'):
                result = run_play(dict(netbird_ip='100.90.0.23', fixture_stale=stale), tasks)
                self.assertEqual(result.returncode == 0, stale == 'false', result.stdout + result.stderr)
                if stale == 'false':
                    self.assertEqual((root / 'netbird.count').read_text(), '2')
                    self.assertEqual((root / 'ip.count').read_text(), '2')

    def test_environment_inventory_connects_using_declared_addresses(self):
        inventory = yaml.safe_load((ROOT / 'ansible/inventory/inventory.yml').read_text())
        for environment in ('test', 'prod'):
            with self.subTest(environment=environment):
                children = inventory['all']['children'][environment + '_env']['children']
                host = children['hou1_' + environment]['hosts'][environment + '_hou1']
                variables = yaml.safe_load(
                    (ROOT / ('ansible/inventory/group_vars/' + environment + '_env.yml')).read_text())
                tasks = [{'ansible.builtin.assert': {'that': [
                    "netbird_enabled | bool", "management_vpn == 'netbird'",
                    "netbird_ip == " + environment + "_netbird_ip",
                    "ansible_host == netbird_ip", "wireguard_enabled | bool",
                ]}}]
                result = run_play({**variables, 'test_netbird_ip': '100.90.0.10',
                                   'prod_netbird_ip': '100.90.0.1',
                                   'ansible_host': host['ansible_host']}, tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wireguard_staff_default_is_preserved_when_netbird_disabled(self):
        result = run_play({}, [{'ansible.builtin.assert': {'that': [
            'staff_vpn_subnet == vpn_subnet']}}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
