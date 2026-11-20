"""Backup drill admission, target safety and independent retained-file checks."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT

sys.path.insert(0, str(ROOT / 'scripts'))
import lab_backup_matrix as controller
import lab_backup_runtime as runtime


class BackupRuntimeTest(unittest.TestCase):
    def point(self, root, name='20261119T040000Z-abcdef123456'):
        path = root / name; path.mkdir()
        data = b'age-encryption.org/v1\nfixture ciphertext'
        checksum = hashlib.sha256(data).hexdigest()
        manifest = dict(schema=1, environment='test', id=name, created=1795060800,
                        size=len(data), sha256=checksum)
        (path / 'recovery.age').write_bytes(data)
        (path / 'manifest.json').write_text(json.dumps(manifest))
        (path / 'receipt.json').write_text(json.dumps(dict(schema=1, sha256=checksum)))
        return path, manifest

    def test_retained_hashes_detect_corruption_and_never_count_private_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path, manifest = self.point(root)
            partial = root / '.incoming-fixture'; partial.mkdir()
            (partial / 'recovery.age').write_bytes(b'partial')
            self.assertEqual(set(runtime.completed(root)), {manifest['id']})
            (path / 'recovery.age').write_bytes(b'x' * manifest['size'])
            with self.assertRaisesRegex(ValueError, 'ciphertext'):
                runtime.completed(root)

    def test_partial_failure_keeps_prior_bytes_and_cannot_publish_a_new_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path, manifest = self.point(root)
            prior = runtime.completed(root)
            current = {**manifest, 'id': '20261119T050000Z-123456abcdef'}
            runtime.preserved_prior(root, prior, current)
            (root / current['id']).mkdir()
            with self.assertRaisesRegex(ValueError, 'partial point was published'):
                runtime.preserved_prior(root, prior, current)
            (root / current['id']).rmdir()
            (path / 'receipt.json').write_text('changed metadata')
            with self.assertRaisesRegex(ValueError, 'previous recovery point changed'):
                runtime.preserved_prior(root, prior, current)

    def test_production_and_wrong_host_or_overlay_stop_before_host_commands(self):
        base = dict(environment='test', role='hou4', hostname='fixture', address='100.91.0.21',
                    interface='wt0', source_ip='100.91.0.10', operation='interrupt')
        for change in ({'environment': 'production'}, {'hostname': 'other'}, {'address': '100.90.0.21'}):
            with self.subTest(change=change), patch.object(runtime.platform, 'node', return_value='fixture'), \
                    patch.object(runtime, 'output') as commands:
                with self.assertRaises(ValueError):
                    runtime.run({**base, **change})
                commands.assert_not_called()

    def test_unbounded_or_active_receiver_is_rejected_before_start(self):
        base = {'points': {'previous': {}}, 'partials': [],
                'service': {'ActiveState': 'inactive', 'RuntimeMaxUSec': 'infinity'}}
        for service in (base['service'], {**base['service'], 'ActiveState': 'active'}):
            with self.subTest(service=service), patch.object(runtime, 'receiver_config', return_value={'repository': '/fixture'}), \
                    patch.object(runtime, 'receiver_state', return_value={**base, 'service': service}), \
                    patch.object(runtime, 'output') as commands:
                with self.assertRaises(ValueError):
                    runtime.interrupt({'expected': {}})
                commands.assert_not_called()

    def test_signal_requires_the_collectors_current_pinned_ssh_child(self):
        with patch.object(runtime.os, 'pidfd_open', return_value=123), \
                patch.object(runtime.os, 'close') as close, \
                patch.object(runtime, 'ssh_child', return_value=999), \
                patch.object(runtime.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(ValueError, 'SSH child changed'):
                runtime.kill_transfer(321, 100, {})
            send.assert_not_called(); close.assert_called_once_with(123)

    def test_ssh_child_selection_ignores_unrelated_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            children = root / '100/task/100'; children.mkdir(parents=True)
            (children / 'children').write_text('321 456')
            for pid, target in [('321', 'dev@100.91.0.10'), ('456', 'hou1-backup@100.91.0.10')]:
                child = root / pid; child.mkdir()
                (child / 'cmdline').write_bytes(b'\0'.join([
                    b'ssh', b'-i', b'/fixture/key', target.encode(), b'export-v1']) + b'\0')
            original = Path
            def proc(value):
                return original(str(value).replace('/proc/', str(root) + '/'))
            with patch.object(runtime, 'Path', side_effect=proc):
                self.assertEqual(runtime.ssh_child(100, {'identity': '/fixture/key', 'source_ip': '100.91.0.10'}), 456)


class BackupControllerTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.identity = self.root / 'offline.agekey'; self.identity.write_text('fixture key'); self.identity.chmod(0o600)
        self.config = {'hosts': {'hou1': {}, 'hou4': {}}, 'application': {'revision': 'a' * 40}}
        with patch.object(controller.deploy, 'Workflow') as workflow:
            workflow.return_value.config = self.config
            workflow.return_value.run = self.root / 'run'
            self.scenario = controller.BackupScenario(self.config,
                {'host': 'hou4', 'recovery_identity': str(self.identity)}, self.root / 'run')

    def test_capture_runs_outside_guard_and_transfer_verification_inside(self):
        scenario = self.scenario; workflow = scenario.workflow
        workflow.selections = {role: {'contracts': {'backup_enabled': True}} for role in self.config['hosts']}
        events = []
        @contextmanager
        def guard():
            events.append('admit')
            try: yield
            finally: events.append('release')
        workflow.capture_guard = guard
        workflow.step = lambda name, action: action()
        scenario.remote = Mock(return_value={'points': {'prior': {}}, 'partials': [],
            'service': {'ActiveState': 'inactive', 'RuntimeMaxUSec': '31min 40s'}})
        scenario.capture = lambda: events.append('capture')
        scenario.interrupt = lambda: events.append('interrupt')
        scenario.verify_offline = lambda: events.append('offline')
        scenario.wait_access = lambda role: events.append('access-' + role)
        workflow.probe = lambda role: events.append(role)
        scenario.execute()
        self.assertEqual(events, ['capture', 'admit', 'access-hou1', 'access-hou4',
                                  'interrupt', 'offline', 'hou1', 'hou4', 'release'])
        self.assertEqual(workflow.status, 'passed')

    def test_identity_drift_stops_before_transport(self):
        self.identity.write_text('replacement identity')
        with patch.object(self.scenario.workflow, 'backup_transport') as transport:
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                self.scenario.prefix('hou4')
            transport.assert_not_called()

    def test_wrong_offline_recipient_stops_before_capture_request(self):
        self.scenario.remote = Mock(return_value={'manifest': {'size': 4*1024*1024}, 'recipient': 'age1expected'})
        result = subprocess.CompletedProcess([], 0, stdout='age1other\n', stderr='')
        with patch.object(controller.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'does not match'):
                self.scenario.capture()
        self.scenario.remote.assert_called_once_with('hou1', 'source-state')

    def test_failed_offline_verification_removes_plaintext(self):
        scenario = self.scenario
        scenario.point = {'id': '20261119T040000Z-abcdef123456'}
        scenario.prefix = Mock(return_value=['ssh', 'fixture'])
        plaintext = scenario.run / 'verified-plaintext'
        def run(command, **kwargs):
            if 'verify-recovery.py' in str(command):
                plaintext.mkdir(); (plaintext / 'secret').write_text('synthetic plaintext')
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='verification failed')
            kwargs['stdout'].write(json.dumps(scenario.point).encode() if command[-1].endswith('manifest.json')
                                   else b'fixture ciphertext')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(controller.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                scenario.verify_offline()
        self.assertFalse(plaintext.exists())

    def test_companion_access_waits_after_source_capture_without_replaying_mutations(self):
        scenario = self.scenario
        scenario.prefix = Mock(return_value=['ssh', 'fixture'])
        scenario.remote = Mock(side_effect=[ConnectionError('VPN reconnecting'), {'private_access': True}])
        with patch.object(controller.time, 'sleep') as pause:
            scenario.wait_access('hou4')
        self.assertEqual(scenario.remote.call_args_list,
                         [unittest.mock.call('hou4', 'access', timeout=30)] * 2)
        pause.assert_called_once_with(3)
        self.assertEqual(scenario.prefix.call_count, 2)

    def test_access_wait_is_bounded_and_input_drift_is_not_retried(self):
        scenario = self.scenario
        scenario.prefix = Mock(return_value=['ssh', 'fixture'])
        scenario.remote = Mock(side_effect=ConnectionError('Still unreachable'))
        with patch.object(controller.time, 'monotonic', side_effect=[0, 0, 181]), \
                patch.object(controller.time, 'sleep') as pause:
            with self.assertRaises(ConnectionError):
                scenario.wait_access('hou4')
        pause.assert_not_called()
        scenario.remote.reset_mock()
        scenario.prefix.side_effect = ValueError('Input drift')
        with patch.object(controller.time, 'sleep') as pause:
            with self.assertRaisesRegex(ValueError, 'Input drift'):
                scenario.wait_access('hou4')
        scenario.remote.assert_not_called(); pause.assert_not_called()
