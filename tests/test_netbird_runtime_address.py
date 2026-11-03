"""Exercise declared address verification with the actual SSH template and VPN defaults."""
import base64
import json
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

    def test_wireguard_staff_default_is_preserved_when_netbird_disabled(self):
        result = run_play({}, [{'ansible.builtin.assert': {'that': [
            'staff_vpn_subnet == vpn_subnet']}}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
