#!/usr/bin/python3
"""Emit a single offline-age-encrypted, consistent HOU1 recovery set over stdout."""
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

CONFIG = Path('/etc/hou1-backup/source.json')


def command(argv, output=None, timeout=300):
    return subprocess.run(argv, stdout=output if output is not None else subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=True, timeout=timeout).stdout


def compose(project):
    return ['docker', 'compose', '--project-directory', project['root'], '-p', project['name']]


@contextmanager
def frozen(project, services):
    base = compose(project)
    running = command(base + ['ps', '--status', 'running', '--services']).decode().splitlines()
    if not set(services).issubset(running):
        raise RuntimeError('Required writer is not running')
    # Always attempt restart, including a partially failed stop or interrupted snapshot.
    try:
        command(base + ['stop', '--timeout', '30', *services], timeout=90)
        yield
    finally:
        command(base + ['start', *services], timeout=120)


def archive_tree(output, directory, names):
    with tarfile.open(output, 'w:gz', dereference=False) as archive:
        for name in names:
            archive.add(Path(directory) / name, arcname=name)


def snapshot(config, work):
    auth, app, netbird = (config[key] for key in ('authentik', 'app', 'netbird'))
    auth_work = work / 'authentik'; auth_work.mkdir()
    with frozen(auth, ['authentik-server', 'authentik-worker']):
        with (auth_work / 'postgresql.sql').open('wb') as sql:
            command(compose(auth) + ['exec', '-T', 'postgresql', 'pg_dump', '-U', 'authentik', '-d',
                                    'authentik', '--no-owner', '--format=plain'], output=sql)
        archive_tree(auth_work / 'persistent.tar.gz', auth['data'], ['media', 'templates', 'certs', 'config'])
        for name in ('compose.yaml', 'authentik.env', 'oidc-clients.env', 'secrets.enc.yaml'):
            shutil.copyfile(Path(auth['root']) / name, auth_work / name)
    cms = work / 'cms'; (cms / 'database').mkdir(parents=True)
    with frozen(app, ['astro-app']):
        database = Path(app['database']) / 'forms.db'
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(cms / 'database/forms.db')) as target:
                source.backup(target)
                if (target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                        or target.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0):
                    raise RuntimeError('Invalid SQLite snapshot')
        shutil.copytree(app['media'], cms / 'media', symlinks=True)
        # Save runtime configuration and TLS, never the live database or plaintext backup directory.
        archive_tree(work / 'app-config.tar.gz', app['root'],
                     ['.env', 'docker-compose.yml', 'docker-compose.override.yml', 'certbot/conf'])
    # The NetBird outage is bounded to a local copy of its state; peers/SSH and ingress stay running.
    with frozen(netbird, ['netbird-server']):
        archive_tree(work / 'netbird.tar.gz', netbird['root'], ['compose.yaml', 'config.yaml', 'dashboard.env', 'data'])
    recovery = work / 'host-recovery'; recovery.mkdir()
    for number, name in enumerate(config['recovery_files']):
        path = Path(name)
        if not path.is_file(): raise ValueError('Missing required host recovery input')
        shutil.copyfile(path, recovery / str(number))
    revision = command(['git', '-C', app['root'], 'rev-parse', 'HEAD']).decode().strip()
    (work / 'recovery.json').write_text(json.dumps({'schema': 1, 'environment': config['environment'],
        'staff_revision': revision, 'recovery_files': config['recovery_files'],
        'components': ['authentik', 'netbird', 'cms', 'app-config', 'host-recovery']}))


def estimate_bytes(config):
    roots = [config['authentik']['data'], config['app']['database'], config['app']['media'],
             config['netbird']['root'], str(Path(config['app']['root']) / 'certbot/conf')]
    size = 0
    for root in roots:
        for directory, _, files in os.walk(root, followlinks=False):
            for name in files:
                path = Path(directory) / name
                if path.is_file() and not path.is_symlink(): size += path.stat().st_size
    return size


def export(config, stream):
    root = Path(config['work_root']); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in root.glob('.snapshot-*'):
        if old.is_dir() and not old.is_symlink(): shutil.rmtree(old)
    # Reserve three snapshot sizes in addition to the host's operating headroom.
    if shutil.disk_usage(root).free < config['minimum_free_bytes'] + 3 * estimate_bytes(config):
        raise RuntimeError('Insufficient snapshot headroom')
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=root) as directory:
        work = Path(directory); payload = work / 'payload'; payload.mkdir()
        snapshot(config, payload)
        # Age encryption is streamed; neither VPS needs an offline recovery identity.
        encrypted = work / 'recovery.age'
        with encrypted.open('wb') as output:
            process = subprocess.Popen(['age', '--encrypt', '--recipient', config['recipient']],
                                       stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL)
            try:
                with tarfile.open(fileobj=process.stdin, mode='w|gz') as archive:
                    for path in sorted(payload.iterdir()): archive.add(path, arcname=path.name)
                process.stdin.close()
                if process.wait(timeout=300) != 0: raise RuntimeError('Encryption failed')
            finally:
                if process.poll() is None: process.kill(); process.wait()
        size = encrypted.stat().st_size
        if not 0 < size <= config['max_archive_bytes']: raise ValueError('Recovery set exceeds budget')
        with encrypted.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        created = int(time.time())
        manifest = dict(schema=1, environment=config['environment'], created=created,
                        id=time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(created))+'-'+uuid.uuid4().hex[:12],
                        size=size, sha256=digest)
        stream.write(json.dumps(manifest).encode() + b'\n')
        with encrypted.open('rb') as archive: shutil.copyfileobj(archive, stream)
        stream.flush()
        return manifest


def status(root, successful, manifest=None):
    path = Path(root) / 'last-export.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    payload = dict(schema=1, last_run_success=int(successful), attempted=int(time.time()),
                   last_success=manifest['created'] if successful else previous.get('last_success', 0))
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(payload, stream); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1: raise PermissionError('Fixed root export only')
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    with open('/run/lock/hou1-backup-export.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(CONFIG.read_text())
        try:
            manifest = export(config, sys.stdout.buffer)
            status(config['work_root'], True, manifest)
        except Exception:
            status(config['work_root'], False)
            raise


if __name__ == '__main__':
    try: main()
    except Exception:
        print('HOU1 backup export failed; private diagnostics withheld.', file=sys.stderr)
        sys.exit(1)
