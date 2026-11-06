from pathlib import Path
import unittest
import io
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import os

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'ansible/playbooks/roles/authentik/templates'


class AuthentikBackupTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('age') and shutil.which('age-keygen'), 'age executables required')
    def test_backup_encrypts_logical_dump_without_copying_live_pgdata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, data, backups, offhost, binaries = [root / name for name in ['project', 'data', 'backups', 'offhost', 'bin']]
            for path in [project, data, backups, offhost, binaries]:
                path.mkdir()
            for name in ['postgres', 'media', 'templates', 'certs', 'config']:
                (data / name).mkdir()
                (data / name / 'marker').write_text(name)
            for name in ['compose.yaml', 'authentik.env', 'oidc-clients.env', 'secrets.enc.yaml']:
                (project / name).write_text('fixture-private-config')
            identity = root / 'test.key'
            subprocess.run(['age-keygen', '-o', str(identity)], capture_output=True, check=True)
            recipient = subprocess.run(['age-keygen', '-y', str(identity)], capture_output=True, text=True, check=True).stdout.strip()
            docker = binaries / 'docker'
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "fixture-logical-sql"\n')
            docker.chmod(0o755)
            env = Environment()
            env.filters['quote'] = shlex.quote
            script = root / 'backup.sh'
            script.write_text(env.from_string((TEMPLATES / 'backup.sh.j2').read_text()).render(
                authentik_project_root=str(project), authentik_data_root=str(data), authentik_backup_root=str(backups),
                authentik_offhost_backup_root=str(offhost), authentik_compose_project='test-only',
                authentik_backup_age_recipient=recipient, authentik_age_key_file=str(identity),
                authentik_backup_retention_days=30))
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH']})
            self.assertEqual(result.returncode, 0, result.stderr)
            archive, = backups.glob('*.tar.gz.age')
            self.assertNotIn(b'fixture-private-config', archive.read_bytes())
            self.assertTrue((offhost / archive.name).exists())
            decrypted = subprocess.run(['age', '--decrypt', '--identity', str(identity), str(archive)], capture_output=True, check=True).stdout
            with tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:gz') as tar:
                self.assertEqual(tar.extractfile('postgresql.sql').read(), b'fixture-logical-sql\n')
                persistent = tar.extractfile('persistent.tar').read()
            with tarfile.open(fileobj=io.BytesIO(persistent)) as tar:
                self.assertIn('media/marker', tar.getnames())
                self.assertFalse(any(name.startswith('postgres') for name in tar.getnames()))

    def test_backup_is_encrypted_verified_and_reports_failures(self):
        backup = (TEMPLATES / 'backup.sh.j2').read_text()
        for required in ['set -euo pipefail', 'pg_dump', 'persistent.tar', 'age --encrypt', 'sha256sum', 'age --decrypt', '.success', '.failed', 'offhost_root']:
            self.assertIn(required, backup)
        self.assertIn('failed=0', backup)

    def test_restore_and_drill_validate_a_clean_recovery(self):
        restore = (TEMPLATES / 'restore.sh.j2').read_text()
        drill = (TEMPLATES / 'restore-drill.sh.j2').read_text()
        for required in ['sha256sum --check', 'DROP SCHEMA public CASCADE', 'pg_isready', 'persistent.tar', '--identity']:
            self.assertIn(required, restore)
        for required in ['duration_seconds', 'discovery=validated', 'jwks=validated', 'gaps=record-in-runbook']:
            self.assertIn(required, drill)

    def test_backup_roots_and_cron_are_separate(self):
        tasks = (ROOT / 'ansible/playbooks/roles/authentik/tasks/main.yml').read_text()
        self.assertIn('authentik_offhost_backup_root', tasks)
        self.assertIn('Schedule encrypted Authentik backups', tasks)
        self.assertIn('authentik_backup_retention_days', (ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
