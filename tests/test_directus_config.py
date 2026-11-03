import copy
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import yaml

from helpers import ROLES, run_play

ROLE = ROLES / 'directus_config'
SPEC = importlib.util.spec_from_file_location('directus_config', ROLE / 'files/configure.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONFIG = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())['directus_config_settings']


class DirectusConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'database').mkdir()
        self.database = self.root / 'database/directus.db'
        self.backups = self.root / 'backups'
        self.db = sqlite3.connect(self.database)
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE directus_settings (id INTEGER PRIMARY KEY,
                storage_asset_transform TEXT, storage_asset_presets TEXT, license_token TEXT);
            INSERT INTO directus_settings VALUES (1, 'all', NULL, 'keep-license');
            CREATE TABLE directus_policies (id TEXT PRIMARY KEY);
            INSERT INTO directus_policies VALUES ('public');
            CREATE TABLE directus_access (policy TEXT, role TEXT, user TEXT);
            INSERT INTO directus_access VALUES ('public', NULL, NULL);
            CREATE TABLE directus_permissions (id INTEGER PRIMARY KEY,
                policy TEXT, collection TEXT, action TEXT, permissions TEXT,
                validation TEXT, presets TEXT, fields TEXT);
            INSERT INTO directus_permissions VALUES (1, 'public', 'directus_files', 'read',
                '{"folder":{"_eq":"public-folder"}}', NULL, NULL, NULL);
            CREATE TABLE services (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO services VALUES (1, 'Keep service');
            CREATE TABLE pets (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE directus_files (id TEXT PRIMARY KEY);
            CREATE TABLE directus_users (id TEXT PRIMARY KEY, password TEXT);
            INSERT INTO directus_users VALUES ('admin', 'keep-password');
        ''')
        self.db.commit()

    def reconcile(self, apply=False, config=None):
        return MODULE.reconcile(self.database, config or CONFIG, self.backups, apply)

    def snapshot(self):
        return list(self.db.iterdump())

    def test_preview_backup_and_idempotence(self):
        before = self.snapshot()
        preview = self.reconcile()
        self.assertTrue(preview['changed'])
        self.assertFalse(preview['applied'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

        result = self.reconcile(apply=True)
        self.assertEqual(result['public_read_added'], ['services', 'pets', 'directus_files'])
        backup = Path(result['backup'])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backups.stat().st_mode & 0o777, 0o700)
        with closing(sqlite3.connect(backup)) as saved:
            self.assertEqual(list(saved.iterdump()), before)
        self.assertEqual(self.db.execute('SELECT license_token FROM directus_settings').fetchone()[0],
                         'keep-license')
        self.assertEqual(self.db.execute('SELECT password FROM directus_users').fetchone()[0],
                         'keep-password')
        self.assertEqual(self.db.execute('SELECT name FROM services').fetchone()[0], 'Keep service')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM directus_permissions WHERE action != 'read'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT permissions FROM directus_permissions WHERE id=1').fetchone()[0],
                         '{"folder":{"_eq":"public-folder"}}')
        self.assertEqual(json.loads(self.db.execute('SELECT storage_asset_presets FROM directus_settings').fetchone()[0]),
                         CONFIG['asset_presets'])
        after = self.snapshot()
        self.assertFalse(self.reconcile(apply=True)['changed'])
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(list(self.backups.iterdir()), [backup])

    def test_preserves_custom_presets(self):
        custom = dict(key='editor-preset', width=42)
        self.db.execute('UPDATE directus_settings SET storage_asset_presets = ?',
                        (json.dumps([custom, dict(key='web-optimized', format='jpeg')]),))
        self.db.commit()
        self.reconcile(apply=True)
        presets = json.loads(self.db.execute('SELECT storage_asset_presets FROM directus_settings').fetchone()[0])
        self.assertEqual(presets, [custom] + CONFIG['asset_presets'])

    def test_rejects_ambiguous_policy(self):
        self.db.executescript("INSERT INTO directus_policies VALUES ('other'); INSERT INTO directus_access VALUES ('other', NULL, NULL);")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'exactly one public policy'):
            self.reconcile(apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

    def test_rejects_sensitive_collection(self):
        config = copy.deepcopy(CONFIG)
        config['public_read_collections'].append('directus_users')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Unsupported public collection'):
            self.reconcile(apply=True, config=config)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

    def test_failed_write_rolls_back(self):
        self.db.executescript('''
            CREATE TRIGGER reject_permission BEFORE INSERT ON directus_permissions
            WHEN NEW.collection = 'pets'
            BEGIN SELECT RAISE(ABORT, 'blocked'); END;
        ''')
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'blocked'):
            self.reconcile(apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_role_preview_and_conditional_restart(self):
        binary = self.root / 'bin'
        binary.mkdir()
        log = self.root / 'docker.log'
        docker = binary / 'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> ' + str(log) + '\n')
        docker.chmod(0o700)
        variables = dict(app_directory=str(self.root),
                         directus_config_backup_directory=str(self.backups))
        task = {'ansible.builtin.include_role': {
            'name': 'directus_config',
            'apply': {'environment': {'PATH': str(binary) + ':' + os.environ['PATH']}},
        }}
        before = self.snapshot()
        check = run_play(variables, [task], extra_args=('--check',))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(log.exists())
        result = run_play(variables, [task, task])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = log.read_text().splitlines()
        self.assertEqual(lines.count('compose restart directus'), 1)
        self.assertEqual(sum(line.startswith('compose exec -T directus node') for line in lines), 3)


if __name__ == '__main__':
    unittest.main()
