"""WireGuard must be restarted without blocking its SSH transport."""
from pathlib import Path
import unittest
import yaml


class WireGuardHandlerTest(unittest.TestCase):
    def test_restart_is_non_blocking(self):
        handlers = yaml.safe_load((Path(__file__).parents[1] /
                                   'ansible/playbooks/roles/wireguard/handlers/main.yml').read_text())
        task = handlers[0]
        self.assertEqual(task['name'], 'Restart WireGuard')
        self.assertTrue(task['ansible.builtin.systemd']['no_block'])
        self.assertEqual(task['ansible.builtin.systemd']['state'], 'restarted')


if __name__ == '__main__':
    unittest.main()
