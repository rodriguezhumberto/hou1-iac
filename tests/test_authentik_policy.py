from pathlib import Path
import unittest
import yaml
from tests.helpers import guard_play, run_plays

ROOT = Path(__file__).resolve().parents[1]


class AuthentikPolicyTest(unittest.TestCase):
    def test_inode_gate_uses_free_count_and_rejects_exhaustion(self):
        tasks = yaml.safe_load((ROOT / 'ansible/playbooks/roles/authentik_policy/tasks/main.yml').read_text())
        gate = next(task for task in tasks if task['name'] == 'Require inode headroom for Authentik and PostgreSQL')
        fixtures = []
        for free, failure in [(900000, None), (5, gate['name'])]:
            fixtures.append(guard_play({
                'authentik_min_free_inodes': 100000,
                'authentik_inode_check': {'stdout_lines': [
                    'Filesystem Inodes IUsed IFree IUse% Mounted on',
                    f'/dev/vda1 1000000 {1000000-free} {free} 10% /',
                ]},
            }, [gate], failure))
        result = run_plays(fixtures)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preflight_is_explicit_and_gated(self):
        play = yaml.safe_load((ROOT / 'ansible/playbooks/authentik-preflight.yml').read_text())[0]
        self.assertEqual(play['roles'][0]['role'], 'authentik_policy')
        self.assertIn('capacity', play['roles'][0]['tags'])
        tasks = (ROOT / 'ansible/playbooks/roles/authentik_policy/tasks/main.yml').read_text()
        for required in ['ansible_processor_vcpus', 'ansible_memtotal_mb', 'df -Pi', 'iostat -dx 1 2', '5432', 'docker network ls', 'docker ps --format']:
            self.assertIn(required, tasks)

    def test_test_and_production_networks_are_distinct(self):
        variables = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
        self.assertNotEqual(variables['authentik_test_network'], variables['authentik_prod_network'])
        self.assertNotEqual(variables['authentik_test_network'], variables['netbird_ingress_network'])
        self.assertNotEqual(variables['authentik_prod_network'], variables['netbird_ingress_network'])
