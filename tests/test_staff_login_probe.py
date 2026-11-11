"""Exercise a real TLS/cookie/redirect flow without live users or application data."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('staff_probe', ROOT / 'scripts/staff-login-probe.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)


def config(environment='production'):
    return dict(environment=environment, auth_origin='https://identity.example.invalid',
                staff_origin='https://staff.example.invalid', issuer='https://identity.example.invalid/application/o/reader/',
                client_id='reader-client', flow='default-authentication-flow', session_cookie='staff_session',
                netbird_subnet='100.90.0.0/16', server_ip='100.90.0.10')


def secret(environment='production'):
    return dict(environment=environment, kind='synthetic-reader', username='monitor-reader', password='synthetic-fixture-password-not-a-real-secret')


@contextmanager
def server(mode=None):
    settings = config(); calls = []; pins = []
    state = dict(authenticated=False, active=False)
    authorization = settings['auth_origin'] + '/application/o/authorize/?' + urlencode(dict(
        client_id=settings['client_id'], response_type='code', redirect_uri=settings['staff_origin'] + probe.CALLBACK,
        code_challenge_method='plain' if mode == 'pkce' else 'S256', code_challenge='a' * 43,
        state='synthetic-state', nonce='synthetic-nonce'))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, status=200, value=None, location=None, cookie=None):
            data = json.dumps(value or {}).encode()
            self.send_response(status)
            if location: self.send_header('Location', location)
            if cookie: self.send_header('Set-Cookie', cookie + '; Secure; HttpOnly; Path=/')
            self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            calls.append((self.command, self.headers['Host'], self.path, dict(self.headers)))
            path = urlsplit(self.path).path
            if path.endswith('/.well-known/openid-configuration'):
                self.respond(value={'issuer': 'https://wrong.example.invalid/' if mode == 'issuer' else settings['issuer'],
                                    'authorization_endpoint': settings['auth_origin'] + '/application/o/authorize/'})
            elif path == '/api/staff/login':
                self.respond(302, location=authorization)
            elif path == '/application/o/authorize/':
                if state['authenticated']:
                    self.respond(302, location=settings['staff_origin'] + probe.CALLBACK + '?code=synthetic-code&state=synthetic-state')
                else:
                    self.respond(302, location=settings['auth_origin'] + '/if/flow/default-authentication-flow/?next=synthetic')
            elif path == '/if/flow/default-authentication-flow/':
                self.respond(cookie='authentik_csrf=synthetic-csrf')
            elif path.startswith('/api/v3/flows/executor/'):
                self.respond(value={'component': 'ak-stage-identification'})
            elif path == probe.CALLBACK:
                state['active'] = True
                self.respond(302, location=settings['staff_origin'] + '/', cookie='staff_session=synthetic-session')
            elif path == probe.READ:
                if state['active'] and 'staff_session=synthetic-session' in self.headers.get('Cookie', ''):
                    self.respond(value={'data': []})
                elif mode == 'revocation-redirect': self.respond(302, location=settings['staff_origin'] + '/login')
                else: self.respond(401)
            elif path == '/api/staff/logout':
                if mode != 'revocation': state['active'] = False
                self.respond()
            elif path in ('/', '/login'): self.respond()
            else: self.respond(404)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.command, self.headers['Host'], self.path, dict(self.headers)))
            if (self.headers.get('Origin') != settings['auth_origin']
                    or self.headers.get('X-CSRFToken') != 'synthetic-csrf' or data.get('password') != secret()['password']):
                self.respond(403); return
            state['authenticated'] = True
            destination = 'https://unexpected.example.invalid/steal?code=synthetic' if mode == 'external' else authorization
            self.respond(value={'component': 'xak-flow-redirect', 'to': destination})
        def do_PATCH(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.command, self.headers['Host'], self.path, dict(self.headers)))
            if data or urlsplit(self.path).query: self.respond(500); return
            if self.headers.get('Origin') != settings['staff_origin']: self.respond(418); return
            self.respond(400 if mode == 'role' else 403)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); cert = root / 'cert.pem'; key = root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=fixture',
                        '-addext', 'subjectAltName=DNS:identity.example.invalid,DNS:staff.example.invalid',
                        '-out', str(cert), '-keyout', str(key)], capture_output=True, check=True)
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
        http.socket = context.wrap_socket(http.socket, server_side=True)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        create = socket.create_connection
        def connect(address, timeout, source_address=None):
            pins.append(address)
            if address != (settings['server_ip'], 443): raise ValueError('Unexpected socket destination')
            return create(('127.0.0.1', http.server_port), timeout, source_address)
        try:
            with patch.object(probe.socket, 'create_connection', side_effect=connect):
                yield settings, calls, pins, ssl.create_default_context(cafile=str(cert))
        finally: http.shutdown(); http.server_close(); thread.join()


class StaffLoginProbeTest(unittest.TestCase):
    def test_real_tls_flow_pkce_cookie_roles_and_revocation_over_only_the_vpn_pin(self):
        with server() as (settings, calls, pins, context):
            self.assertTrue(probe.probe(settings, secret(), context))
            self.assertGreater(len(pins), 5)
            self.assertEqual(set(pins), {('100.90.0.10', 443)})
            self.assertEqual([call[:3] for call in calls if call[0] == 'PATCH'],
                             [('PATCH', 'staff.example.invalid', probe.READ)])
            callback = next(call for call in calls if call[2].startswith(probe.CALLBACK))
            self.assertNotIn('X-CSRFToken', callback[3])
            self.assertNotIn('Origin', callback[3])
    def test_pkce_external_redirect_excess_role_and_logout_regressions_fail(self):
        for mode in ('pkce', 'external', 'role', 'revocation', 'revocation-redirect', 'issuer'):
            with self.subTest(mode=mode), server(mode) as (settings, calls, pins, context):
                with self.assertRaises(Exception): probe.probe(settings, secret(), context)
                self.assertTrue(all(host in ('identity.example.invalid', 'staff.example.invalid') for _, host, _, _ in calls))
                if mode == 'pkce': self.assertEqual(len(calls), 2)
                if mode == 'issuer': self.assertEqual(len(calls), 1)
    def test_default_trust_rejects_the_fixture_certificate(self):
        with server() as (settings, calls, pins, context):
            with self.assertRaises(Exception): probe.probe(settings, secret())
            self.assertEqual(calls, [])
    def test_environment_and_destination_mismatches_fail_before_connecting(self):
        for key, value in [('environment', 'prod'), ('auth_origin', 'http://identity.example.invalid'),
                           ('staff_origin', 'https://user:password@staff.example.invalid'),
                           ('server_ip', '198.51.100.10'), ('issuer', 'https://elsewhere.example.invalid/application/o/client/')]:
            with self.subTest(key=key), patch.object(probe.socket, 'create_connection') as connect:
                with self.assertRaises(Exception): probe.probe({**config(), key: value}, secret())
                connect.assert_not_called()
        with patch.object(probe.socket, 'create_connection') as connect:
            with self.assertRaises(ValueError): probe.probe(config(), secret('test'))
            connect.assert_not_called()
    def test_numeric_failure_freshness_and_recovery_metrics_exclude_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe.publish(root, 'production', True, 100)
            for now in (101, 102, 103): probe.publish(root, 'production', False, now)
            text = (root / 'staff-login.prom').read_text()
            self.assertIn('consecutive_failures{probe_environment="production"} 3', text)
            self.assertIn('last_success_timestamp_seconds{probe_environment="production"} 100', text)
            self.assertNotIn(secret()['password'], text)
            probe.publish(root, 'production', True, 104)
            self.assertEqual(json.loads((root / 'state.json').read_text())['failures'], 0)
            (root / 'state.json').write_text('{"last_success":"invalid"}')
            probe.publish(root, 'production', False, 105)
            self.assertNotIn('invalid', (root / 'staff-login.prom').read_text())


if __name__ == '__main__': unittest.main()
