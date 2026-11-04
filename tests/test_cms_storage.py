"""Exercise real storage tasks, Compose merges, and SQLite recovery offline."""
import io
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest

import yaml

from helpers import ROOT, ROLES, run_play


def source_tasks(role, filename):
    tasks = yaml.safe_load((ROLES / role / 'tasks' / filename).read_text())
    for task in tasks:
        template = task.get('ansible.builtin.template')
        if template:
            template['src'] = template['src'].replace('{{ role_path }}', str(ROLES / role))
            # Keep the production tasks; only substitute local test ownership.
            if 'owner' in template:
                template['owner'] = str(os.getuid())
                template['group'] = str(os.getgid())
    return tasks


class CmsStorageTest(unittest.TestCase):
    def test_test_and_production_have_distinct_storage_identity(self):
        test = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/test_env.yml').read_text())
        prod = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/prod_env.yml').read_text())
        self.assertEqual(test['cms_environment'], 'test')
        self.assertEqual(prod['cms_environment'], 'production')
        self.assertNotEqual(test['STAFF_SESSION_SECRET'], prod['STAFF_SESSION_SECRET'])

    def test_compose_keeps_database_and_ingress_in_both_deployment_orders(self):
        for order in [('cms', 'netbird'), ('netbird', 'cms')]:
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                app = root / 'app'
                app.mkdir()
                subprocess.run(['git', 'init', str(app)], check=True, capture_output=True)
                base = {'services': {
                    'astro-app': {'image': 'node:24', 'volumes': ['./database:/var/data']},
                    'nginx': {'image': 'nginx:alpine', 'networks': ['app-network']}},
                    'networks': {'app-network': {}}}
                (app / 'docker-compose.yml').write_text(yaml.safe_dump(base))
                variables = {
                    'app_directory': str(app), 'cms_media_directory': str(root / 'media'),
                    'netbird_control_plane_directory': str(root / 'netbird'),
                }
                tasks_by_name = {
                    'cms': source_tasks('webstack', 'cms-compose.yml'),
                    'netbird': source_tasks('netbird_control_plane', 'app-ingress.yml')}
                tasks = sum((tasks_by_name[name] for name in order), [])
                tasks += [{'ansible.builtin.slurp': {
                    'src': str(app / 'docker-compose.override.yml')}, 'register': 'first_render'}]
                tasks += sum((tasks_by_name[name] for name in order), [])
                tasks += [{'ansible.builtin.slurp': {
                    'src': str(app / 'docker-compose.override.yml')}, 'register': 'second_render'},
                    {'ansible.builtin.assert': {'that': 'first_render.content == second_render.content'}}]
                result = run_play(variables, tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                rendered = (app / 'docker-compose.override.yml').read_text()
                self.assertTrue(rendered.startswith('# Managed by hou1-iac.'))
                merged = subprocess.run(['docker', 'compose', '--project-directory', str(app),
                    'config', '--format', 'json'], text=True, capture_output=True)
                self.assertEqual(merged.returncode, 0, merged.stderr)
                services = json.loads(merged.stdout)['services']
                mounts = {v['target']: v['source'] for v in services['astro-app']['volumes']}
                self.assertEqual(mounts['/var/data'], str(app / 'database'))
                self.assertEqual(mounts['/var/data/media'], str(root / 'media'))
                self.assertIn('netbird-ingress', services['nginx']['networks'])
                self.assertEqual(len(services['nginx']['volumes']), 2)

    def test_owned_override_guards_do_not_replace_operator_or_app_files(self):
        for role, file in [('webstack', 'cms-compose.yml'),
                           ('netbird_control_plane', 'app-ingress.yml')]:
            for tracked in (False, True):
                with self.subTest(role=role, tracked=tracked), tempfile.TemporaryDirectory() as directory:
                    app = Path(directory)
                    subprocess.run(['git', 'init', str(app)], check=True, capture_output=True)
                    override = app / 'docker-compose.override.yml'
                    original = 'services: {}\n'
                    override.write_text(original)
                    if tracked:
                        subprocess.run(['git', '-C', str(app), 'add', override.name], check=True)
                    result = run_play({'app_directory': str(app)},
                                      source_tasks(role, file))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('override', result.stdout)
                    self.assertEqual(override.read_text(), original)

    def test_acl_accepts_real_staff_placeholder_only_with_its_vpn_restriction(self):
        for restriction, valid in [('allow ${staff_vpn_subnet};', True), ('', False)]:
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                app = Path(directory)
                (app / 'nginx').mkdir()
                source = 'server {\n server_name ${STAFF_HOSTNAME};\n ' + restriction + '\n deny all;\n}\n'
                template = app / 'nginx/nginx.conf.template'
                template.write_text(source)
                result = run_play({'app_directory': str(app)},
                    [{'ansible.builtin.import_tasks': str(ROLES / 'webstack/tasks/cms-acl.yml')}])
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
                self.assertEqual(template.read_text(), source)

    def test_backup_wal_restore_and_failure_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'app/database'
            media = root / 'media'
            backup = root / 'backup'
            app = database.parent
            for path in (database, media, backup):
                path.mkdir(parents=True)
            db = sqlite3.connect(database / 'forms.db')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE values_table (value TEXT)')
            db.execute("INSERT INTO values_table VALUES ('before')")
            db.commit()  # Keep the connection open so the committed row remains in WAL.
            (database / 'directus.db').write_text('Directus must not be backed up/restored here')
            (media / 'before.txt').write_text('before')
            variables = {
                'app_directory': str(app), 'cms_backup_directory': str(backup),
                'cms_database_directory': str(database), 'cms_media_directory': str(media),
                'cms_backup_retention_days': 30, 'cms_storage_owner': str(os.getuid()),
                'cms_storage_group': str(os.getgid())}
            result = run_play(variables, [{'ansible.builtin.template': {
                'src': str(ROLES / 'webstack/templates' / (name + '.j2')),
                'dest': str(app / name), 'mode': '0700'}}
                for name in ('cms-backup.sh', 'cms-restore.sh')])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            (fake_bin / 'docker').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n')
            (fake_bin / 'date').write_text('#!/bin/sh\necho 20261104T000000Z\n')
            for file in fake_bin.iterdir():
                file.chmod(0o700)
            log = root / 'docker.log'
            env = {**os.environ, 'PATH': str(fake_bin) + ':' + os.environ['PATH'],
                   'DOCKER_LOG': str(log)}
            run = lambda name, *args: subprocess.run([str(app / name), *map(str, args)],
                text=True, capture_output=True, env=env)
            first = run('cms-backup.sh')
            self.assertEqual(first.returncode, 0, first.stderr)
            archive = Path(first.stdout.strip())
            second = run('cms-backup.sh')
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotEqual(first.stdout, second.stdout)
            with tarfile.open(archive) as tar:
                self.assertNotIn('database/directus.db', tar.getnames())
                extracted = root / 'check'
                tar.extractall(extracted, filter='data')
            with closing(sqlite3.connect(extracted / 'database/forms.db')) as snapshot:
                self.assertEqual(snapshot.execute('SELECT value FROM values_table').fetchone()[0], 'before')
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            db.execute("UPDATE values_table SET value='changed'")
            db.commit()
            db.close()
            (media / 'before.txt').unlink()
            (media / 'changed.txt').write_text('changed')
            result = run('cms-restore.sh', archive)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(log.exists())  # Validation never stops the app.
            result = run('cms-restore.sh', archive, '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(), ['compose stop astro-app', 'compose start astro-app'])
            with closing(sqlite3.connect(database / 'forms.db')) as restored:
                self.assertEqual(restored.execute('SELECT value FROM values_table').fetchone()[0], 'before')
            self.assertEqual((media / 'before.txt').read_text(), 'before')
            self.assertEqual((media / 'changed.txt').read_text(), 'changed')  # Recovery orphans remain private.
            self.assertEqual((database / 'forms.db').stat().st_mode & 0o777, 0o640)
            self.assertEqual((media / 'before.txt').stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(backup.glob('cms-*.tar.gz'))), 3)
            # Failure to take a recovery snapshot leaves the DB intact and restarts Astro.
            (app / 'cms-backup.sh').write_text('#!/bin/sh\nexit 1\n')
            log.unlink()
            result = run('cms-restore.sh', archive, '--apply')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines(), ['compose stop astro-app', 'compose start astro-app'])
            self.assertTrue((database / 'directus.db').exists())

    def test_restore_rejects_empty_corrupt_and_unsafe_archives_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restore = root / 'cms-restore.sh'
            result = run_play({
                'app_directory': str(root), 'cms_database_directory': str(root / 'db'),
                'cms_media_directory': str(root / 'media'),
                'cms_storage_owner': str(os.getuid()), 'cms_storage_group': str(os.getgid())},
                [{'ansible.builtin.template': {
                    'src': str(ROLES / 'webstack/templates/cms-restore.sh.j2'),
                    'dest': str(restore), 'mode': '0700'}}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, body, kind in [
                    ('database/forms.db', b'', 'file'),
                    ('database/forms.db', b'not sqlite', 'file'),
                    ('../outside', b'escape', 'file'),
                    ('media/../../outside', b'escape', 'file'),
                    ('media/link', b'', 'symlink'),
                    ('media/link', b'', 'hardlink'),
                    ('media/pipe', b'', 'fifo')]:
                archive = root / 'invalid.tar.gz'
                with tarfile.open(archive, 'w:gz') as tar:
                    member = tarfile.TarInfo(name)
                    member.size = len(body)
                    if kind == 'symlink':
                        member.type = tarfile.SYMTYPE
                        member.linkname = '/etc/passwd'
                    elif kind == 'hardlink':
                        member.type = tarfile.LNKTYPE
                        member.linkname = 'database/forms.db'
                    elif kind == 'fifo':
                        member.type = tarfile.FIFOTYPE
                    tar.addfile(member, io.BytesIO(body) if kind == 'file' else None)
                result = subprocess.run([str(restore), str(archive), '--apply'],
                                        text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertFalse((root / 'outside').exists())


if __name__ == '__main__':
    unittest.main()
