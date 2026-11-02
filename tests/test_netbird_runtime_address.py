"""The client role promotes its verified address for later security tasks."""
from pathlib import Path
import unittest
import yaml

from helpers import ROLES


class RuntimeAddressTest(unittest.TestCase):
    def test_main_sets_verified_address_before_security(self):
        tasks = yaml.safe_load((ROLES / 'netbird_client/tasks/main.yml').read_text())
        names = [task.get('name') for task in tasks]
        self.assertLess(names.index('Verify the enrolled peer address'),
                        names.index('Use the verified API-assigned address for later roles'))
        task = next(task for task in tasks if task.get('name') == 'Use the verified API-assigned address for later roles')
        self.assertIn('netbird_ip', task['ansible.builtin.set_fact'])
        self.assertIn('management_ip', task['ansible.builtin.set_fact'])
        self.assertEqual(task['when'], 'netbird_enabled | bool')


if __name__ == '__main__':
    unittest.main()
