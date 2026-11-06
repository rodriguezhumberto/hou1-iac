"""Offline integration checks using the actual Ansible roles and templates.

Run: python3 -m unittest discover -s tests -v
No VPS, VPN enrollment, credentials, or Docker daemon is used.
"""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest

from helpers import ROLES, guard_play, netbird_vars, run_play, run_plays


class ManagementTest(unittest.TestCase):
    def test_fresh_ssh_probe_preserves_pinned_proxy_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'argv.json'
            ssh = Path(directory) / 'ssh'
            ssh.write_text('#!/usr/bin/python3\nimport json,sys\n'
                           f'open({str(capture)!r}, "w").write(json.dumps(sys.argv[1:]))\n')
            ssh.chmod(0o755)
            options = ['-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=verified-test-host',
                       '-o', 'ProxyCommand=ssh test-gateway nc %h %p']
            result = run_play({**netbird_vars(), 'security_netbird_ssh_options': options}, [{
                'ansible.builtin.import_tasks': str(ROLES / 'security/tasks/verify-access.yml'),
                'environment': {'PATH': directory + ':' + os.environ['PATH']}}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            argv = json.loads(capture.read_text())
            self.assertEqual(argv[:len(options)], options)
            self.assertIn('ControlMaster=no', argv)
            self.assertIn('ControlPath=none', argv)
            self.assertEqual(argv[-2:], ['test-user@100.90.0.10', 'sudo -n true'])

    def test_management_guards(self):
        nb = netbird_vars()
        config = 'Validate self-hosted NetBird configuration'
        address = 'Require a declared server address in the account subnet'
        scenarios = [
            (nb, None),
            ({**nb, 'netbird_version': '0.83.0'}, config),
            ({**nb, 'netbird_ip': ''}, address),
            ({**nb, 'netbird_ip': '100.90.0.0'}, address),
            ({**nb, 'netbird_ip': '100.90.255.254'}, address),
            ({**nb, 'netbird_ip': '100.90.255.255'}, address),
            ({**nb, 'netbird_listen_port': 51820}, None),
            ({**nb, 'netbird_listen_port': 0}, config),
            ({**nb, 'netbird_management_url': 'https://api.netbird.io'}, config),
            ({**nb, 'netbird_management_url': 'http://netbird.example.org'}, config),
            ({**nb, 'netbird_subnet': '0.0.0.0/0; allow all'}, config),
            ({**nb, 'netbird_ip': '192.168.1.2'}, address),
            (nb, None),  # A rescued failure must not affect the next scenario.
        ]
        tasks = [{'ansible.builtin.import_role': {'name': 'netbird_common'}}]
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
                "'ListenAddress 10.10.0.10' not in first_render",
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
        guard = 'Reject listeners outside the NetBird address'
        plays = []
        for listeners, valid in [
            (['listenaddress 100.90.0.10:22'], True),
            (['listenaddress 10.10.0.10:22', 'listenaddress 100.90.0.10:22'], False),
            ([], False),
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

if __name__ == '__main__':
    unittest.main()
