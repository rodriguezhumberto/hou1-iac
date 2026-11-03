"""Exercise the replacement CMS storage templates without a VPS."""
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

import yaml

from helpers import ROOT, run_play


class CmsStorageTest(unittest.TestCase):
    def test_test_and_production_have_distinct_storage_identity(self):
        test = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/test_env.yml').read_text())
        prod = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/prod_env.yml').read_text())
        self.assertEqual(test['cms_environment'], 'test')
        self.assertEqual(prod['cms_environment'], 'production')
        self.assertNotEqual(test['STAFF_SESSION_SECRET'], prod['STAFF_SESSION_SECRET'])

    def test_backup_and_restore_scripts_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'data/database'
            media = root / 'data/media'
            backup = root / 'backup'
            app = root / 'app'
            database.mkdir(parents=True)
            media.mkdir(parents=True)
            backup.mkdir()
            app.mkdir()

            db = sqlite3.connect(database / 'cms.db')
            db.execute('CREATE TABLE values_table (value TEXT)')
            db.execute("INSERT INTO values_table VALUES ('before')")
            db.commit()
            db.close()
            (media / 'before.txt').write_text('before')

            variables = {
                'app_directory': str(app),
                'cms_backup_directory': str(backup),
                'cms_database_directory': str(database),
                'cms_media_directory': str(media),
                'cms_backup_retention_days': 30,
                'cms_storage_owner': str(os.getuid()),
                'cms_storage_group': str(os.getgid()),
            }
            tasks = []
            for name in ('cms-backup.sh.j2', 'cms-restore.sh.j2'):
                tasks.append({'ansible.builtin.template': {
                    'src': str(ROOT / 'ansible/playbooks/roles/webstack/templates' / name),
                    'dest': str(app / name[:-3]),
                }})
            result = run_play(variables, tasks)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in ('cms-backup.sh', 'cms-restore.sh'):
                (app / name).chmod(0o700)

            # The restore helper's Compose calls are deliberately fake here;
            # this test validates archive and SQLite behavior offline.
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            docker = fake_bin / 'docker'
            docker.write_text('#!/bin/sh\nexit 0\n')
            docker.chmod(0o700)
            env = {**os.environ, 'PATH': str(fake_bin) + ':' + os.environ['PATH']}
            subprocess.run([str(app / 'cms-backup.sh')], check=True, env=env)
            archive = next(backup.glob('cms-*.tar.gz'))

            db = sqlite3.connect(database / 'cms.db')
            db.execute('DELETE FROM values_table')
            db.execute("INSERT INTO values_table VALUES ('changed')")
            db.commit()
            db.close()
            (media / 'before.txt').unlink()
            (media / 'changed.txt').write_text('changed')

            subprocess.run([str(app / 'cms-restore.sh'), str(archive), '--apply'],
                           check=True, env=env)
            db = sqlite3.connect(database / 'cms.db')
            self.assertEqual(db.execute('SELECT value FROM values_table').fetchone()[0], 'before')
            db.close()
            self.assertEqual((media / 'before.txt').read_text(), 'before')
            self.assertFalse((media / 'changed.txt').exists())


if __name__ == '__main__':
    unittest.main()
