"""Exercise the real Ansible URI tasks against a disposable setup API fixture."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

import yaml

import test_vpn_migration as migration


class BootstrapTest(unittest.TestCase):
    def test_owner_creation_repeat_run_and_failure_handling(self):
        tasks = yaml.safe_load((migration.ROLES / 'netbird_control_plane/tasks/bootstrap.yml').read_text())
        first_api_task = next(i for i, task in enumerate(tasks)
                              if task['name'].startswith('Read the NetBird setup state'))
        # Skip Docker discovery only. The production role supplies the private URL.
        api_tasks = tasks[first_api_task:]
        state = {'setup_required': True, 'posts': [], 'reject': False}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'setup_required': state['setup_required']}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                state['posts'].append(body)
                self.send_response(500 if state['reject'] else 200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                if not state['reject']:
                    state['setup_required'] = False
                self.wfile.write(b'{"user_id":"test-owner"}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = {'netbird_bootstrap_api_url': 'http://127.0.0.1:' + str(server.server_port)}
            owner = {'netbird_owner_email': 'owner@example.org',
                     'netbird_owner_name': 'Test Owner',
                     'netbird_owner_password': 'offline-test-password-123'}
            scenarios = [
                # First creation; repeat run needs no credentials or POST.
                (True, owner, False, True, 1),
                (False, {}, False, True, 0),
                # Invalid/missing credentials must fail before any POST.
                (True, {}, False, False, 0),
                (True, {**owner, 'netbird_owner_password': 'short'}, False, False, 0),
                # Never treat a malformed state as permission to bootstrap.
                ('false', owner, False, False, 0),
                # Failure is not retried: re-query state on the next run instead.
                (True, owner, True, False, 1),
            ]
            for required, secrets, reject, valid, post_count in scenarios:
                with self.subTest(required=required, reject=reject, post_count=post_count):
                    state.update(setup_required=required, posts=[], reject=reject)
                    result = migration.MigrationTest.run_play(self, {**base, **secrets}, api_tasks)
                    self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
                    self.assertEqual(len(state['posts']), post_count)
                    self.assertNotIn(owner['netbird_owner_password'], result.stdout + result.stderr)
                    self.assertNotIn(owner['netbird_owner_email'], result.stdout + result.stderr)
                    if post_count:
                        self.assertEqual(state['posts'][0], {
                            'email': owner['netbird_owner_email'], 'name': owner['netbird_owner_name'],
                            'password': owner['netbird_owner_password'], 'create_pat': False})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
