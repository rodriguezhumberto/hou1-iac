"""The live JWKS fixture serves only public-key responses at its exact test URL."""

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

SPEC = importlib.util.spec_from_file_location('jwks_proxy', Path(__file__).resolve().parents[1] / 'scripts/live_oidc/jwks_proxy.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class JwksFixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.keys = [{'kid': 'old', 'kty': 'RSA', 'n': 'public-modulus', 'e': 'AQAB'},
                     {'kid': 'new', 'kty': 'RSA', 'n': 'other-public-modulus', 'e': 'AQAB'}]
        self.publish(200)
        self.server = m.serve(self.root, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def publish(self, status):
        (self.root / 'fixture.json').write_text(json.dumps({'status': status, 'keys': self.keys}))

    def test_overlap_retirement_and_outage_have_observable_public_only_receipts(self):
        with urllib.request.urlopen(self.base + m.JWKS_PATH) as response:
            self.assertEqual(json.load(response)['keys'], self.keys)
        self.keys = self.keys[1:]
        self.publish(200)
        with urllib.request.urlopen(self.base + m.JWKS_PATH) as response:
            self.assertEqual(json.load(response)['keys'], self.keys)
        self.publish(503)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + m.JWKS_PATH)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(json.load(caught.exception), {})
        receipts = [json.loads(line) for line in (self.root / 'requests.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in receipts], [200, 200, 503])
        self.assertEqual([r['kids'] for r in receipts], [['old', 'new'], ['new'], ['new']])
        self.assertTrue(all(set(r) == {'time', 'status', 'kids'} for r in receipts))

    def test_other_clients_token_endpoints_and_mutations_are_not_served(self):
        for path, method in [('/application/o/netbird-test/jwks/', 'GET'),
                             ('/application/o/token/', 'POST'), (m.JWKS_PATH, 'POST')]:
            with self.subTest(path=path, method=method), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(self.base + path, method=method))
            self.assertIn(caught.exception.code, (404, 501))
        self.assertFalse((self.root / 'requests.jsonl').exists())
