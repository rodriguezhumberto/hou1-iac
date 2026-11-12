from pathlib import Path
import unittest


RUNBOOK = Path(__file__).resolve().parents[1] / "docs/production-oidc-runbook.md"


class ProductionRunbookTest(unittest.TestCase):
    def test_runbook_has_immutable_refs_gates_and_recovery_paths(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        required = (
            "INFRA_REF=<40 lowercase hex commit SHA>", "STAFF_REF=", "NETBIRD_REF=",
            "BACKUP_ID=", "BACKUP_SHA256=", "APP_IMAGE=", "TEST_REHEARSAL=", "OIDC_TESTS=",
            "authentik-preflight.yml", "authentik-bootstrap.yml", "netbird-authentik-cutover.yml",
            "verify-recovery.py", "recovery.age", "receipt.json", "break-glass.sh", "authentik-rollback.sh",
            "Current production evidence and remaining handoff limits:", "test-only", "ON_ERROR_STOP",
            "PostgreSQL", "Nginx", "Staff login", "1%", "5 minutes", "15 minutes",
        )
        for marker in required:
            self.assertIn(marker, text)

    def test_runbook_does_not_contain_secret_values_or_test_deploy_targets(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertNotIn("NETBIRD_API_PAT=", text)
        self.assertNotIn("/mnt/offhost-backups/", text)
        self.assertNotIn("restore-drill.sh", text)
        self.assertNotIn("client_secret=", text)
        self.assertNotIn("hou1_test", text)


if __name__ == "__main__":
    unittest.main()
