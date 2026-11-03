#!/usr/bin/env python3
"""Reconcile application CMS settings in SQLite."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def decode(value, fallback=None):
    return json.loads(value) if value is not None else fallback


def plan(db, config):
    """Validate the entire plan before making any changes."""
    settings = db.execute('SELECT * FROM directus_settings').fetchall()
    if len(settings) != 1:
        raise ValueError('Expected one initialized Directus settings row')
    current = settings[0]
    presets = decode(current['storage_asset_presets'], [])
    requested = config['asset_presets']
    keys = [p['key'] for p in requested]
    if len(keys) != len(set(keys)) or not all(keys):
        raise ValueError('Managed asset preset keys must be unique and nonempty')
    # Preserve editor-created presets.
    merged = [p for p in presets if p['key'] not in keys] + requested
    settings_changes = {}
    if sorted(presets, key=lambda p: p['key']) != sorted(merged, key=lambda p: p['key']):
        settings_changes['storage_asset_presets'] = json.dumps(merged)
    if current['storage_asset_transform'] != config['storage_asset_transform']:
        settings_changes['storage_asset_transform'] = config['storage_asset_transform']

    public_policies = db.execute('''
        SELECT DISTINCT p.id FROM directus_policies p
        JOIN directus_access a ON a.policy = p.id
        WHERE a.role IS NULL AND a.user IS NULL
    ''').fetchall()
    if len(public_policies) != 1:
        raise ValueError('Expected exactly one public policy; review CMS access first')
    policy = public_policies[0]['id']
    additions = []
    for collection in dict.fromkeys(config['public_read_collections']):
        if collection not in {'services', 'pets', 'directus_files'}:
            raise ValueError('Unsupported public collection: ' + collection)
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (collection,),
        ).fetchone()
        if not exists:
            raise ValueError('Missing application collection: ' + collection)
        permissions = db.execute('''
            SELECT permissions, fields FROM directus_permissions
            WHERE policy = ? AND collection = ? AND action = 'read'
        ''', (policy, collection)).fetchall()
        if not any(decode(p['permissions']) in (None, {}) and
                   p['fields'] == '*' for p in permissions):
            additions.append(collection)
    return current['id'], settings_changes, policy, additions


def reconcile(database, config, backup_directory, apply=False):
    database = Path(database).resolve(strict=True)
    db = sqlite3.connect(database.as_uri() + ('?mode=rw' if apply else '?mode=ro'),
                         uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    backup = None
    try:
        row_id, settings, policy, additions = plan(db, config)
        changed = bool(settings or additions)
        if changed and apply:
            backup_directory = Path(backup_directory)
            backup_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(backup_directory, 0o700)
            prefix = datetime.now(timezone.utc).strftime('directus-%Y%m%dT%H%M%SZ-')
            fd, backup = tempfile.mkstemp(prefix=prefix, suffix='.db', dir=backup_directory)
            os.close(fd)
            with closing(sqlite3.connect(backup)) as target:
                db.backup(target)
            db.execute('BEGIN IMMEDIATE')
            row_id, settings, policy, additions = plan(db, config)
            for field, value in settings.items():
                db.execute('UPDATE directus_settings SET ' + field + ' = ? WHERE id = ?',
                           (value, row_id))
            for collection in additions:
                db.execute('''
                    INSERT INTO directus_permissions
                        (policy, collection, action, permissions, validation, presets, fields)
                    VALUES (?, ?, 'read', NULL, NULL, NULL, '*')
                ''', (policy, collection))
            db.commit()
        return dict(changed=changed, applied=changed and apply, backup=backup,
                    settings=list(settings), public_read_added=additions)
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--backup-directory', required=True)
    parser.add_argument('--configuration', required=True, type=json.loads)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(reconcile(args.database, args.configuration,
                              args.backup_directory, args.apply)))
