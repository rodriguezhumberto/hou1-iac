"""Use real Ansible enrollment tasks with a disposable API and fake CLI."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from ansible.parsing.vault import VaultLib, VaultSecret
import yaml

from helpers import ROLES, run_play


class EnrollmentTest(unittest.TestCase):
    def test_first_enrollment_reconnect_membership_and_failure_cleanup(self):
        state = {}
        token = 'fixture-admin-pat'
        key = 'fixture-oneoff-key'
        server_ip = '100.90.0.10'

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, value):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def authenticated(self):
                if self.headers.get('Authorization') != 'Token ' + token or state.get('revoked'):
                    self.reply(401, {})
                    return False
                return True

            def do_GET(self):
                if not self.authenticated():
                    return
                if self.path == '/api/groups':
                    self.reply(200, state['groups'])
                elif self.path.startswith('/api/groups/'):
                    self.reply(200, state['groups'][0])
                elif self.path == '/api/peers/server-peer':
                    self.reply(200, state['peers'][0])
                elif self.path == '/api/peers':
                    self.reply(200, state['peers'])
                else:
                    self.reply(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/enroll':
                    if body != {'key': key}:
                        self.reply(401, {})
                        return
                    group = state['groups'][0]
                    group['peers'].append({'id': 'server-peer'})
                    state['peers'] = [{'id': 'server-peer', 'ip': server_ip,
                                       'groups': [{'id': group['id'], 'name': group['name']}],
                                       'name': 'test-server', 'ssh_enabled': False,
                                       'login_expiration_enabled': False,
                                       'inactivity_expiration_enabled': True}]
                    self.reply(200, {})
                elif self.path == '/api/setup-keys' and self.authenticated():
                    state['key_posts'].append(body)
                    self.reply(200, {'id': 'generated-key', 'key': key})

            def do_DELETE(self):
                if self.authenticated():
                    state['deleted'].append(self.path)
                    self.reply(200, {})

            def do_PUT(self):
                if not self.authenticated():
                    return
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/peers/server-peer':
                    state['peer_puts'].append(body)
                    if state.get('reject_ip'):
                        self.reply(400, {})
                        return
                    state['peers'][0].update(body)
                    self.reply(200, state['peers'][0])
                    return
                state['group_puts'].append(body)
                state['groups'][0].update(body, peers=[{'id': value} for value in body['peers']])
                state['peers'][0]['groups'].append({'id': 'test', 'name': 'test-servers'})
                self.reply(200, state['groups'][0])

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                endpoint = f'http://127.0.0.1:{server.server_port}'
                password = root / 'password'
                password.write_text('fixture-vault-password')
                vault_file = root / 'token.yml'
                vault = VaultLib([('default', VaultSecret(password.read_bytes()))])
                vault_file.write_bytes(vault.encrypt(json.dumps({
                    'netbird_management_url': endpoint, 'netbird_api_token': token})))
                bin_dir = root / 'bin'
                bin_dir.mkdir()
                cli = bin_dir / 'netbird'
                cli.write_text('''#!/usr/bin/python3
import json, os, pathlib, stat, sys, urllib.request
args = sys.argv[1:]
record = {'has_key': '--setup-key-file' in args, 'args': args}
if record['has_key']:
    path = pathlib.Path(args[args.index('--setup-key-file') + 1])
    record.update(path=str(path), mode=stat.S_IMODE(path.stat().st_mode))
    value = path.read_text()
    assert value == 'fixture-oneoff-key'
    assert path.stat().st_uid == os.getuid()
    assert path.stat().st_mode & 0o777 == 0o600
pathlib.Path(os.environ['NB_FIXTURE_LOG']).write_text(json.dumps(record))
if os.environ['NB_FIXTURE_FAIL'] == 'true':
    sys.exit(2)
if record['has_key']:
    request = urllib.request.Request(os.environ['NB_FIXTURE_URL'] + '/enroll',
        data=json.dumps({'key': value}).encode(), headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(request).read()
''')
                cli.chmod(0o755)
                log = root / 'cli.json'
                status = {'management': {'url': endpoint, 'connected': True},
                          'wireguardPort': 51821, 'netbirdIp': server_ip + '/16'}
                base = {'netbird_management_url': endpoint, 'netbird_ip': server_ip,
                        'netbird_token_vault_file': str(vault_file),
                        'netbird_server_group': 'test-servers',
                        'netbird_before_status': {'netbirdIp': ''},
                        'netbird_marker_stat': {'stat': {'exists': False}},
                        'netbird_after': {'stdout': json.dumps(status)}}
                connect_tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'netbird_client/tasks' / name)}
                                 for name in ['api-context.yml', 'enrollment-check.yml']]
                connect = yaml.safe_load((ROLES / 'netbird_client/tasks/connect.yml').read_text())[0]
                # Exercise production tasks without requiring sudo on the test controller.
                # Only file ownership is adapted to the current test user.
                for task in connect['block']:
                    if 'ansible.builtin.copy' in task:
                        task['ansible.builtin.copy'].update(owner=str(os.getuid()), group=str(os.getgid()))
                connect_tasks.append(connect)
                membership = {'ansible.builtin.import_tasks': str(ROLES / 'netbird_client/tasks/membership.yml')}

                def reset():
                    state.clear()
                    state.update(groups=[{'id': 'test', 'name': 'test-servers',
                                          'peers': [{'id': 'other-server'}],
                                          'resources': [{'id': 'resource', 'type': 'host'}]}],
                                 peers=[], key_posts=[], deleted=[], group_puts=[], peer_puts=[])

                def run(valid, variables=None, fail_cli=False):
                    tasks = [{'environment': {
                        'PATH': str(bin_dir) + ':' + os.environ['PATH'],
                        'NB_FIXTURE_LOG': str(log), 'NB_FIXTURE_URL': endpoint,
                        'NB_FIXTURE_FAIL': str(fail_cli).lower()},
                        'block': connect_tasks + [membership, {
                            'ansible.builtin.import_tasks': str(ROLES / 'netbird_client/tasks/assign-address.yml')}]}]
                    result = run_play({**base, **(variables or {})}, tasks,
                                      extra_args=['--vault-password-file', str(password), '--diff', '-v'])
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode == 0, valid, output)
                    for secret in [token, key, password.read_text()]:
                        self.assertNotIn(secret, output)
                    return output

                reset()
                run(True)
                self.assertEqual(state['key_posts'], [{
                    'name': 'hou1-localhost', 'type': 'one-off', 'usage_limit': 1,
                    'expires_in': 600, 'ephemeral': False, 'auto_groups': ['test']}])
                self.assertEqual(state['deleted'], ['/api/setup-keys/generated-key'])
                record = json.loads(log.read_text())
                self.assertTrue(record['has_key'])
                self.assertNotIn(key, record['args'])
                self.assertFalse(Path(record['path']).exists())
                self.assertEqual(state['group_puts'], [])  # auto_groups assigned the server.

                enrolled = {'netbird_before_status': status}
                run(True, enrolled)
                self.assertFalse(json.loads(log.read_text())['has_key'])
                self.assertEqual(len(state['key_posts']), 1)
                self.assertEqual(len(state['deleted']), 1)

                run(True, {'netbird_before_status': {'netbirdIp': ''},
                           'netbird_marker_stat': {'stat': {'exists': True}},
                           'netbird_marker': {'content': base64.b64encode(json.dumps(
                               {'management_url': endpoint}).encode()).decode()}})
                self.assertEqual(len(state['key_posts']), 1)

                # Membership repair preserves other peers and resource assignments.
                state['groups'][0]['peers'] = [{'id': 'other-server'}]
                state['peers'][0]['groups'] = []
                run(True, enrolled)
                self.assertEqual(state['group_puts'], [{
                    'name': 'test-servers', 'peers': ['other-server', 'server-peer'],
                    'resources': [{'id': 'resource', 'type': 'host'}]}])
                run(True, enrolled)
                self.assertEqual(len(state['group_puts']), 1)

                # Reassignment targets the observed peer ID and preserves all required settings.
                desired = '100.90.0.23'
                run(True, {**enrolled, 'netbird_ip': desired})
                self.assertEqual(state['peer_puts'], [{
                    'ip': desired, 'name': 'test-server', 'ssh_enabled': False,
                    'login_expiration_enabled': False, 'inactivity_expiration_enabled': True}])
                assigned = {'netbird_before_status': {**status, 'netbirdIp': desired + '/16'},
                            'netbird_after': {'stdout': json.dumps({**status, 'netbirdIp': desired + '/16'})},
                            'netbird_ip': desired}
                run(True, assigned)
                self.assertEqual(len(state['peer_puts']), 1)

                # A stale/deleted peer can be assigned its desired IP again after enrollment.
                # A conflicting peer is never selected or overwritten instead.
                state['peers'].append({'id': 'another-peer', 'ip': server_ip})
                run(False, {**assigned, 'netbird_ip': server_ip})
                self.assertEqual(len(state['peer_puts']), 1)
                state['peers'].pop()
                state['reject_ip'] = True
                run(False, {**assigned, 'netbird_ip': server_ip})
                self.assertEqual(state['peers'][0]['ip'], desired)
                state['reject_ip'] = False
                run(True, {**assigned, 'netbird_ip': server_ip})

                state['peers'][0]['groups'].append({'id': 'prod', 'name': 'prod-servers'})
                self.assertIn('opposite environment', run(False, enrolled))
                self.assertEqual(len(state['group_puts']), 1)

                reset()
                run(False, fail_cli=True)
                self.assertEqual(state['deleted'], ['/api/setup-keys/generated-key'])
                record = json.loads(log.read_text())
                self.assertFalse(Path(record['path']).exists())
                self.assertEqual(state['peers'], [])
                run(True)  # Failed first enrollment can be retried with a new one-off key.
                self.assertEqual(len(state['key_posts']), 2)

                reset()
                state['revoked'] = True
                self.assertIn('Could not prepare server enrollment', run(False))
                self.assertEqual(state['key_posts'], [])
                state['revoked'] = False
                vault_file.unlink()
                run(False)
                self.assertEqual(state['key_posts'], [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
