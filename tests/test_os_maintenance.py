"""OS upgrades are explicit; maintenance retains the private management boundary."""
import unittest
import yaml

from helpers import ROLES, guard_play, run_plays


class OsMaintenanceTest(unittest.TestCase):
    def test_routine_host_setup_does_not_request_package_upgrades(self):
        tasks = yaml.safe_load((ROLES / 'common/tasks/main.yml').read_text())
        package_tasks = [task.get('ansible.builtin.apt', task.get('apt')) for task in tasks]
        package_tasks = [args for args in package_tasks if args is not None]
        self.assertTrue(package_tasks)
        self.assertTrue(any(task.get('update_cache') for task in package_tasks))
        self.assertFalse(any(task.get('upgrade') not in (None, False, 'no')
                             for task in package_tasks))

    def test_maintenance_refuses_bootstrap_and_public_management(self):
        task = yaml.safe_load((ROLES / 'os_maintenance/tasks/main.yml').read_text())[0]
        # Run the production guard, never the apt task, on disposable localhost.
        cases = [
            ({'node_bootstrap': False, 'ansible_host': '100.90.0.10', 'netbird_ip': '100.90.0.10'}, None),
            ({'node_bootstrap': True, 'ansible_host': '100.90.0.10', 'netbird_ip': '100.90.0.10'}, task['name']),
            ({'node_bootstrap': False, 'ansible_host': '203.0.113.10', 'netbird_ip': '100.90.0.10'}, task['name']),
        ]
        result = run_plays([guard_play(values, [task], expected) for values, expected in cases])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_upgrade_mode_is_rejected(self):
        task = yaml.safe_load((ROLES / 'os_maintenance/tasks/main.yml').read_text())[1]
        result = run_plays([
            guard_play({'os_maintenance_upgrade': mode, 'os_maintenance_cache_valid_time': 3600},
                       [task], None if mode in ('safe', 'dist') else task['name'])
            for mode in ('safe', 'dist', 'yes', 'no', 'invalid')
        ])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
