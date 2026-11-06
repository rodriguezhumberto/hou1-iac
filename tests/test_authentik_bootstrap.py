from pathlib import Path
import json
import py_compile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'ansible/playbooks/roles/authentik'


class AuthentikBootstrapTest(unittest.TestCase):
    def test_bootstrap_script_is_valid_and_api_reconciliation_is_idempotent(self):
        py_compile.compile(str(ROLE / 'files/bootstrap.py'), doraise=True)
        script = (ROLE / 'files/bootstrap.py').read_text()
        for required in ['def ensure', 'PATCH', 'core/groups', 'propertymappings/provider/scope', 'providers/oauth2', 'core/applications', 'environment does not match']:
            self.assertIn(required, script)

    def test_manifests_keep_clients_and_audiences_isolated(self):
        for env in ['test', 'prod']:
            content = (ROLE / 'templates' / f'bootstrap-{env}.json.j2').read_text()
            self.assertIn(f'"environment": "{env}"', content)
            self.assertIn('client_id', content)
            self.assertIn('audience', content)
            self.assertIn('redirect_uris', content)
            self.assertIn('logout_uris', content)
        test = (ROLE / 'templates/bootstrap-test.json.j2').read_text()
        prod = (ROLE / 'templates/bootstrap-prod.json.j2').read_text()
        self.assertIn('pearland-paws-staff-test', test)
        self.assertIn('pearland-paws-staff', prod)
        self.assertIn('netbird-test', test)
        self.assertIn('netbird-prod', prod)

    def test_bootstrap_is_disabled_for_base_compose_deployments(self):
        tasks = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn('authentik_bootstrap_enabled | bool', tasks)
        self.assertIn('AUTHENTIK_BOOTSTRAP_TOKEN', tasks)
