"""Offline integration checks using the actual Ansible roles and templates.

Run: python3 -m unittest discover -s tests -v
No VPS, VPN enrollment, credentials, or Docker daemon is used.
"""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from helpers import ROLES, netbird_vars, run_play


class MigrationTest(unittest.TestCase):
    def test_rollout_guards(self):
        nb = netbird_vars()
        scenarios = [
            ({}, True),
            (nb, True),
            ({**nb, 'management_vpn': 'netbird'}, True),
            ({'wireguard_enabled': False}, False),
            ({'management_vpn': 'netbird'}, False),
            ({**nb, 'netbird_version': '0.83.0'}, False),
            ({**nb, 'netbird_ip': ''}, False),
            ({**nb, 'netbird_ip': '100.90.0.0'}, False),
            ({**nb, 'netbird_ip': '100.90.255.254'}, False),
            ({**nb, 'netbird_ip': '100.90.255.255'}, False),
            ({**nb, 'netbird_wireguard_port': 51820}, False),
            ({**nb, 'netbird_management_url': 'https://api.netbird.io'}, False),
            ({**nb, 'netbird_management_url': 'http://netbird.example.org'}, False),
            ({**nb, 'netbird_subnet': '0.0.0.0/0; allow all'}, False),
            ({**nb, 'management_vpn': 'netbird', 'netbird_ip': '192.168.1.2'}, False),
        ]
        for variables, valid in scenarios:
            with self.subTest(variables=variables):
                result = run_play(variables, [{'ansible.builtin.import_role': {'name': 'management_vpn'}}])
                self.assertEqual(result.returncode == 0, valid,
                                 result.stdout + result.stderr)

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
        for listeners, valid in [
            (['listenaddress 10.10.0.10:22', 'listenaddress 100.90.0.10:22'], True),
            (['listenaddress 0.0.0.0:22'], False),
            (['listenaddress [::]:22'], False),
        ]:
            with self.subTest(listeners=listeners):
                result = run_play({**netbird_vars(),
                    'ssh_effective_config': {'stdout_lines': listeners}}, tasks)
                self.assertEqual(result.returncode == 0, valid,
                                 result.stdout + result.stderr)

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
        scenarios = [({}, True),
            ({'netbird_before_status': {**status, 'management': {
                  'url': 'https://NETBIRD.example.org:443/', 'connected': True}},
              'netbird_after': {'stdout': json.dumps({**status, 'management': {
                  'url': 'https://netbird.example.org:443', 'connected': True}})},
              'netbird_marker': {'content': base64.b64encode(
                  b'{"management_url":"https://NETBIRD.example.org:443/"}').decode()}}, True),
            ({'netbird_after': {'stdout': json.dumps({**status, 'management': {
                  'url': 'https://netbird.example.org:8443', 'connected': True}})}}, False),
            ({'netbird_after': {'stdout': json.dumps({**status, 'management': {
                  'url': 'http://netbird.example.org:443', 'connected': True}})}}, False),
            ({'netbird_management_url': 'https://another.example.org'}, False)]
        for overrides, valid in scenarios:
            with self.subTest(overrides=overrides):
                result = run_play({**variables, **overrides}, tasks)
                self.assertEqual(result.returncode == 0, valid,
                                 result.stdout + result.stderr)

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
        for nb, wg, primary in [(False, True, 'wireguard'), (True, True, 'wireguard'),
                                (True, True, 'netbird')]:
            with self.subTest(netbird=nb, wireguard=wg), tempfile.TemporaryDirectory() as directory:
                nginx = Path(directory) / 'nginx'
                nginx.mkdir()
                template = nginx / 'nginx.conf.template'
                template.write_text(source)
                variables = {**netbird_vars(), 'netbird_enabled': nb,
                             'wireguard_enabled': wg, 'management_vpn': primary,
                             'app_directory': directory}
                result = run_play(variables, acl_tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
