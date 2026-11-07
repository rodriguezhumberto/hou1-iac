"""Temporary public-key-only JWKS fixture; expose only on the test Docker network.

This models overlap which the installed Authentik does not publish natively.
It never handles tokens, authorization codes, cookies, or private key material.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

JWKS_PATH = '/application/o/pearland-paws-staff-test/jwks/'


def serve(root, address=('0.0.0.0', 9081)):
    root = Path(root)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/health':
                status, payload = 200, b'{}'
            elif self.path == JWKS_PATH:
                with lock:
                    fixture = json.loads((root / 'fixture.json').read_text())
                    status = fixture['status']
                    payload = json.dumps({'keys': fixture['keys']}).encode() if status == 200 else b'{}'
                    with (root / 'requests.jsonl').open('a') as stream:
                        stream.write(json.dumps({'time': time.time(), 'status': status,
                                                 'kids': [k['kid'] for k in fixture['keys']]}) + '\n')
            else:
                status, payload = 404, b'{}'
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

    return ThreadingHTTPServer(address, Handler)


if __name__ == '__main__':
    serve('/state').serve_forever()
