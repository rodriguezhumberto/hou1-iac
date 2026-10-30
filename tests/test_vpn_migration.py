"""Offline integration checks using the actual Ansible roles and templates.

Run: python3 -m unittest discover -s tests -v
No VPS, VPN enrollment, credentials, or Docker daemon is used.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLES = ROOT / 'ansible/playbooks/roles'
DEFAULTS = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())


class MigrationTest(unittest.TestCase):
    def run_play(self, variables, tasks, extra_args=()):
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'play.yml'
            play.write_text(yaml.safe_dump([dict(
                hosts='localhost', gather_facts=False,
                vars={**DEFAULTS, 'hou1_vpn_ip': '10.10.0.10',
                      'ansible_host': '100.90.0.10', **variables,
                      'ansible_python_interpreter': sys.executable}, tasks=tasks,
            )]))
            env = {**os.environ, 'ANSIBLE_ROLES_PATH': str(ROLES),
                   'ANSIBLE_NOCOLOR': '1', 'ANSIBLE_LOCALHOST_WARNING': 'False'}
            return subprocess.run(
                ['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play), *extra_args],
                env=env, text=True, capture_output=True, timeout=40,
            )

    @staticmethod
    def netbird_vars():
        return dict(netbird_enabled=True,
                    netbird_management_url='https://netbird.example.org',
                    netbird_subnet='100.90.0.0/16', netbird_ip='100.90.0.10')

    def test_rollout_guards(self):
        nb = self.netbird_vars()
        scenarios = [
            ({}, True),
            (nb, True),
            ({**nb, 'management_vpn': 'netbird', 'wireguard_enabled': False}, True),
            ({'wireguard_enabled': False}, False),
            ({'management_vpn': 'netbird'}, False),
            ({**nb, 'netbird_wireguard_port': 51820}, False),
            ({**nb, 'netbird_management_url': 'https://api.netbird.io'}, False),
            ({**nb, 'netbird_management_url': 'http://netbird.example.org'}, False),
            ({**nb, 'netbird_subnet': '0.0.0.0/0; allow all'}, False),
            ({**nb, 'management_vpn': 'netbird', 'netbird_ip': '192.168.1.2'}, False),
            ({**nb, 'management_vpn': 'netbird', 'wireguard_enabled': False,
              'ansible_host': '10.10.0.10'}, False),
        ]
        for variables, valid in scenarios:
            with self.subTest(variables=variables):
                result = self.run_play(variables, [{'ansible.builtin.import_role': {'name': 'vpn_config'}}])
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
            {'ansible.builtin.set_fact': {'wireguard_enabled': False}},
            {'ansible.builtin.set_fact': {
                'final_render': "{{ lookup('template', '" + template + "') }}"}},
            {'ansible.builtin.assert': {'that': [
                "'ListenAddress 10.10.0.10' not in final_render",
                "'ListenAddress 100.90.0.10' in final_render"]}},
        ]
        result = self.run_play(self.netbird_vars(), tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_effective_ssh_listener_guard(self):
        task = next(task for task in yaml.safe_load(
            (ROLES / 'security/tasks/main.yml').read_text())[-1]['block']
            if task['name'].startswith('Reject listeners'))
        for listeners, valid in [
            (['listenaddress 10.10.0.10:22', 'listenaddress 100.90.0.10:22'], True),
            (['listenaddress 0.0.0.0:22'], False),
            (['listenaddress [::]:22'], False),
        ]:
            with self.subTest(listeners=listeners):
                result = self.run_play({**self.netbird_vars(),
                    'ssh_effective_config': {'stdout_lines': listeners}}, [task])
                self.assertEqual(result.returncode == 0, valid,
                                 result.stdout + result.stderr)

    def test_enrollment_identity_and_address_guards(self):
        role = yaml.safe_load((ROLES / 'netbird/tasks/main.yml').read_text())
        names = ['Decide whether first enrollment is required',
                 'Refuse to change the recorded enrollment endpoint',
                 'Refuse to move an enrolled peer to another management server',
                 'Require a setup key only for first enrollment',
                 'Verify the management server and actual listening port',
                 'Verify the peer belongs to the configured subnet']
        tasks = [task for task in role if task['name'] in names]
        status = {'management': {'url': 'https://netbird.example.org', 'connected': True},
                  'wireguardPort': 51821, 'netbirdIp': '100.90.0.10/16'}
        variables = {**self.netbird_vars(),
            'netbird_before_status': status,
            'netbird_marker_stat': {'stat': {'exists': True}},
            'netbird_marker': {'content': base64.b64encode(
                b'{"management_url":"https://netbird.example.org"}').decode()},
            'netbird_after': {'stdout': json.dumps(status)},
            'netbird_interface_state': {'stdout': json.dumps([
                {'addr_info': [{'local': '100.90.0.10'}]}])}}
        scenarios = [({}, True),
            # A previously verified peer can reconnect without a setup key even
            # when the daemon initially reports no IP after restart.
            ({'netbird_before_status': {'netbirdIp': ''}}, True),
            ({'netbird_before_status': {'netbirdIp': ''},
              'netbird_marker_stat': {'stat': {'exists': False}}}, False),
            ({'netbird_before_status': {'netbirdIp': ''},
              'netbird_marker_stat': {'stat': {'exists': False}},
              'netbird_setup_key': 'offline-test-key'}, True),
            ({'netbird_management_url': 'https://another.example.org'}, False),
            ({'netbird_subnet': '100.91.0.0/16'}, False),
            ({'netbird_ip': '100.90.0.11'}, False)]
        for overrides, valid in scenarios:
            with self.subTest(overrides=overrides):
                result = self.run_play({**variables, **overrides}, tasks)
                self.assertEqual(result.returncode == 0, valid,
                                 result.stdout + result.stderr)

    def test_cms_allowlist_for_each_stage(self):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())
        acl_tasks = tasks[1:4]
        source = '''server {
    server_name cms.example.org;
    allow ${vpn_subnet};
    deny all;
}
'''
        for nb, wg, primary in [(False, True, 'wireguard'), (True, True, 'wireguard'),
                                (True, True, 'netbird'), (True, False, 'netbird')]:
            with self.subTest(netbird=nb, wireguard=wg), tempfile.TemporaryDirectory() as directory:
                nginx = Path(directory) / 'nginx'
                nginx.mkdir()
                template = nginx / 'nginx.conf.template'
                template.write_text(source)
                variables = {**self.netbird_vars(), 'netbird_enabled': nb,
                             'wireguard_enabled': wg, 'management_vpn': primary,
                             'app_directory': directory}
                result = self.run_play(variables, acl_tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                rendered = template.read_text()
                self.assertEqual('allow 100.90.0.0/16;' in rendered, nb)
                self.assertEqual('allow 10.10.0.0/24;' in rendered, wg and primary == 'wireguard')
                self.assertIn('deny all;', rendered)
                self.assertNotIn('${vpn_subnet}', rendered)

    def test_changed_application_template_fails_closed(self):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())[1:4]
        with tempfile.TemporaryDirectory() as directory:
            nginx = Path(directory) / 'nginx'
            nginx.mkdir()
            template = nginx / 'nginx.conf.template'
            original = 'server { deny all; }\n'
            template.write_text(original)
            result = self.run_play({**self.netbird_vars(), 'app_directory': directory}, tasks)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(template.read_text(), original)


if __name__ == '__main__':
    unittest.main()
