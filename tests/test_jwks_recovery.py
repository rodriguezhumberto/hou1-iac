"""Recovery must not depend on deleted RPCs or skip ingress on an IdP failure."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('recovery', ROOT / 'scripts/live_oidc/jwks_recovery.py')
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class JwksRecoveryTests(unittest.TestCase):
    def invoke(self, fail=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'restore.json').write_text(json.dumps({'original_key': 'synthetic-key'}))
            (root / 'ingress.original').write_text('original config')
            ingress = root / 'ingress.conf'
            ingress.write_text('temporary fixture')
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                if fail == 'timeout' and '-i' in command:
                    raise subprocess.TimeoutExpired(command, 40)
                bad = fail == 'idp' and '-i' in command or fail == 'nginx' and command[-1] == '-t'
                return subprocess.CompletedProcess(command, int(bool(bad)), stdout='', stderr='')

            with patch.object(recovery.subprocess, 'run', side_effect=run):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                        recovery.recover(root, ingress)
                else:
                    recovery.recover(root, ingress)
            self.assertEqual(ingress.read_text(), 'original config')
            self.assertEqual(calls[-1], ['docker', 'rm', '-f', 'hou1-jwks-security-proxy'])
            return calls

    def test_recovery_is_container_local_and_restores_ingress(self):
        calls = self.invoke()
        self.assertIn('AUTHENTIK_BOOTSTRAP_TOKEN', calls[0][-1])
        self.assertNotIn('/root/hou1-test-auth-api.py', calls[0][-1])
        self.assertTrue(any(command[-2:] == ['-s', 'reload'] for command in calls))

    def test_idp_failure_or_timeout_does_not_skip_ingress_and_proxy_cleanup(self):
        for failure in ('idp', 'timeout'):
            calls = self.invoke(failure)
            self.assertTrue(any(command[-2:] == ['-s', 'reload'] for command in calls))

    def test_invalid_nginx_does_not_reload(self):
        calls = self.invoke('nginx')
        self.assertFalse(any(command[-2:] == ['-s', 'reload'] for command in calls))


if __name__ == '__main__':
    unittest.main()
