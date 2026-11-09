#!/usr/bin/env python3
"""Decrypt and validate a HOU1 recovery set in an empty private operator directory.

Does not touch live services or databases. Offline identity must stay off HOU1/HOU4.
"""
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile


def extract(archive, destination, links=False):
    destination = Path(destination)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not path.parts or path.is_absolute() or '..' in path.parts or path in names
                    or not (member.isfile() or member.isdir() or (links and member.issym()))):
                raise ValueError('Unsafe archive entry')
            names.add(path)
            if member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute(): raise ValueError('Absolute symlink')
                resolved = (destination / path.parent / link).resolve()
                if not resolved.is_relative_to(destination.resolve()): raise ValueError('Escaping symlink')
        # Do not permit any member to traverse a symlink, regardless of tar member order.
        symlinks = {PurePosixPath(m.name) for m in members if m.issym()}
        if any(any(parent in symlinks for parent in PurePosixPath(m.name).parents) for m in members):
            raise ValueError('Archive writes through a symlink')
        for member in members:
            target = destination / member.name
            if member.isdir(): target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif member.issym():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.symlink_to(member.linkname)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source.extractfile(member) as data, target.open('xb') as output:
                    shutil.copyfileobj(data, output)


def verify(archive, manifest_file, identity, output, environment):
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Recovery output must be empty')
    output.mkdir(mode=0o700, parents=True, exist_ok=True); output.chmod(0o700)
    manifest = json.loads(Path(manifest_file).read_text())
    with Path(archive).open('rb') as source: digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if (manifest['schema'] != 1 or manifest['environment'] != environment
            or manifest['size'] != Path(archive).stat().st_size or manifest['sha256'] != digest):
        raise ValueError('Wrong environment, size or checksum')
    with tempfile.TemporaryDirectory(prefix='.verify-', dir=output.parent) as temporary:
        root = Path(temporary); plaintext = root / 'recovery.tar.gz'
        with plaintext.open('wb') as stream:
            subprocess.run(['age', '--decrypt', '--identity', str(identity), str(archive)],
                           stdout=stream, stderr=subprocess.DEVNULL, check=True, timeout=600)
        payload = root / 'payload'; payload.mkdir()
        extract(plaintext, payload)
        metadata = json.loads((payload / 'recovery.json').read_text())
        if metadata['schema'] != 1 or metadata['environment'] != environment: raise ValueError('Wrong recovery metadata')
        with closing(sqlite3.connect((payload / 'cms/database/forms.db').as_uri() + '?mode=ro', uri=True)) as db:
            if (db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                    or db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0):
                raise ValueError('SQLite corrupt or uninitialized')
        auth = payload / 'authentik'
        if not (auth / 'postgresql.sql').stat().st_size: raise ValueError('Empty PostgreSQL dump')
        for name in ['compose.yaml','authentik.env','oidc-clients.env','secrets.enc.yaml']:
            if not (auth / name).is_file(): raise ValueError('Incomplete Authentik recovery')
        for name, folder in [('authentik/persistent.tar.gz','authentik-files'),('netbird.tar.gz','netbird-files'),('app-config.tar.gz','app-config')]:
            dest = payload / folder; dest.mkdir()
            extract(payload / name, dest, links=True)
        for name in ['compose.yaml','config.yaml','dashboard.env','data']:
            if not (payload / 'netbird-files' / name).exists(): raise ValueError('Incomplete NetBird recovery')
        for number, _ in enumerate(metadata['recovery_files']):
            if not (payload / 'host-recovery' / str(number)).is_file(): raise ValueError('Missing host recovery key')
        # Only fully verified output becomes visible; the directory is private plaintext recovery material.
        for path in payload.iterdir(): shutil.move(str(path), output / path.name)
    return dict(verified=True, environment=environment, staff_revision=metadata['staff_revision'],
                database='sqlite-integrity-ok', offline_decryption=True,
                remaining='Import PostgreSQL into a clean database and run application/identity recovery gates')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['archive','manifest','identity','output','environment']: parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try: print(json.dumps(verify(args.archive,args.manifest,args.identity,args.output,args.environment)))
    except Exception:
        parser.exit(1, 'Recovery validation failed; private diagnostics withheld.\n')
