from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'scripts/oidc-integration-harness.py'


class OidcIntegrationHarnessTest(unittest.TestCase):
    def test_failure_matrix_covers_every_required_assertion(self):
        namespace = {}
        exec(HARNESS.read_text(), namespace)
        results = namespace['failure_matrix'](now=1_700_000_000)  # noqa: S102
        required = {'invalid_issuer', 'invalid_audience', 'invalid_signature', 'expired', 'not_before',
                    'state_replay', 'nonce_mismatch', 'pkce_failure', 'jwks_rotation', 'missing_groups',
                    'clock_skew', 'revocation'}
        self.assertEqual(set(results), required)
        self.assertTrue(all(value == 'passed' for value in results.values()))

    def test_offline_run_writes_isolated_evidence_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'evidence.json'
            env = {**os.environ, 'OIDC_TEST_ENVIRONMENT': 'test', 'OIDC_TEST_CLIENT_ID': 'netbird-test'}
            subprocess.run([sys.executable, str(HARNESS), '--issuer', 'https://auth-test.example/application/o/netbird-test/',
                            '--audience', 'netbird-test', '--netbird-url', 'https://netbird-test.example', '--report', str(report), '--offline'],
                           env=env, check=True)
            data = json.loads(report.read_text())
            self.assertEqual(data['environment'], 'test')
            self.assertEqual(data['live_checks']['status'], 'skipped')
            report_text = report.read_text()
            self.assertNotIn('NETBIRD_API_PAT', report_text)
            self.assertNotIn('client_secret', report_text)

    def test_harness_rejects_production_references(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'OIDC_TEST_ENVIRONMENT': 'test'}
            result = subprocess.run([sys.executable, str(HARNESS), '--issuer', 'https://auth.prod.example/application/o/netbird/',
                                     '--audience', 'netbird-prod', '--netbird-url', 'https://netbird.prod.example',
                                     '--report', str(Path(directory) / 'evidence.json'), '--offline'], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
