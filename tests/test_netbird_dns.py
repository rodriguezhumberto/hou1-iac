"""Private DNS discovery, API lifecycle, and real resolver behavior."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import grp
import json
import os
from pathlib import Path
import pwd
import runpy
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest

from ansible.errors import AnsibleFilterError
from jinja2 import Template

from helpers import ROLES, ROOT, run_play

plan = runpy.run_path(str(ROOT / 'ansible/playbooks/filter_plugins/netbird_dns.py'))['dns_plan']


class DnsTest(unittest.TestCase):
    def setUp(self):
        self.groups = [dict(id=n, name=n) for n in
                       ('test-servers', 'prod-servers', 'administrators', 'staff')]
        self.peers = [dict(id='test', ip='100.90.218.114', groups=[self.groups[0]]),
                      dict(id='prod', ip='100.90.0.1', groups=[self.groups[1]])]
        self.records = [dict(name=n + '.example.org', peer_group='test-servers')
                        for n in ('test', 'staff-test', 'cms-test')]

    def make_plan(self, existing=()):
        return plan(self.peers, self.groups, self.records, '100.90.0.1',
                    'example.org', '100.90.0.0/16', 1053, existing)

    def test_discovery_and_failures(self):
        result = self.make_plan()
        self.assertEqual({r['address'] for r in result['records']}, {'100.90.218.114'})
        self.assertEqual(result['body']['groups'], ['administrators', 'staff'])
        self.assertFalse(result['body']['primary'])
        self.assertEqual(result['body']['nameservers'][0]['port'], 1053)
        self.assertTrue(result['body']['search_domains_enabled'])
        saved = dict(id='dns', **result['body'])
        saved['groups'].reverse()
        self.assertFalse(self.make_plan([saved])['changed'])
        self.peers[0]['ip'] = '100.90.218.115'
        self.assertEqual(self.make_plan([saved])['records'][0]['address'], '100.90.218.115')
        cases = [lambda: self.peers.append(deepcopy(self.peers[0])),
                 lambda: self.peers.pop(0),
                 lambda: self.peers[0].update(ip='192.168.0.1'),
                 lambda: self.records[0].update(name='unrelated.net'),
                 lambda: self.records[0].update(name='bad\nserver.example.org'),
                 lambda: self.records.append(deepcopy(self.records[0]))]
        for mutate in cases:
            self.setUp()
            mutate()
            with self.assertRaises((AnsibleFilterError, ValueError)):
                self.make_plan()
        self.setUp()
        for existing in [[saved, saved], [dict(saved, description='Operator managed')],
                         [dict(saved, name='other', domains=['test.example.org'])]]:
            with self.assertRaises(AnsibleFilterError):
                self.make_plan(existing)

    def test_api_create_idempotence_update_and_readback(self):
        state = dict(nameservers=[], posts=0, puts=0, corrupt=False)
        peers = self.peers

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                if self.headers.get('Authorization') != 'Token fixture-token':
                    self.send_error(401)
                    return
                if self.path == '/api/peers' and self.command == 'GET':
                    value = peers
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(value).encode())
                    return
                if not self.path.startswith('/api/dns/nameservers'):
                    self.send_error(404)
                    return
                if self.command != 'GET':
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.command == 'POST':
                        state['posts'] += 1
                    else:
                        state['puts'] += 1
                    state['nameservers'] = [dict(id='dns', **body)]
                value = deepcopy(state['nameservers'])
                if value and state['corrupt']:
                    value[0]['primary'] = True
                if self.command != 'GET':
                    value = value[0]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            do_GET = do_POST = do_PUT = handle_request

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'netbird_dns/tasks' / name)}
                     for name in ('discover.yml', 'publish.yml')]
            variables = dict(netbird_management_url=f'http://127.0.0.1:{server.server_port}',
                             netbird_client_token=dict(token='fixture-token'),
                             netbird_dns_peers=dict(json=self.peers),
                    netbird_client_groups=dict(json=self.groups), netbird_dns_port=1053,
                             netbird_server_peer=self.peers[1], netbird_dns_domain='example.org',
                             netbird_dns_records=self.records)

            def run(valid=True):
                result = run_play(variables, tasks, extra_args=['--diff', '-v'])
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
                self.assertNotIn('fixture-token', result.stdout + result.stderr)
                self.assertNotIn('staff-test.example.org', result.stdout + result.stderr)
                return result

            run()
            self.assertEqual((state['posts'], state['puts']), (1, 0))
            result = run()
            self.assertIn('changed=0', result.stdout)
            self.assertEqual((state['posts'], state['puts']), (1, 0))
            state['nameservers'][0]['enabled'] = False
            state['nameservers'][0]['nameservers'][0]['port'] = 53
            run()
            self.assertEqual((state['posts'], state['puts']), (1, 1))
            self.assertEqual(state['nameservers'][0]['nameservers'][0]['port'], 1053)
            state['corrupt'] = True
            run(False)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_real_resolver_private_records_and_public_forwarding(self):
        binary = os.environ.get('DNSMASQ_BIN') or shutil.which('dnsmasq')
        if not binary:
            self.skipTest('Set DNSMASQ_BIN or install dnsmasq-base for resolver integration checks.')

        def query(name, qtype=1):
            labels = b''.join(bytes([len(s)]) + s.encode() for s in name.split('.')) + b'\0'
            return struct.pack('!6H', 1234, 0x100, 1, 0, 0, 0) + labels + struct.pack('!2H', qtype, 1)

        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        upstream.bind(('127.0.0.1', 0))
        upstream.settimeout(0.2)
        stopped = threading.Event()

        def forward():
            while not stopped.is_set():
                try:
                    data, remote = upstream.recvfrom(4096)
                except socket.timeout:
                    continue
                question_end = data.index(b'\0', 12) + 5
                response = data[:2] + struct.pack('!5H', 0x8180, 1, 1, 0, 0) + data[12:question_end]
                response += b'\xc0\x0c' + struct.pack('!2HIH', 1, 1, 60, 4) + socket.inet_aton('192.0.2.10')
                upstream.sendto(response, remote)

        worker = threading.Thread(target=forward, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with socket.socket() as reserve:
                    reserve.bind(('127.0.0.1', 0))
                    port = reserve.getsockname()[1]
                template = (ROLES / 'netbird_dns/templates/dnsmasq.conf.j2').read_text()
                config = Template(template).render(
                    netbird_server_peer=dict(ip='127.0.0.1'), netbird_dns_port=1053,
                    netbird_dns_plan=self.make_plan(),
                    netbird_dns_upstreams=[f'127.0.0.1#{upstream.getsockname()[1]}'])
                config = config.replace('port=1053', f'port={port}')
                config = config.replace('user=netbird-dns', 'user=' + pwd.getpwuid(os.getuid()).pw_name)
                config = config.replace('group=netbird-dns', 'group=' + grp.getgrgid(os.getgid()).gr_name)
                path = root / 'dnsmasq.conf'
                path.write_text(config)
                checked = subprocess.run([binary, '--test', '--conf-file=' + str(path)], capture_output=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                process = subprocess.Popen([binary, '--keep-in-foreground', '--conf-file=' + str(path)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    ready = threading.Event()
                    for _ in range(50):
                        if process.poll() is not None:
                            self.fail(process.stderr.read().decode())
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                                break
                        except OSError:
                            ready.wait(0.05)
                    else:
                        self.fail('Resolver did not start')
                    for name, qtype, expected in [('test.example.org', 1, '100.90.218.114'),
                                                  ('staff-test.example.org', 1, '100.90.218.114'),
                                                  ('cms-test.example.org', 1, '100.90.218.114'),
                                                  ('test.example.org', 28, None),
                                                  ('public.example.org', 1, '192.0.2.10')]:
                        for protocol in ('udp', 'tcp'):
                            data = query(name, qtype)
                            with socket.socket(type=socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM) as sock:
                                sock.settimeout(2)
                                sock.connect(('127.0.0.1', port))
                                if protocol == 'udp':
                                    sock.send(data)
                                    response = sock.recv(4096)
                                else:
                                    sock.sendall(struct.pack('!H', len(data)) + data)
                                    length = struct.unpack('!H', sock.recv(2))[0]
                                    response = b''
                                    while len(response) < length:
                                        response += sock.recv(length - len(response))
                            self.assertEqual(response[3] & 15, 0)
                            if expected:
                                self.assertIn(socket.inet_aton(expected), response)
                            else:
                                self.assertEqual(struct.unpack('!H', response[6:8])[0], 0)
                finally:
                    process.terminate()
                    process.communicate(timeout=5)
        finally:
            stopped.set()
            worker.join()
            upstream.close()
