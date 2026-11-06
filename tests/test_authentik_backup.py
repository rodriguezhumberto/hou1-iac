from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'ansible/playbooks/roles/authentik/templates'


class AuthentikBackupTest(unittest.TestCase):
    def test_backup_is_encrypted_verified_and_reports_failures(self):
        backup = (TEMPLATES / 'backup.sh.j2').read_text()
        for required in ['set -euo pipefail', 'pg_dump', 'persistent.tar', 'age --encrypt', 'sha256sum', 'age --decrypt', '.success', '.failed', 'offhost_root']:
            self.assertIn(required, backup)
        self.assertIn('failed=0', backup)

    def test_restore_and_drill_validate_a_clean_recovery(self):
        restore = (TEMPLATES / 'restore.sh.j2').read_text()
        drill = (TEMPLATES / 'restore-drill.sh.j2').read_text()
        for required in ['sha256sum --check', 'DROP SCHEMA public CASCADE', 'pg_isready', 'persistent.tar']:
            self.assertIn(required, restore)
        for required in ['duration_seconds', 'discovery=validated', 'jwks=validated', 'gaps=record-in-runbook']:
            self.assertIn(required, drill)

    def test_backup_roots_and_cron_are_separate(self):
        tasks = (ROOT / 'ansible/playbooks/roles/authentik/tasks/main.yml').read_text()
        self.assertIn('authentik_offhost_backup_root', tasks)
        self.assertIn('Schedule encrypted Authentik backups', tasks)
        self.assertIn('authentik_backup_retention_days', (ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
