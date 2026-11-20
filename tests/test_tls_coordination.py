"""Renewal waits for managed deployment/capture without locking Ansible twice."""
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpers import ROOT, ROLES

sys.path.insert(0, str(ROOT / 'scripts'))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller = load(ROOT / 'scripts/controller-tls.py', 'coordinated_tls_controller')
receiver = load(ROLES / 'controller_tls/files/receive.py', 'coordinated_tls_receiver')


class TlsCoordinationTest(unittest.TestCase):
    def test_real_process_waits_on_the_same_controller_and_capture_inodes(self):
        for kind in ['controller', 'receiver']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                lock = root / 'operation.lock'
                lock.touch(mode=0o600)
                marker = root / 'entered'
                source = ROOT / 'scripts/controller-tls.py' if kind == 'controller' else ROLES / 'controller_tls/files/receive.py'
                setup = ('scope=m.deployment_admission(' + repr({'coordination_lock': str(lock)}) + ',timeout=5)'
                         if kind == 'controller' else
                         'm.CAPTURE_LOCK=Path(' + repr(str(lock)) + ');m.WRITER_INTENT=Path(' + repr(str(root / 'intent')) + ');scope=m.renewal_admission(timeout=5)')
                program = ('import importlib.util,sys\nfrom pathlib import Path\n'
                           + 'sys.path.insert(0,' + repr(str(ROOT / 'scripts')) + ')\n'
                           + 's=importlib.util.spec_from_file_location("fixture",' + repr(str(source)) + ');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
                           + setup + '\nprint("waiting",flush=True)\nwith scope:Path(' + repr(str(marker)) + ').write_text("entered")\n')
                inode = lock.stat().st_ino
                with lock.open('r+') as held:
                    fcntl.flock(held, fcntl.LOCK_EX)
                    child = subprocess.Popen([sys.executable, '-u', '-c', program],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    try:
                        self.assertEqual(child.stdout.readline().strip(), 'waiting')
                        time.sleep(0.2)
                        self.assertFalse(marker.exists())
                        fcntl.flock(held, fcntl.LOCK_UN)
                        out, error = child.communicate(timeout=5)
                        self.assertEqual(child.returncode, 0, out + error)
                    finally:
                        if child.poll() is None:
                            child.kill()
                            child.communicate()
                self.assertTrue(marker.exists())
                self.assertEqual(lock.stat().st_ino, inode)

    def test_gateway_waits_but_admitted_ansible_import_does_not_relock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / 'capture.lock'
            admission = receiver.renewal_admission
            with patch.object(receiver, 'STATE', root), patch.object(receiver, 'CAPTURE_LOCK', lock), \
                    patch.object(receiver, 'WRITER_INTENT', root / 'intent'), \
                    patch.object(receiver, 'read_config', return_value={}), \
                    patch.object(receiver, 'install', return_value={'passed': True}) as install:
                with lock.open('w') as held:
                    fcntl.flock(held, fcntl.LOCK_EX)
                    with patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))), \
                            patch.object(sys, 'argv', ['receiver']), patch.object(sys, 'stdout', io.StringIO()):
                        receiver.main()
                    install.assert_called_once()
                    install.reset_mock()
                    with patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))), \
                            patch.object(sys, 'argv', ['receiver', '--renew']), \
                            patch.object(receiver, 'renewal_admission', side_effect=lambda: admission(timeout=0)):
                        with self.assertRaises(TimeoutError):
                            receiver.main()
                    install.assert_not_called()

    def test_receiver_rejects_interrupted_writers_and_symlinked_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(receiver, 'CAPTURE_LOCK', root / 'capture.lock'), \
                    patch.object(receiver, 'WRITER_INTENT', root / 'intent'):
                (root / 'intent').touch()
                with self.assertRaises(RuntimeError), receiver.renewal_admission():
                    self.fail('Interrupted writers admitted')
                (root / 'capture.lock').unlink()
                (root / 'capture.lock').symlink_to(root / 'intent')
                with self.assertRaises(OSError), receiver.renewal_admission():
                    self.fail('Symlink admitted')

    def test_controller_opt_out_and_timeout_leave_admission_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'workflow.lock'
            lock.touch(mode=0o600)
            config = {'coordination_lock': str(lock)}
            with lock.open('r+') as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                with self.assertRaises(TimeoutError), controller.deployment_admission(config, timeout=0):
                    self.fail('Controller contention admitted')
                with controller.deployment_admission(config, enabled=False):
                    pass
                with controller.deployment_admission({}):
                    pass

    def test_deployment_ssh_budget_allows_bounded_capture_wait(self):
        config = {'ssh_config': '/private/ssh', 'ssh_host': 'renewal'}
        for action, timeout in [('deploy-v1', 1200), ('status-v1', 60)]:
            with patch.object(controller, 'run', return_value=b'{}') as run:
                self.assertEqual(controller.ssh(config, action, {}), {})
                self.assertEqual(run.call_args.kwargs['timeout'], timeout)
                self.assertEqual(json.loads(run.call_args.kwargs['input']), {})
