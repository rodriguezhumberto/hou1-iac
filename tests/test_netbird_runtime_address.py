"""Exercise declared address verification with the actual SSH template and VPN defaults."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest

from helpers import ROLES, ROOT, run_play

import yaml


class RuntimeAddressTest(unittest.TestCase):
    def test_waits_for_address_propagation_and_rejects_stale_addresses(self):
        main = yaml.safe_load((ROLES / 'netbird_client/tasks/main.yml').read_text())
        waits = [task for task in main if task.get('register') in
                 ('netbird_after', 'netbird_interface_state') and 'until' in task
                 and 'netbird_ip' in task['until']]
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
# Address changes may briefly remove the interface before it is recreated.
if name == 'ip' and n == 1:
    sys.exit(1)
ip = '100.90.0.23' if n > 1 and os.environ['ADDRESS_FIXTURE_STALE'] != name else '100.90.0.99'
if name == 'netbird':
    print(json.dumps({'netbirdIp': ip + '/16', 'management': {'connected': True}}))
else:
    print(json.dumps([{'addr_info': [{'local': ip}]}]))
''')
                script.chmod(0o755)
            template = str(ROLES / 'security/templates/sshd_config.j2')
            verify = [
                {'ansible.builtin.set_fact': {'rendered': "{{ lookup('template', '" + template + "') }}"}},
                {'ansible.builtin.assert': {'that': [
                    "'ListenAddress 100.90.0.23' in rendered",
                    "'ListenAddress 10.10.0.10' in rendered",
                    "management_ip == '10.10.0.10'",
                    "staff_vpn_subnet == '100.90.0.0/16'",
                ]}},
            ]
            tasks = [{'environment': {
                'PATH': str(root) + ':' + os.environ['PATH'],
                'ADDRESS_FIXTURE_DIRECTORY': str(root),
                'ADDRESS_FIXTURE_STALE': '{{ fixture_stale }}'},
                'block': waits + verify}]
            for stale in ('none', 'netbird', 'ip'):
                for counter in root.glob('*.count'):
                    counter.unlink()
                result = run_play(dict(netbird_enabled=True, netbird_ip='100.90.0.23',
                                       fixture_stale=stale, ssh_config_before={
                                           'content': base64.b64encode(b'Port 22\n').decode()}), tasks)
                self.assertEqual(result.returncode == 0, stale == 'none', result.stdout + result.stderr)
                if stale == 'none':
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
