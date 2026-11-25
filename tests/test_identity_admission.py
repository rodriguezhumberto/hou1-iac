"""Default-off admission and validated scheduling preserve controller boundaries."""

import os
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import ROLES, guard_play, run_play, run_plays


class IdentityAdmissionTest(unittest.TestCase):
    def test_role_does_nothing_without_declared_private_inputs(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'HOME': directory}):
            result = run_play({}, [{'ansible.builtin.include_role': {'name': 'identity_admission'}}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('changed=0', result.stdout)

    def test_scheduled_transport_rejects_shell_and_systemd_expansions(self):
        tasks = yaml.safe_load((ROLES / 'identity_admission/tasks/schedule-inputs.yml').read_text())[:1]
        base = {'identity_admission_schedule_enabled': True,
                'identity_admission_coordination_lock': '/private/workflow.lock'}
        scenarios = [([], None), (['/usr/bin/python3', '/private/lab-network.py', '--'], None)]
        for bad in [['sh', '-c', 'echo secret'], ['%h/config'], ['$TOKEN'], ['line\nbreak'], [123], 'sh -c']:
            scenarios.append((bad, 'Validate scheduled admission transport and coordination'))
        result = run_plays([guard_play({**base, 'identity_admission_transport_argv': argv}, tasks, expected)
                            for argv, expected in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
