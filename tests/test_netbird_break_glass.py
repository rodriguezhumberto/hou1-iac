from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NetBirdBreakGlassTest(unittest.TestCase):
    def test_recovery_separates_pat_ssh_console_and_dashboard(self):
        script = (ROOT / 'ansible/playbooks/roles/netbird_control_plane/templates/break-glass.sh.j2').read_text()
        docs = (ROOT / 'docs/netbird-break-glass.md').read_text()
        for required in ['NETBIRD_API_PAT', 'Authorization: Token', 'ssh ', 'authentik-rollback.sh', 'api_pat_is_not_dashboard_login']:
            self.assertIn(required, script)
        for required in ['host/serial console', 'does not create a dashboard login', '15-minute']:
            self.assertIn(required, docs)

    def test_evidence_is_redacted_and_operator_scoped(self):
        tasks = (ROOT / 'ansible/playbooks/roles/netbird_control_plane/tasks/break-glass.yml').read_text()
        self.assertIn('netbird_break_glass_operators', tasks)
        self.assertIn("mode: '0600'", tasks)
        self.assertIn('api_pat_is_not_dashboard_login=true', tasks)
