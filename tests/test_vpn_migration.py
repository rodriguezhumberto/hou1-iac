"""Offline integration checks using the actual Ansible roles and templates.

Run: python3 -m unittest discover -s tests -v
No VPS, VPN enrollment, credentials, or Docker daemon is used.
"""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from helpers import ROLES, guard_play, netbird_vars, run_play, run_plays


class MigrationTest(unittest.TestCase):
    def test_rollout_guards(self):
        nb = netbird_vars()
        recovery = 'Keep WireGuard recovery throughout this migration stage'
        config = 'Validate self-hosted NetBird configuration'
        address = 'Require a declared server address in the account subnet'
        scenarios = [
            ({}, None),
            ({**nb, 'management_vpn': 'netbird'}, None),
            ({'wireguard_enabled': False}, recovery),
            ({'management_vpn': 'netbird'}, recovery),
            ({**nb, 'netbird_version': '0.83.0'}, config),
            ({**nb, 'netbird_ip': ''}, address),
            ({**nb, 'netbird_ip': '100.90.0.0'}, address),
            ({**nb, 'netbird_ip': '100.90.255.254'}, address),
            ({**nb, 'netbird_ip': '100.90.255.255'}, address),
            ({**nb, 'netbird_wireguard_port': 51820}, config),
            ({**nb, 'netbird_management_url': 'https://api.netbird.io'}, config),
            ({**nb, 'netbird_management_url': 'http://netbird.example.org'}, config),
            ({**nb, 'netbird_subnet': '0.0.0.0/0; allow all'}, config),
            ({**nb, 'management_vpn': 'netbird', 'netbird_ip': '192.168.1.2'}, address),
            (nb, None),  # A rescued failure must not affect the next scenario.
        ]
        tasks = [{'ansible.builtin.import_role': {'name': 'management_vpn'}}]
        result = run_plays([guard_play(variables, tasks, expected_guard)
                            for variables, expected_guard in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_private_ssh_template_preserves_settings_and_is_idempotent(self):
        # Real Ansible rendering checks both initial migration and subsequent runs.
        template = str(ROLES / 'security/templates/sshd_config.j2')
        tasks = [
            {'ansible.builtin.set_fact': {
                'ssh_config_before': {'content': base64.b64encode(
                    b'# provider config\nListenAddress 10.10.0.10\nPort 22\n'
                    b'Include /etc/ssh/sshd_config.d/*.conf\n').decode()}}},
            {'ansible.builtin.set_fact': {
                'first_render': "{{ lookup('template', '" + template + "') }}"}},
            {'ansible.builtin.assert': {'that': [
                "'ListenAddress 100.90.0.10' in first_render",
                "'ListenAddress 10.10.0.10' in first_render",
                "'Port 22' in first_render",
                "'Include /etc/ssh/sshd_config.d/*.conf' in first_render",
                "'0.0.0.0' not in first_render",
            ]}},
            {'ansible.builtin.set_fact': {
                'ssh_config_before': {'content': '{{ first_render | b64encode }}'}}},
            {'ansible.builtin.assert': {'that': [
                "lookup('template', '" + template + "') == first_render"]}},
        ]
        result = run_play(netbird_vars(), tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_effective_ssh_listener_guard(self):
        tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'security/tasks/verify-listeners.yml')}]
        guard = 'Reject listeners outside the selected private VPN addresses'
        plays = []
        for listeners, valid in [
            (['listenaddress 10.10.0.10:22', 'listenaddress 100.90.0.10:22'], True),
            (['listenaddress 0.0.0.0:22'], False),
            (['listenaddress [::]:22'], False),
        ]:
            plays.append(guard_play({**netbird_vars(),
                'ssh_effective_config': {'stdout_lines': listeners}},
                tasks, None if valid else guard))
        result = run_plays(plays)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_enrollment_identity_and_address_guards(self):
        tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'netbird_client/tasks' / name)}
                 for name in ['validate-address.yml', 'enrollment-check.yml', 'verify-connection.yml']]
        status = {'management': {'url': 'https://netbird.example.org', 'connected': True},
                  'wireguardPort': 51821, 'netbirdIp': '100.90.0.10/16'}
        variables = {**netbird_vars(),
            'netbird_before_status': status,
            'netbird_marker_stat': {'stat': {'exists': True}},
            'netbird_marker': {'content': base64.b64encode(
                b'{"management_url":"https://netbird.example.org"}').decode()},
            'netbird_after': {'stdout': json.dumps(status)}}
        connection_guard = 'Verify the management server and actual listening port'
        scenarios = [({}, None),
            ({'netbird_before_status': {**status, 'management': {
                  'url': 'https://NETBIRD.example.org:443/', 'connected': True}},
              'netbird_after': {'stdout': json.dumps({**status, 'management': {
                  'url': 'https://netbird.example.org:443', 'connected': True}})},
              'netbird_marker': {'content': base64.b64encode(
                  b'{"management_url":"https://NETBIRD.example.org:443/"}').decode()}}, None),
            ({'netbird_after': {'stdout': json.dumps({**status, 'management': {
                  'url': 'https://netbird.example.org:8443', 'connected': True}})}}, connection_guard),
            ({'netbird_after': {'stdout': json.dumps({**status, 'management': {
                  'url': 'http://netbird.example.org:443', 'connected': True}})}}, connection_guard),
            ({'netbird_management_url': 'https://another.example.org'},
             'Refuse to change the recorded enrollment endpoint')]
        result = run_plays([guard_play({**variables, **overrides}, tasks, expected_guard)
                            for overrides, expected_guard in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cms_allowlist_for_each_stage(self):
        acl_tasks = [
            {'ansible.builtin.import_tasks': str(ROLES / 'webstack/tasks/cms-acl.yml')},
            {'ansible.builtin.assert': {'that': [
                "staff_vpn_subnet == (netbird_subnet if netbird_enabled else vpn_subnet)"]}},
        ]
        source = '''server {
    server_name cms.example.org;
    allow ${vpn_subnet};
    deny all;
}
'''
        scenarios = [(False, True, 'wireguard'), (True, True, 'wireguard'),
                     (True, True, 'netbird')]
        with tempfile.TemporaryDirectory() as directory:
            plays = []
            outputs = []
            for index, (nb, wg, primary) in enumerate(scenarios):
                app_directory = Path(directory) / str(index)
                nginx = app_directory / 'nginx'
                nginx.mkdir(parents=True)
                template = nginx / 'nginx.conf.template'
                template.write_text(source)
                variables = {**netbird_vars(), 'netbird_enabled': nb,
                             'wireguard_enabled': wg, 'management_vpn': primary,
                             'app_directory': str(app_directory)}
                plays.append(dict(variables=variables, tasks=acl_tasks))
                outputs.append((nb, wg, primary, template))
            result = run_plays(plays)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for nb, wg, primary, template in outputs:
                with self.subTest(netbird=nb, wireguard=wg, management_vpn=primary):
                    rendered = template.read_text()
                    self.assertEqual('allow 100.90.0.0/16;' in rendered, nb)
                    self.assertEqual('allow 10.10.0.0/24;' in rendered, wg and primary == 'wireguard')
                    self.assertIn('deny all;', rendered)
                    self.assertNotIn('${vpn_subnet}', rendered)

    def test_changed_application_template_fails_closed(self):
        tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'webstack/tasks/cms-acl.yml')}]
        with tempfile.TemporaryDirectory() as directory:
            nginx = Path(directory) / 'nginx'
            nginx.mkdir()
            template = nginx / 'nginx.conf.template'
            original = 'server { deny all; }\n'
            template.write_text(original)
            result = run_play({**netbird_vars(), 'app_directory': directory}, tasks)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(template.read_text(), original)


if __name__ == '__main__':
    unittest.main()
