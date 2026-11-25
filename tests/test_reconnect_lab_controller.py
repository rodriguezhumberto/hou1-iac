"""Controller reconnect stays bound to private inputs and the existing container."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from helpers import ROOT

SPEC = importlib.util.spec_from_file_location('reconnect_lab', ROOT / 'scripts/reconnect-lab-controller.py')
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


class ControllerReconnectTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = json.loads((ROOT / 'examples/lab-network.json').read_text())
        state = self.root / 'peer'; state.mkdir(mode=0o700)
        resolver = self.root / 'resolver'; resolver.write_text('nameserver 192.0.2.53\n'); resolver.chmod(0o600)
        self.config.update(state_directory=str(state), resolver_file=str(resolver))
        self.path = self.root / 'network.json'
        self.path.write_text(json.dumps(self.config)); self.path.chmod(0o600)
        self.peer = {'id': 'a' * 64, 'pid': 12345}

    def test_plan_validates_disconnected_identity_without_mutation(self):
        for connected in [True, False]:
            with self.subTest(connected=connected), \
                    patch.object(controller.network, 'inspect_container', return_value=self.peer), \
                    patch.object(controller.network, 'peer_connected', return_value=connected), \
                    patch.object(controller.subprocess, 'run') as run:
                report = controller.reconnect(self.path)
                self.assertEqual(report, dict(environment='test', validated=True, connected=connected, applied=False))
                run.assert_not_called()

    def test_reconnect_preserves_container_and_waits_for_same_peer(self):
        with patch.object(controller.network, 'inspect_container', return_value=self.peer), \
                patch.object(controller.network, 'peer_connected', side_effect=[False, False, True]), \
                patch.object(controller.subprocess, 'run') as run, patch.object(controller.time, 'sleep') as sleep:
            report = controller.reconnect(self.path, apply=True)
            self.assertTrue(report['applied'] and report['connected'] and report['container_preserved'])
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['docker', 'exec', self.peer['id'], 'netbird', action] for action in ['down', 'up']])
            self.assertTrue(all(call.kwargs == dict(capture_output=True, check=True, timeout=30)
                                for call in run.call_args_list))
            sleep.assert_called_once_with(1)

    def test_invalid_config_or_peer_stops_before_down(self):
        for change in [{'environment': 'production'}, {'transport': 'bootstrap'},
                       {'management_url': 'https://production.example.invalid'}, {'address': '100.90.0.2'}]:
            with self.subTest(change=change):
                self.path.write_text(json.dumps({**self.config, **change}))
                with patch.object(controller.subprocess, 'run') as run, self.assertRaises(ValueError):
                    controller.reconnect(self.path, apply=True)
                run.assert_not_called()
        self.path.write_text(json.dumps(self.config))
        with patch.object(controller.network, 'inspect_container', return_value=self.peer), \
                patch.object(controller.network, 'peer_connected', side_effect=ValueError('wrong peer')), \
                patch.object(controller.subprocess, 'run') as run, self.assertRaises(ValueError):
            controller.reconnect(self.path, apply=True)
        run.assert_not_called()

    def test_changed_config_or_container_stops_before_up(self):
        for change in ['config', 'id', 'pid']:
            with self.subTest(change=change):
                self.path.write_text(json.dumps(self.config))
                peer = dict(self.peer)
                def execute(*args, **kwargs):
                    if change == 'config':
                        self.path.write_text(json.dumps({**self.config, 'container': 'different'}))
                    else:
                        peer[change] = 'b' * 64 if change == 'id' else 23456
                with patch.object(controller.network, 'inspect_container', side_effect=lambda c, **kw: copy.copy(peer)), \
                        patch.object(controller.network, 'peer_connected', return_value=True), \
                        patch.object(controller.subprocess, 'run', side_effect=execute) as run, \
                        self.assertRaisesRegex(ValueError, 'changed during reconnect'):
                    controller.reconnect(self.path, apply=True)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][-1], 'down')

    def test_failed_command_is_not_retried_and_output_is_withheld(self):
        stderr = io.StringIO()
        with patch.object(controller.network, 'inspect_container', return_value=self.peer), \
                patch.object(controller.network, 'peer_connected', return_value=True), \
                patch.object(controller.subprocess, 'run', side_effect=subprocess.CalledProcessError(
                    1, ['docker'], output='private output', stderr='private error')) as run, redirect_stderr(stderr):
            self.assertEqual(controller.main(['--config', str(self.path), '--apply']), 1)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('private output', stderr.getvalue())
        self.assertNotIn('private error', stderr.getvalue())

    def test_connection_wait_is_bounded(self):
        clock = {'seconds': 0}
        def advance(seconds):
            clock['seconds'] = 91
        with patch.object(controller.network, 'inspect_container', return_value=self.peer), \
                patch.object(controller.network, 'peer_connected', return_value=False), \
                patch.object(controller.subprocess, 'run'), patch.object(controller.time, 'sleep', side_effect=advance) as sleep, \
                patch.object(controller.time, 'monotonic', side_effect=lambda: clock['seconds']), \
                self.assertRaisesRegex(TimeoutError, 'ninety seconds'):
            controller.reconnect(self.path, apply=True)
        sleep.assert_called_once_with(1)

    def test_validation_and_commands_share_the_deadline(self):
        clock = {'seconds': 0}
        def inspect(config, **kwargs):
            clock['seconds'] = 80
            return self.peer
        with patch.object(controller.network, 'inspect_container', side_effect=inspect), \
                patch.object(controller.network, 'peer_connected', return_value=True) as status, \
                patch.object(controller.subprocess, 'run') as run, \
                patch.object(controller.time, 'monotonic', side_effect=lambda: clock['seconds']):
            self.assertTrue(controller.reconnect(self.path, apply=True)['applied'])
        self.assertEqual([call.kwargs['timeout'] for call in run.call_args_list], [10, 10])
        self.assertTrue(all(call.kwargs['timeout'] == 10 for call in status.call_args_list))


if __name__ == '__main__':
    unittest.main()
