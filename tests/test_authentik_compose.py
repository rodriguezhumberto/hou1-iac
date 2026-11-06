from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'ansible/playbooks/roles/authentik/templates/compose.yaml.j2'


class AuthentikComposeTest(unittest.TestCase):
    def test_compose_has_pinned_images_health_gate_limits_and_private_database(self):
        compose = TEMPLATE.read_text()
        for required in [
                'image: {{ authentik_image }}',
                'image: {{ authentik_postgres_image }}',
                'condition: service_healthy', 'healthcheck:', 'mem_limit:', 'cpus:',
                'pids_limit:', 'restart: unless-stopped', 'max-size: "20m"',
                'internal: true', 'expose: [\'9000\']']:
            self.assertIn(required, compose)
        self.assertNotIn('5432:', compose)
        self.assertNotIn('ports:', compose)

    def test_test_and_prod_state_are_variable_separated(self):
        tasks = (ROOT / 'ansible/playbooks/roles/authentik/tasks/main.yml').read_text()
        self.assertIn('authentik_environment in [\'test\', \'prod\']', tasks)
        self.assertIn('/var/lib/hou1-authentik/(test|prod)', tasks)
