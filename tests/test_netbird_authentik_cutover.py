from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NetBirdAuthentikCutoverTest(unittest.TestCase):
    def test_cutover_is_blocked_without_spike_and_preserves_state(self):
        tasks = (ROOT / 'ansible/playbooks/roles/netbird_control_plane/tasks/authentik-cutover.yml').read_text()
        for required in ['netbird_version == \'0.82.0\'', 'netbird_authentik_compatibility_passed', 'data', 'secrets.json', 'config.yaml', 'dashboard.env']:
            self.assertIn(required, tasks)

    def test_external_config_keeps_sqlite_keys_and_redirects(self):
        config = (ROOT / 'ansible/playbooks/roles/netbird_control_plane/templates/config-authentik.yaml.j2').read_text()
        self.assertIn('issuer: "{{ netbird_authentik_issuer }}"', config)
        self.assertIn('engine: sqlite', config)
        self.assertIn('store_encryption_key', config)
        self.assertIn('dashboardRedirectURIs', config)
        self.assertIn('cliRedirectURIs', config)

    def test_rollback_and_health_evidence_are_present(self):
        cutover = (ROOT / 'ansible/playbooks/roles/netbird_control_plane/templates/authentik-cutover.sh.j2').read_text()
        rollback = (ROOT / 'ansible/playbooks/roles/netbird_control_plane/templates/authentik-rollback.sh.j2').read_text()
        for required in ['netbird-state.tar.gz', 'embedded_snapshot', 'jwks_uri', 'health=passed']:
            self.assertIn(required, cutover)
        self.assertIn('embedded_issuer', rollback)
