"""Interrupted offline verification contains plaintext and terminates decryptors."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import ROOT

sys.path.insert(0, str(ROOT / 'scripts'))
from lab_backup_client import run_verifier


class VerificationCleanupTest(unittest.TestCase):
    def test_killed_verifier_leaves_plaintext_only_inside_owned_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'fixture.age'; archive.write_bytes(b'synthetic ciphertext')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(dict(schema=1, environment='test', size=archive.stat().st_size,
                                               sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
            # This synthetic decryptor emits no operational recovery material.
            age = root / 'age'
            age.write_text('#!' + sys.executable + '\nimport sys,time\n'
                           'sys.stdout.buffer.write(b"synthetic plaintext"); sys.stdout.flush(); time.sleep(30)\n')
            age.chmod(0o700)
            output = root / 'verified-plaintext'
            command = [sys.executable, str(ROOT / 'scripts/verify-recovery.py'),
                       '--archive', str(archive), '--manifest', str(manifest),
                       '--identity', str(root / 'unused'), '--output', str(output), '--environment', 'test']
            process = subprocess.Popen(command, env={**os.environ, 'PATH': str(root)+os.pathsep+os.environ['PATH']},
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    files = list(output.glob('.verify-*/recovery.tar.gz'))
                    if files and files[0].stat().st_size:
                        break
                    time.sleep(0.02)
                else:
                    self.fail('synthetic decryptor did not write staging plaintext')
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
                self.assertFalse(list(root.glob('.verify-*')))
                self.assertEqual(files[0].read_bytes(), b'synthetic plaintext')
                shutil.rmtree(output)
                self.assertFalse(output.exists())
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)

    def test_timeout_terminates_decryptor_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            child_pid = Path(folder) / 'child.pid'
            child = ('import os,time; from pathlib import Path; '
                     f'Path({str(child_pid)!r}).write_text(str(os.getpid())); time.sleep(30)')
            parent = ('import subprocess,sys,time; '
                      f'subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(30)')
            with self.assertRaises(subprocess.TimeoutExpired):
                run_verifier([sys.executable, '-c', parent], timeout=1)
            self.assertTrue(child_pid.exists())
            status = Path('/proc') / child_pid.read_text() / 'stat'
            def stopped():
                try:
                    return status.read_text().split()[2] == 'Z'
                except FileNotFoundError:
                    return True
            deadline = time.monotonic() + 5
            while not stopped() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(stopped())
