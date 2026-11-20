"""Scheduling uses the selected executor, or the maintained direct publisher."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

from helpers import ROLES, guard_play, run_plays


class RenewalScheduleTest(unittest.TestCase):
    def test_profiles_are_validated_before_deriving_installed_paths(self):
        tasks = yaml.safe_load((ROLES / 'controller_tls/tasks/executor.yml').read_text())
        scenarios = []
        for profile, suffix in [('', ''), ('lab', '-lab')]:
            scenarios.append(guard_play({'hou1_tls_executor_profile': profile}, tasks + [
                {'ansible.builtin.assert': {'that': '_hou1_tls_executor_suffix == ' + repr(suffix)}}]))
        for profile in ['../production', 'lab;id', 'lab\n', 'LAB', None, 'x' * 33]:
            scenarios.append(guard_play({'hou1_tls_executor_profile': profile}, tasks, tasks[0]['name']))
        result = run_plays(scenarios)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_direct_mode_publishes_without_invoking_a_namespace_job(self):
        source = yaml.safe_load((ROLES / 'controller_tls/tasks/schedule.yml').read_text())
        block = next(task['block'] for task in source if 'block' in task)
        task = next(t for t in block if t['name'] ==
                    'Publish an initial renewal status before dependent monitoring starts')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_file = root / 'invocation.json'
            (root / 'controller-tls.py').write_text(
                'import json,sys\nfrom pathlib import Path\n'
                + 'Path(' + repr(str(result_file)) + ').write_text(json.dumps(sys.argv[1:]))\n')
            variables = {'hou1_tls_executor_job_prefix': '', '_hou1_tls_executor_suffix': '',
                         'ansible_playbook_python': sys.executable,
                         'hou1_tls_schedule_directory': directory,
                         'hou1_tls_controller_config': str(root / 'controller.json')}
            result = run_plays([{'variables': variables, 'tasks': [task]}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result_file.read_text()),
                             ['--config', str(root / 'controller.json'), '--publish-status'])

    def test_fresh_scheduler_installs_programs_before_its_readiness_check(self):
        tasks = yaml.safe_load((ROLES / 'controller_tls/tasks/schedule.yml').read_text())
        names = [t['name'] for t in next(t['block'] for t in tasks if 'block' in t)]
        self.assertLess(names.index('Install controller TLS programs from this checkout'),
                        names.index('Verify private configuration and scoped SSH before changing timers'))
