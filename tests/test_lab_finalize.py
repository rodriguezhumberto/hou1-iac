"""Finalization ordering, resume binding and bounded backup observation."""

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
import lab_finalize as controller
import lab_backup_runtime as runtime


class FinalizationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.identity = self.root / 'offline.agekey'
        self.identity.write_text('private fixture'); self.identity.chmod(0o600)
        self.events = []
        self.locked = False
        self.workflow = Mock()
        w = self.workflow
        w.config = {'version': 2, 'hosts': {r: {'repository': '/fixture/' + r} for r in ('hou1', 'hou4')}}
        w.run = self.root / 'run'; w.run.mkdir(mode=0o700)
        w.inputs = {'hou1': {'inventory': 'frozen'}, 'hou4': {}}
        w.capabilities = {'encrypted-input': 'sha256'}
        w.transports = {'hou1': [['ssh', 'source'], 'dev', 22], 'hou4': [['ssh', 'receiver'], 'dev', 22]}
        w.selections = {r: {'contracts': {'backup_enabled': True}} for r in ('hou1', 'hou4')}
        w.step.side_effect = lambda name, op: op()
        @contextmanager
        def guard(**kwargs):
            self.assertFalse(self.locked)
            self.locked = True; self.events.append('admit')
            try: yield
            finally:
                self.events.append('release'); self.locked = False
        w.capture_guard.side_effect = guard
        self.finalize = controller.Finalization(w, str(self.identity))
        self.point = {'id': '20261119T040000Z-abcdef123456', 'sha256': 'a' * 64}

    def receipt(self):
        path = self.root / 'receipt.json'
        path.write_text(json.dumps({'version': 1, 'environment': 'test', 'phase': 'finalize',
                                   'finalization': self.workflow.finalization}))
        path.chmod(0o600)
        return path

    def test_runtime_precedes_capture_and_collection_holds_selected_point(self):
        f = self.finalize
        def play(role, path, name, **kwargs):
            self.events.append(name)
            if name == 'backup-source-initialize':
                self.assertTrue(self.locked)
                self.assertEqual(json.loads(kwargs['extra_args'][1]), {'hou1_backup_capture_on_initialize': False})
            else: self.assertTrue(self.locked)
        self.workflow.playbook.side_effect = play
        self.workflow.probe.side_effect = lambda role: self.events.append('verify-' + role)
        def capture():
            self.assertFalse(self.locked); self.events.append('capture')
        def collect():
            self.assertTrue(self.locked); self.events.append('collect')
        f.capture_initial = capture; f.collect = collect
        f.wait_access = lambda role: self.events.append('access-' + role)
        f.verify_offline = lambda: self.events.append('offline')
        f.execute()
        self.assertEqual(self.events, ['admit', 'hou1-reconcile', 'verify-hou1',
            'backup-source-initialize', 'release', 'capture', 'access-hou4', 'admit', 'hou4-reconcile',
            'collect', 'offline', 'verify-hou1', 'verify-hou4', 'release'])

    def test_reconcile_failure_releases_lock_without_capture_or_receiver_changes(self):
        self.workflow.playbook.side_effect = ValueError('source migration failed')
        self.finalize.capture_initial = Mock(); self.finalize.collect = Mock()
        with self.assertRaisesRegex(ValueError, 'migration'):
            self.finalize.execute()
        self.assertEqual(self.events, ['admit', 'release'])
        self.assertEqual(self.workflow.playbook.call_count, 1)
        self.finalize.capture_initial.assert_not_called(); self.finalize.collect.assert_not_called()

    def test_capture_failure_stops_receiver_changes(self):
        self.finalize.capture_initial = Mock(side_effect=ValueError('capture failed'))
        self.finalize.collect = Mock()
        with self.assertRaisesRegex(ValueError, 'capture failed'):
            self.finalize.execute()
        self.assertEqual([call.args[0] for call in self.workflow.playbook.call_args_list], ['hou1', 'hou1'])
        self.finalize.collect.assert_not_called()

    def test_resume_accepts_same_point_and_rejects_foreign_or_changed_bindings(self):
        self.workflow.finalization['point'] = self.point
        path = self.receipt()
        resumed = controller.Finalization(self.workflow, str(self.identity), str(path))
        self.assertEqual(resumed.point, self.point)
        original = json.loads(path.read_text())
        for field in ('configuration_sha256', 'inputs', 'capabilities', 'transports', 'identity'):
            value = copy.deepcopy(original); value['finalization']['binding'][field] = 'changed'
            path.write_text(json.dumps(value))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differ'):
                controller.Finalization(self.workflow, str(self.identity), str(path))
        original['phase'] = 'verify'; path.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, 'finalization receipt'):
            controller.Finalization(self.workflow, str(self.identity), str(path))

    def test_lost_start_response_is_observed_without_replaying_mutation(self):
        f = self.finalize; f.prefix = Mock()
        f.remote = Mock(side_effect=[ConnectionError('response lost'), ConnectionError('reconnecting'), {'ready': True}])
        f.request('hou1', 'ensure-capture')
        with patch.object(controller.time, 'sleep'):
            result = f.observe('hou1', 'source-status', lambda state: state['ready'], 3000)
        self.assertTrue(result['ready'])
        self.assertEqual([c.args[1] for c in f.remote.call_args_list],
                         ['ensure-capture', 'source-status', 'source-status'])

    def test_observation_deadline_and_input_drift_fail_without_mutating(self):
        f = self.finalize; f.prefix = Mock(); f.remote = Mock(side_effect=ConnectionError())
        with patch.object(controller.time, 'monotonic', side_effect=[0, 0, 5, 6]), \
                patch.object(controller.time, 'sleep'):
            with self.assertRaises(TimeoutError):
                f.observe('hou1', 'source-status', lambda state: True, 5)
        f.remote.reset_mock(); f.prefix.side_effect = ValueError('input drift')
        with self.assertRaisesRegex(ValueError, 'input drift'):
            f.observe('hou1', 'source-status', lambda state: True, 5)
        f.remote.assert_not_called()

    def test_resume_never_silently_switches_to_a_new_source_point(self):
        f = self.finalize; f.point = self.point
        f.prefix = Mock()
        f.remote = Mock(return_value={'manifest': {**self.point, 'id': 'other'}, 'recipient': 'age1fixture'})
        f.request = Mock()
        with patch.object(controller.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'age1fixture\n')):
            with self.assertRaisesRegex(ValueError, 'point changed'):
                f.capture_initial()
        f.request.assert_not_called()

    def test_selected_point_saved_before_collection_and_existing_capture_reused(self):
        state = {'manifest': self.point, 'recipient': 'age1fixture', 'intent': False,
                 'status': {'last_success': 123}, 'service': {'ActiveState': 'inactive', 'Result': 'success'}}
        f = self.finalize; f.remote = Mock(return_value=state); f.prefix = Mock()
        with patch.object(controller.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'age1fixture\n')):
            f.capture_initial()
        self.assertEqual(self.workflow.finalization['point'], self.point)
        self.workflow.save.assert_called()
        f.request = Mock(); f.remote.return_value = {'points': {self.point['id']: {'manifest': self.point}},
            'partials': [], 'service': state['service']}
        f.remote.side_effect = [state, f.remote.return_value, f.remote.return_value, f.remote.return_value]
        f.collect()

    def test_older_scheduled_transfer_finishes_before_selected_point_is_requested(self):
        f = self.finalize; f.point = self.point; f.prefix = Mock()
        old_active = {'points': {}, 'partials': ['.incoming-old'],
                      'service': {'ActiveState': 'activating', 'Result': 'success'}}
        old_done = {'points': {}, 'partials': [],
                    'service': {'ActiveState': 'inactive', 'Result': 'success'}}
        committed = {**old_done, 'points': {self.point['id']: {'manifest': self.point}}}
        observations = iter([old_active, old_done, old_done, old_active, committed])
        events = []
        def remote(role, operation, **kwargs):
            if role == 'hou1':
                return {'manifest': self.point, 'intent': False}
            state = next(observations)
            events.append((operation, state['service']['ActiveState']))
            return state
        f.remote = Mock(side_effect=remote)
        with patch.object(controller.time, 'sleep'):
            f.collect()
        self.assertEqual(events, [('receiver-state', 'activating'), ('receiver-state', 'inactive'),
            ('collect', 'inactive'), ('receiver-state', 'activating'), ('receiver-state', 'inactive')])
        f.remote.assert_any_call('hou4', 'collect', expected=self.point)


class InitializationRuntimeTest(unittest.TestCase):
    def contract(self, role, operation):
        return dict(environment='test', role=role, hostname='fixture', address='100.91.0.10',
                    interface='wt0', source_ip='100.91.0.10', operation=operation)

    def run_remote(self, contract, state):
        addresses = json.dumps([{'addr_info': [{'local': '100.91.0.10'}]}])
        with patch.object(runtime.platform, 'node', return_value='fixture'), \
                patch.object(runtime, 'source_state', return_value=state), \
                patch.object(runtime, 'receiver_state', return_value=state), \
                patch.object(runtime, 'output', return_value=addresses) as command:
            runtime.run(contract)
            return [call.args[0] for call in command.call_args_list if call.args[0][0] == 'systemctl']

    def test_ensure_capture_reuses_success_or_active_service_and_retries_failed_initial_only(self):
        for success, active, starts in [(0, 'inactive', 1), (0, 'failed', 1), (0, 'activating', 0),
                                         (123, 'inactive', 0), (123, 'failed', 0)]:
            state = {'status': {'last_success': success}, 'intent': False, 'service': {'ActiveState': active}}
            with self.subTest(success=success, active=active):
                self.assertEqual(len(self.run_remote(self.contract('hou1', 'ensure-capture'), state)), starts)

    def test_interrupted_writers_refuse_capture(self):
        state = {'status': {'last_success': 0}, 'intent': True, 'service': {'ActiveState': 'failed'}}
        with self.assertRaisesRegex(ValueError, 'Interrupted writers'):
            self.run_remote(self.contract('hou1', 'ensure-capture'), state)

    def test_collector_reuses_exact_committed_point_without_starting_again(self):
        point = {'id': 'point', 'sha256': 'checksum'}
        state = {'points': {'point': {'manifest': point}}, 'partials': [],
                 'service': {'ActiveState': 'inactive', 'RuntimeMaxUSec': '31min'}}
        contract = {**self.contract('hou4', 'collect'), 'expected': point}
        self.assertEqual(self.run_remote(contract, state), [])
        state['points']['point']['manifest'] = {**point, 'sha256': 'corrupt'}
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.run_remote(contract, state)

    def test_empty_source_status_is_allowed_only_before_first_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); config = root / 'source.json'
            config.write_text(json.dumps(dict(environment='test', work_root='/var/lib/hou1-backup', recipient='age1fixture')))
            original_path = Path
            def mapped(value):
                return root if value == '/var/lib/hou1-backup' else original_path(value)
            with patch.object(runtime, 'SOURCE', config), patch.object(runtime, 'Path', side_effect=mapped), \
                    patch.object(runtime, 'unit', return_value={}):
                self.assertIsNone(runtime.source_state(allow_empty=True)['manifest'])
                (root / 'last-export.json').write_text(json.dumps({'last_success': 123}))
                with self.assertRaisesRegex(ValueError, 'point is missing'):
                    runtime.source_state(allow_empty=True)
