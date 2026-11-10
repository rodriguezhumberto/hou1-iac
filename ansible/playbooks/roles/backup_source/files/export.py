#!/usr/bin/python3
"""Prepare one encrypted recovery point with durable interrupted-writer recovery."""
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
WRITERS = {'authentik': ['authentik-server', 'authentik-worker'],
           'app': ['astro-app'], 'netbird': ['netbird-server']}


def sync_directory(path):
    descriptor = os.open(path, os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def atomic_json(path, payload):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(payload, stream); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def command(argv, output=None, timeout=300):
    return subprocess.run(argv, stdout=output if output is not None else subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=True, timeout=timeout).stdout


def compose(project):
    return ['docker', 'compose', '--project-directory', project['root'], '-p', project['name']]


@contextmanager
def frozen(project, services, journal=None):
    base = compose(project)
    running = command(base + ['ps', '--status', 'running', '--services']).decode().splitlines()
    if not set(services).issubset(running):
        raise RuntimeError('Required writer is not running')
    # Persist before stopping: ExecStopPost/boot recovery must work after SIGKILL.
    if journal is not None:
        path, component = journal
        atomic_json(path, {'schema': 1, 'component': component})
    # Always attempt restart, including a partially failed stop or interrupted snapshot.
    try:
        command(base + ['stop', '--timeout', '30', *services], timeout=90)
        yield
    finally:
        command(base + ['start', *services], timeout=120)
        if journal is not None:
            path.unlink(); sync_directory(path.parent)


def archive_tree(output, directory, names):
    with tarfile.open(output, 'w:gz', dereference=False) as archive:
        for name in names:
            archive.add(Path(directory) / name, arcname=name)


def application_receipt(root):
    import importlib.util
    helper = Path(__file__).with_name('hou1-application-artifact.py')
    if not helper.exists():
        helper = Path(__file__).resolve().parents[2] / 'application_image/files/artifact.py'
    spec = importlib.util.spec_from_file_location('application_artifact', helper)
    artifact = importlib.util.module_from_spec(spec); spec.loader.exec_module(artifact)
    return artifact.running(root)


def snapshot(config, work):
    auth, app, netbird = (config[key] for key in ('authentik', 'app', 'netbird'))
    auth_work = work / 'authentik'; auth_work.mkdir()
    intent = Path(config['work_root']) / 'restart-intent.json'
    with frozen(auth, WRITERS['authentik'], (intent, 'authentik')):
        with (auth_work / 'postgresql.sql').open('wb') as sql:
            command(compose(auth) + ['exec', '-T', 'postgresql', 'pg_dump', '-U', 'authentik', '-d',
                                    'authentik', '--no-owner', '--format=plain'], output=sql)
        archive_tree(auth_work / 'persistent.tar.gz', auth['data'], ['media', 'templates', 'certs', 'config'])
        for name in ('compose.yaml', 'authentik.env', 'oidc-clients.env', 'secrets.enc.yaml'):
            shutil.copyfile(Path(auth['root']) / name, auth_work / name)
    cms = work / 'cms'; (cms / 'database').mkdir(parents=True)
    with frozen(app, WRITERS['app'], (intent, 'app')):
        application_image = application_receipt(app['root'])
        database = Path(app['database']) / 'forms.db'
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(cms / 'database/forms.db')) as target:
                source.backup(target)
                if (target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                        or target.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0):
                    raise RuntimeError('Invalid SQLite snapshot')
        shutil.copytree(app['media'], cms / 'media', symlinks=True)
        # Save runtime configuration and TLS, never the live database or plaintext backup directory.
        files = ['.env', 'docker-compose.yml', 'docker-compose.override.yml', 'certbot/conf']
        if application_image is not None:
            files.append('application-image.json')
        archive_tree(work / 'app-config.tar.gz', app['root'], files)
    # Copy while stopped, then compress after restart. Integrated relays also stop
    # with this container, so preparation runs locally, independently of SSH.
    netbird_copy = work / 'netbird-copy'; netbird_copy.mkdir()
    with frozen(netbird, WRITERS['netbird'], (intent, 'netbird')):
        for name in ['compose.yaml', 'config.yaml', 'dashboard.env']:
            shutil.copy2(Path(netbird['root']) / name, netbird_copy / name)
        shutil.copytree(Path(netbird['root']) / 'data', netbird_copy / 'data', symlinks=True)
    try:
        archive_tree(work / 'netbird.tar.gz', netbird_copy, ['compose.yaml', 'config.yaml', 'dashboard.env', 'data'])
    finally:
        shutil.rmtree(netbird_copy)
    recovery = work / 'host-recovery'; recovery.mkdir()
    for number, name in enumerate(config['recovery_files']):
        path = Path(name)
        if not path.is_file(): raise ValueError('Missing required host recovery input')
        shutil.copyfile(path, recovery / str(number))
    revision = command(['git', '-C', app['root'], 'rev-parse', 'HEAD']).decode().strip()
    metadata = {'schema': 1, 'environment': config['environment'],
        'staff_revision': revision, 'recovery_files': config['recovery_files'],
        'components': ['authentik', 'netbird', 'cms', 'app-config', 'host-recovery']}
    if application_image is not None:
        if application_image['revision'] != revision:
            raise ValueError('Source revision changed during the backup')
        metadata['application_image'] = application_image
    (work / 'recovery.json').write_text(json.dumps(metadata))


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
    atomic_json(path, payload)


def recover(config):
    """Resume only the recorded fixed writer group, then remove partial plaintext."""
    root = Path(config['work_root'])
    intent = root / 'restart-intent.json'
    if intent.exists():
        record = json.loads(intent.read_text())
        component = record.get('component')
        if record != {'schema': 1, 'component': component} or component not in WRITERS:
            raise ValueError('Invalid writer recovery intent')
        # Paths and service arguments come from root-owned config/code, never the journal.
        base = compose(config[component]); services = WRITERS[component]
        command(base + ['start', *services], timeout=120)
        running = command(base + ['ps', '--status', 'running', '--services']).decode().splitlines()
        if not set(services).issubset(running): raise RuntimeError('Writer recovery incomplete')
        status(root, False)
        intent.unlink(); sync_directory(root)
    for old in root.glob('.snapshot-*'):
        if old.is_dir() and not old.is_symlink(): shutil.rmtree(old)
    for pattern in ('.prepared-*', '*.tmp'):
        for old in root.glob(pattern):
            if old.is_file() and not old.is_symlink(): old.unlink()


def prepare(config):
    """Atomically replace one encrypted recovery point without depending on SSH."""
    root = Path(config['work_root']); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in root.glob('.prepared-*'):
        if old.is_file() and not old.is_symlink(): old.unlink()
    descriptor, name = tempfile.mkstemp(prefix='.prepared-', dir=root)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            manifest = export(config, stream)
            stream.flush(); os.fsync(stream.fileno())
        staged.replace(root / 'ready.export')
        directory = os.open(root, os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        return manifest
    finally:
        staged.unlink(missing_ok=True)


def main():
    if os.geteuid() != 0 or sys.argv[1:] not in ([], ['--recover']):
        raise PermissionError('Fixed root preparation or recovery only')
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    with open('/run/lock/hou1-backup-export.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(CONFIG.read_text())
        recover(config)
        if sys.argv[1:] == ['--recover']: return
        # A hard kill cannot report failure; record the attempt before stopping writers.
        status(config['work_root'], False)
        try:
            manifest = prepare(config)
            status(config['work_root'], True, manifest)
        except Exception:
            status(config['work_root'], False)
            raise


if __name__ == '__main__':
    try: main()
    except Exception:
        print('HOU1 backup export failed; private diagnostics withheld.', file=sys.stderr)
        sys.exit(1)
