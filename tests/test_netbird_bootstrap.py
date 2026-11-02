"""Exercise bootstrap, encrypted persistence and account updates through Ansible."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from ansible.parsing.vault import VaultLib, VaultSecret

from helpers import ROLES, run_play


class BootstrapTest(unittest.TestCase):
    def test_bootstrap_vault_and_account_lifecycle(self):
        tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'netbird_control_plane/tasks/bootstrap-owner.yml')}]
        token = 'fixture-private-pat'
        state = {}

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, body):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                if self.path == '/api/instance':
                    self.reply(200, {'setup_required': state['required']})
                    return
                state['auth'].append(self.headers.get('Authorization'))
                if state['revoked'] or self.headers.get('Authorization') != 'Token ' + token:
                    self.reply(401, {})
                elif self.path == '/api/accounts':
                    self.reply(200, [{'id': 'fixture-account', 'settings': state['settings']}])
                elif self.path == '/api/peers':
                    self.reply(200, state['peers'])
                else:
                    self.reply(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                state['posts'].append(body)
                if state['reject']:
                    self.reply(500, {})
                    return
                state['required'] = False
                if state.get('break_storage'):
                    # Simulate storage failure between preflight and saving the PAT.
                    state['vault_path'].parent.rmdir()
                    state['vault_path'].parent.write_text('storage unavailable')
                response = {'user_id': 'fixture-owner'}
                if not state.get('omit_token'):
                    response['personal_access_token'] = token
                self.reply(200, response)

            def do_PUT(self):
                if self.headers.get('Authorization') != 'Token ' + token:
                    self.reply(401, {})
                    return
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                state['puts'].append(body)
                state['settings'] = body['settings']
                self.reply(200, {'id': 'fixture-account', 'settings': state['settings']})

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                password = root / 'password'
                password.write_text('fixture-vault-password')
                password.chmod(0o600)
                path = root / 'vault' / 'token.yml'
                owner = {'netbird_owner_email': 'owner@example.org',
                         'netbird_owner_name': 'Test Owner',
                         'netbird_owner_password': 'fixture-owner-password'}
                base = {'netbird_bootstrap_api_url': f'http://127.0.0.1:{server.server_port}',
                        'netbird_management_url': 'https://netbird.example.org',
                        'netbird_token_vault_file': str(path)}
                vault = VaultLib([('default', VaultSecret(password.read_bytes()))])

                def reset(**overrides):
                    state.clear()
                    state.update(required=True, posts=[], puts=[], auth=[], peers=[],
                                 settings={'network_range': '100.80.0.0/16',
                                           'peer_login_expiration': 86400,
                                           'peer_login_expiration_enabled': True},
                                 reject=False, revoked=False, vault_path=path)
                    state.update(overrides)

                def run(valid, variables=None, credential=password):
                    args = ['--diff', '-v']
                    if credential is not None:
                        args += ['--vault-password-file', str(credential)]
                    result = run_play({**base, **(variables or {})}, tasks, extra_args=args)
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode == 0, valid, output)
                    for secret in [token, owner['netbird_owner_email'], owner['netbird_owner_password'],
                                   password.read_text()]:
                        self.assertNotIn(secret, output)
                    return output

                reset()
                # Preflight fails before owner creation without an encryption credential.
                run(False, owner, credential=None)
                self.assertEqual(state['posts'], [])
                self.assertFalse(path.exists())
                for variables, required in [({}, True), ({**owner, 'netbird_owner_password': 'short'}, True),
                                             (owner, 'false')]:
                    reset(required=required)
                    run(False, variables)
                    self.assertEqual(state['posts'], [])
                reset(reject=True)
                run(False, owner)
                self.assertEqual(len(state['posts']), 1)  # Never blindly retry setup.
                self.assertFalse(path.exists())

                reset()
                run(True, owner)
                self.assertEqual(state['posts'], [{
                    'email': owner['netbird_owner_email'], 'name': owner['netbird_owner_name'],
                    'password': owner['netbird_owner_password'], 'create_pat': True, 'pat_expire_in': 365}])
                encrypted = path.read_bytes()
                self.assertTrue(encrypted.startswith(b'$ANSIBLE_VAULT;'))
                self.assertNotIn(token.encode(), encrypted)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(vault.decrypt(encrypted)), {
                    'netbird_management_url': base['netbird_management_url'], 'netbird_api_token': token})
                self.assertEqual(state['settings']['network_range'], '100.90.0.0/16')
                self.assertTrue(state['settings']['peer_login_expiration_enabled'])
                self.assertEqual(state['settings']['peer_login_expiration'], 86400)
                self.assertEqual(len(state['puts']), 1)
                self.assertEqual(list(path.parent.iterdir()), [path])

                run(True)  # No owner credentials required; encrypted file is unchanged.
                self.assertEqual(path.read_bytes(), encrypted)
                self.assertEqual(len(state['posts']), 1)
                self.assertEqual(len(state['puts']), 1)
                self.assertTrue(all(auth == 'Token ' + token for auth in state['auth']))

                state['settings']['network_range'] = '100.80.0.0/16'
                state['peers'] = [{'id': 'enrolled-peer'}]
                self.assertIn('will not renumber', run(False))
                self.assertEqual(len(state['puts']), 1)
                state['revoked'] = True
                self.assertIn('expired or revoked', run(False))
                self.assertEqual(path.read_bytes(), encrypted)
                self.assertEqual(len(state['posts']), 1)

                wrong_password = root / 'wrong-password'
                wrong_password.write_text('wrong')
                run(False, credential=wrong_password)
                run(False, {'netbird_management_url': 'https://another.example.org'})
                path.write_text('not an encrypted vault')
                run(False)
                self.assertEqual(len(state['posts']), 1)
                path.unlink()
                self.assertIn('API token Vault is missing', run(False))
                self.assertEqual(len(state['posts']), 1)

                reset(omit_token=True)
                run(False, owner)
                self.assertFalse(path.exists())
                run(False)
                self.assertEqual(len(state['posts']), 1)

                reset(break_storage=True)
                run(False, owner)
                self.assertFalse(path.exists())
                path.parent.unlink()
                run(False)
                self.assertEqual(len(state['posts']), 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
