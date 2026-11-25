"""Real-process lock contention, controller loss and writer-recovery admission."""

import importlib.util
import json
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from helpers import ROOT

sys.path.insert(0, str(ROOT / 'scripts'))
from lab_capture_guard import CaptureGuard
import lab_capture_guard

FILES = ROOT / 'ansible/playbooks/roles/backup_source/files'


class CaptureCoordinationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.lock = self.root / 'capture.lock'
        self.intent = self.root / 'restart-intent.json'
        program = (FILES / 'operation_lock.py').read_text().replace(
            '/run/lock/hou1-backup-export.lock', str(self.lock)).replace(
            '/var/lib/hou1-backup/restart-intent.json', str(self.intent))
        (self.root / 'operation_lock.py').write_text(program)
        (self.root / 'export.py').write_text((FILES / 'export.py').read_text().replace(
            '/var/lib/hou1-tls/activation.json', str(self.root / 'activation.json')))
        spec = importlib.util.spec_from_file_location('fixture_lock', self.root / 'operation_lock.py')
        self.operation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.operation)
        self.hold = [sys.executable, '-u', '-c',
                     f'import sys; sys.path.insert(0, {str(self.root)!r}); '
                     'import operation_lock; operation_lock.hold()']

    def guard(self, name='guard', timeout=3):
        return CaptureGuard(self.hold, self.root / (name + '.log'), timeout=timeout)

    def test_capture_refuses_pending_tls_before_snapshot_or_writer_mutation(self):
        spec = importlib.util.spec_from_file_location('tls_gated_capture', self.root / 'export.py')
        source = importlib.util.module_from_spec(spec); spec.loader.exec_module(source)
        config = self.root / 'source.json'
        config.write_text(json.dumps({'work_root': str(self.root)}))
        pending = self.root / 'activation.json'; pending.write_text('synthetic private intent')
        ready = self.root / 'ready.export'; ready.write_bytes(b'previous recovery point')
        with patch.object(source, 'CONFIG', config), patch.object(source, 'TLS_ACTIVATION', pending), \
                patch.object(source, 'recover') as recover, patch.object(source, 'prepare') as prepare:
            with self.assertRaisesRegex(RuntimeError, 'Pending TLS activation'):
                source.run()
            prepare.assert_not_called()
            self.assertEqual(ready.read_bytes(), b'previous recovery point')
            self.assertEqual(json.loads((self.root / 'last-export.json').read_text())['last_run_success'], 0)
            # Writer recovery must remain available to repair unrelated intent.
            source.run(recovery_only=True)
            self.assertEqual(recover.call_count, 2)
            prepare.assert_not_called()
            pending.unlink()
            prepare.return_value = {'created': 123, 'id': 'synthetic-point'}
            source.run()
            prepare.assert_called_once()

    def start(self, program):
        process = subprocess.Popen([sys.executable, '-u', '-c', program],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.addCleanup(cleanup)
        return process

    def line(self, process):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(5), 'Child did not reach its synchronization point')
        return process.stdout.readline()

    def backup(self, pause=False):
        return self.start(f'''
import fcntl, json, sys
from pathlib import Path
sys.path.insert(0, {str(self.root)!r})
import export
export.CONFIG = Path({str(self.root / 'config.json')!r})
original = fcntl.flock
def observed(*args):
    try: return original(*args)
    except BlockingIOError:
        print('blocked', flush=True)
        raise
fcntl.flock = observed
export.recover = lambda config: None
def prepare(config):
    Path({str(self.root / 'writer-stop')!r}).write_text(config['selected'])
    print('capturing', flush=True)
    if {pause!r}: sys.stdin.read(1)
    return {{'created': 1234}}
export.prepare = prepare
export.run()
''')

    def config(self):
        (self.root / 'config.json').write_text(json.dumps(
            {'work_root': str(self.root), 'selected': 'fresh configuration'}))

    def test_deployment_first_blocks_capture_before_configuration_read_or_writer_stop(self):
        # No config exists until after capture is blocked on the shared lock.
        with self.guard():
            backup = self.backup()
            self.assertEqual(self.line(backup), b'blocked\n')
            self.assertFalse((self.root / 'writer-stop').exists())
            self.assertFalse((self.root / 'last-export.json').exists())
            self.config()
        stdout, stderr = backup.communicate(timeout=5)
        self.assertEqual(backup.returncode, 0, stderr)
        self.assertIn(b'capturing', stdout)
        self.assertEqual((self.root / 'writer-stop').read_text(), 'fresh configuration')
        self.assertEqual(json.loads((self.root / 'last-export.json').read_text())['last_run_success'], 1)

    def test_capture_first_holds_deployment_until_capture_finishes(self):
        self.config()
        backup = self.backup(pause=True)
        self.assertEqual(self.line(backup), b'capturing\n')
        holder = self.start(self.hold[-1])
        with selectors.DefaultSelector() as selector:
            selector.register(holder.stdout, selectors.EVENT_READ)
            self.assertFalse(selector.select(0.2), 'Deployment entered during capture')
        backup.stdin.write(b'x'); backup.stdin.flush()
        self.assertEqual(backup.wait(timeout=5), 0)
        self.assertEqual(self.line(holder), b'ready\n')
        holder.stdin.close(); holder.wait(timeout=5)

    def test_admission_timeout_releases_waiter_and_preserves_previous_point(self):
        previous = self.root / 'ready.export'
        previous.write_bytes(b'previous verified point')
        with self.operation.exclusive():
            with self.assertRaises(ValueError):
                with self.guard(timeout=0.1):
                    self.fail('Contended guard admitted deployment')
        self.assertEqual(previous.read_bytes(), b'previous verified point')
        with self.guard('retry'):
            pass

    def test_capture_wait_is_bounded_and_keeps_previous_success(self):
        path = self.root / 'operation_lock.py'
        path.write_text(path.read_text().replace('WAIT_SECONDS = 900', 'WAIT_SECONDS = 0.05'))
        (self.root / 'ready.export').write_bytes(b'previous point')
        previous_status = '{"last_run_success":1,"last_success":1234}'
        (self.root / 'last-export.json').write_text(previous_status)
        with self.operation.exclusive():
            backup = self.backup()
            _, stderr = backup.communicate(timeout=5)
            self.assertNotEqual(backup.returncode, 0)
            self.assertIn(b'TimeoutError', stderr)
        self.assertFalse((self.root / 'writer-stop').exists())
        self.assertEqual((self.root / 'ready.export').read_bytes(), b'previous point')
        self.assertEqual((self.root / 'last-export.json').read_text(), previous_status)

    def test_recovery_intent_refuses_deployment_without_removing_intent(self):
        self.intent.write_text('{"schema":1,"component":"netbird"}')
        with self.assertRaises(ValueError):
            with self.guard():
                self.fail('Unrecovered writer admitted deployment')
        self.assertTrue(self.intent.exists())
        with self.operation.exclusive(timeout=0):
            pass

    def test_controller_interruption_releases_lock(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.guard():
                raise KeyboardInterrupt()
        with self.operation.exclusive(timeout=0):
            pass

    def test_controller_eof_cancels_admission_while_capture_still_holds_lock(self):
        with self.operation.exclusive():
            holder = self.start(self.hold[-1])
            holder.stdin.close()
            self.assertNotEqual(holder.wait(timeout=5), 0)
            self.assertEqual(holder.stdout.read(), b'')
        with self.operation.exclusive(timeout=0):
            pass

    def test_host_releases_abandoned_lease_even_when_stdin_remains_open(self):
        path = self.root / 'operation_lock.py'
        path.write_text(path.read_text().replace('HOLD_IDLE_SECONDS = 60', 'HOLD_IDLE_SECONDS = 0.3'))
        holder = self.start(self.hold[-1])
        self.assertEqual(self.line(holder), b'ready\n')
        self.assertNotEqual(holder.wait(timeout=5), 0)
        self.assertFalse(holder.stdin.closed)
        with self.operation.exclusive(timeout=0):
            pass

    def test_transport_retry_starts_a_fresh_keepalive(self):
        path = self.root / 'operation_lock.py'
        path.write_text(path.read_text().replace('HOLD_IDLE_SECONDS = 60', 'HOLD_IDLE_SECONDS = 1.0'))
        attempted = self.root / 'first-ssh-attempt'
        program = ("import sys\nfrom pathlib import Path\n"
                   f"attempted = Path({str(attempted)!r})\n"
                   "if not attempted.exists():\n    attempted.touch()\n    sys.exit(255)\n"
                   + self.hold[-1])
        with patch.object(lab_capture_guard, 'KEEPALIVE_SECONDS', 0.05):
            with CaptureGuard([sys.executable, '-u', '-c', program],
                              self.root / 'retry-admission.log', timeout=8) as guard:
                time.sleep(1.2)
                guard.check()
        self.assertTrue(attempted.exists())
        with self.operation.exclusive(timeout=0):
            pass

    def test_controller_keepalive_preserves_a_healthy_lease(self):
        path = self.root / 'operation_lock.py'
        path.write_text(path.read_text().replace('HOLD_IDLE_SECONDS = 60', 'HOLD_IDLE_SECONDS = 0.5'))
        with patch.object(lab_capture_guard, 'KEEPALIVE_SECONDS', 0.05):
            with self.guard() as guard:
                time.sleep(1)
                guard.check()
                with self.assertRaises(TimeoutError):
                    with self.operation.exclusive(timeout=0):
                        self.fail('Capture entered while healthy controller held admission')

    def test_lost_holder_stops_active_controller_work(self):
        marker = self.root / 'child-running'
        pid = self.root / 'child-pid'
        with self.guard() as guard:
            def disconnect():
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                guard.process.kill()
            worker = threading.Thread(target=disconnect)
            worker.start()
            try:
                with self.assertRaises(ValueError):
                    guard.run([sys.executable, '-c',
                        f'import os,time; from pathlib import Path; Path({str(pid)!r}).write_text(str(os.getpid())); '
                        f'Path({str(marker)!r}).touch(); time.sleep(30)'])
            finally:
                worker.join(timeout=5)
        self.assertTrue(marker.exists())
        with self.assertRaises(ProcessLookupError):
            __import__('os').kill(int(pid.read_text()), 0)
        with self.operation.exclusive(timeout=0):
            pass

    def test_disconnected_pipe_cleanup_preserves_the_original_failure(self):
        with self.assertRaisesRegex(ValueError, 'original guard failure'):
            with self.guard() as guard:
                guard.process.kill()
                guard.process.wait()
                guard.process.stdin.write(b'pending')
                raise ValueError('original guard failure')
        self.assertTrue(guard.process.stdin.closed)
        self.assertTrue(guard.process.stdout.closed)
        with self.operation.exclusive(timeout=0):
            pass

    def test_symlink_lock_is_rejected(self):
        target = self.root / 'target'; target.write_text('preserve')
        self.lock.symlink_to(target)
        with self.assertRaises(OSError):
            with self.operation.exclusive(timeout=0):
                pass
        self.assertEqual(target.read_text(), 'preserve')
