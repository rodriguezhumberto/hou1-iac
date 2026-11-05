"""Run the retirement tasks with real SQLite/WAL storage and a scoped Docker fixture."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

import yaml

from helpers import ROLES, run_play


DOCKER = '''#!/usr/bin/env python3
import json, os, pathlib, sys
state_path = pathlib.Path(os.environ['RETIRE_STATE'])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with open(os.environ['RETIRE_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'ps':
    filters = [args[i+1].removeprefix('label=').split('=', 1)
               for i, value in enumerate(args) if value == '--filter']
    for container in state['containers']:
        if all(container['labels'].get(key) == value for key, value in filters):
            print(container['id'])
elif args[0] in ('stop', 'rm'):
    target = next(c for c in state['containers'] if c['id'] == args[-1])
    if args[0] == 'stop':
        if state.get('fail_stop'): sys.exit(1)
        target['running'] = False
    else:
        assert not target['running'], 'Do not remove running containers'
        assert pathlib.Path(os.environ['RETIRE_ARCHIVE']).is_file(), 'Archive before removal'
        state['containers'].remove(target)
    state_path.write_text(json.dumps(state))
else:
    sys.exit('Unexpected Docker operation')
'''


class RetirementTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / 'app'
        self.database = self.app / 'database'
        self.database.mkdir(parents=True)
        uploads = self.app / 'directus-data/uploads'
        uploads.mkdir(parents=True)
        (uploads / 'photo.jpg').write_bytes(b'legacy photo')
        self.db = sqlite3.connect(self.database / 'directus.db')
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE pets (name TEXT)')
        self.db.execute("INSERT INTO pets VALUES ('committed in WAL')")
        self.db.commit()  # Leave the connection open so a checkpoint is necessary.
        self.customer = self.database / 'forms.db'
        self.customer.write_bytes(b'Customer data must remain byte-identical')
        self.backups = self.root / 'backups'
        self.backups.mkdir()
        self.archive = self.backups / 'directus.tar.gz'
        self.state = self.root / 'docker-state.json'
        self.log = self.root / 'docker-log.jsonl'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'docker').write_text(DOCKER)
        (self.bin / 'docker').chmod(0o700)
        self.containers = [
            self.container('local', 'paws', self.app),
            self.container('foreign', 'another-app', self.app),
            self.container('other-folder', 'paws', self.root / 'another-folder'),
            self.container('astro', 'paws', self.app, 'astro-app'),
        ]
        self.state.write_text(json.dumps({'containers': self.containers}))
        main = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())
        self.remove = next(t for t in main if t['name'] == 'Remove archived Directus containers from this project')

    def container(self, name, project, directory, service='directus'):
        return {'id': name, 'running': True, 'labels': {
            'com.docker.compose.project': project,
            'com.docker.compose.project.working_dir': str(directory),
            'com.docker.compose.service': service}}

    def run_retirement(self, services=None, fail_deployment=False):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/retire-directus.yml').read_text())
        # Only local ownership differs from production.
        for task in tasks:
            if 'ansible.builtin.file' in task:
                task['ansible.builtin.file'].update(owner=str(os.getuid()), group=str(os.getgid()))
        if fail_deployment:
            tasks += [{'ansible.builtin.fail': {'msg': 'Deployment failed'}}]
        tasks += [self.remove]
        return run_play({
            'app_directory': str(self.app), 'cms_database_directory': str(self.database),
            'directus_retirement_archive_directory': str(self.backups),
            'directus_retirement_archive': str(self.archive),
            'role_path': str(ROLES / 'webstack'),
            'cms_compose_config': {'stdout': json.dumps({'name': 'paws', 'services':
                services if services is not None else {'astro-app': {}, 'nginx': {}}})},
        }, [{'block': tasks, 'environment': {
            'PATH': str(self.bin) + ':' + os.environ['PATH'],
            'RETIRE_STATE': str(self.state), 'RETIRE_LOG': str(self.log),
            'RETIRE_ARCHIVE': str(self.archive)}}])

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_checkpoint_archive_and_remove_only_this_projects_directus(self):
        self.assertGreater((self.database / 'directus.db-wal').stat().st_size, 0)
        result = self.run_retirement()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual([c['id'] for c in state['containers']], ['foreign', 'other-folder', 'astro'])
        self.assertTrue(all(c['running'] for c in state['containers']))
        mutations = [c for c in self.calls() if c[0] != 'ps']
        self.assertEqual(mutations, [['stop', '--time', '30', 'local'], ['rm', 'local']])
        self.assertEqual((self.database / 'directus.db-wal').stat().st_size, 0)
        with tarfile.open(self.archive) as tar:
            self.assertNotIn('database/forms.db', tar.getnames())
            self.assertEqual(tar.extractfile('directus-data/uploads/photo.jpg').read(), b'legacy photo')
            snapshot = self.root / 'archived.db'
            snapshot.write_bytes(tar.extractfile('database/directus.db').read())
        with sqlite3.connect(snapshot) as db:
            self.assertEqual(db.execute('SELECT name FROM pets').fetchall(), [('committed in WAL',)])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.customer.read_bytes(), b'Customer data must remain byte-identical')
        before = self.archive.read_bytes()
        repeated = self.run_retirement()
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_stop_failure_prevents_archive_and_removal(self):
        self.state.write_text(json.dumps({'containers': self.containers, 'fail_stop': True}))
        result = self.run_retirement()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.archive.exists())
        self.assertFalse(any(c[0] == 'rm' for c in self.calls()))

    def test_missing_database_blocks_retirement_before_stopping(self):
        (self.database / 'directus.db').rename(self.database / 'unexpected.db')
        result = self.run_retirement()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.archive.exists())
        self.assertTrue(all(c[0] == 'ps' for c in self.calls()))

    def test_no_legacy_storage_or_containers_leaves_other_projects_alone(self):
        self.db.close()
        for file in self.database.glob('directus.db*'):
            file.unlink()
        (self.app / 'directus-data/uploads/photo.jpg').unlink()
        (self.app / 'directus-data/uploads').rmdir()
        (self.app / 'directus-data').rmdir()
        remaining = self.containers[1:]
        self.state.write_text(json.dumps({'containers': remaining}))
        result = self.run_retirement()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['containers'], remaining)
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.customer.read_bytes(), b'Customer data must remain byte-identical')

    def test_old_application_stack_is_rejected_before_stopping(self):
        result = self.run_retirement(services={'astro-app': {}, 'directus': {}})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.archive.exists())

    def test_corrupt_or_outdated_archive_prevents_removal(self):
        for contents in (b'not a tar archive', None):
            with self.subTest(corrupt=contents is not None):
                if contents is not None:
                    self.archive.write_bytes(contents)
                else:
                    self.archive.unlink()
                    first = self.run_retirement(fail_deployment=True)
                    self.assertNotEqual(first.returncode, 0)
                    self.db.execute("INSERT INTO pets VALUES ('later edit')")
                    self.db.commit()
                before = hashlib.sha256(self.archive.read_bytes()).digest()
                result = self.run_retirement()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(hashlib.sha256(self.archive.read_bytes()).digest(), before)
                self.assertFalse(any(c[0] == 'rm' for c in self.calls()))
                self.assertEqual(self.customer.read_bytes(), b'Customer data must remain byte-identical')

    def test_busy_checkpoint_blocks_archiving_and_removal(self):
        reader = sqlite3.connect(self.database / 'directus.db')
        self.addCleanup(reader.close)
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM pets').fetchall()
        self.db.execute("INSERT INTO pets VALUES ('newer WAL page')")
        self.db.commit()
        result = self.run_retirement()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.archive.exists())
        self.assertFalse(any(c[0] == 'rm' for c in self.calls()))

    def test_failed_deployment_keeps_archive_and_stopped_container(self):
        result = self.run_retirement(fail_deployment=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.archive.exists())
        local = next(c for c in json.loads(self.state.read_text())['containers'] if c['id'] == 'local')
        self.assertFalse(local['running'])
        self.assertFalse(any(c[0] == 'rm' for c in self.calls()))
