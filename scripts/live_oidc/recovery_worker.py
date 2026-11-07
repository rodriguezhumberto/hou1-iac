"""Self-contained, root-only Authentik restore guardian for the fixed test VPS.

Install in /root/hou1-oidc-recovery before the drill. The timer can recover
without controller SSH, Authentik API access, DNS or new image downloads.
"""

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/root/hou1-oidc-recovery')
PROJECT = Path('/opt/hou1-authentik/test')
DATA = Path('/var/lib/hou1-authentik/test')
FILES = ('compose.yaml', 'authentik.env', 'oidc-clients.env', 'secrets.enc.yaml')
NAMES = ('hou1-authentik-test-authentik-server-1', 'hou1-authentik-test-authentik-worker-1',
         'hou1-authentik-test-postgresql-1')
COMPOSE = ['docker', 'compose', '-p', 'hou1-authentik-test', '-f', str(PROJECT / 'compose.yaml')]


def run(command, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Isolated recovery command failed; output withheld')
    return result.stdout


def guard():
    if os.geteuid() != 0:
        raise RuntimeError('Privileged test-host operation required')
    app = json.loads(run(['docker', 'inspect', 'pearlandpaws-test-astro-app-1']))[0]
    env = dict(item.split('=', 1) for item in app['Config']['Env'])
    if env.get('STAFF_ENVIRONMENT') != 'test' or env.get('STAFF_OIDC_ISSUER') != (
        'https://auth-test.vps-test.pearlandpaws.com/application/o/pearland-paws-staff-test/'
    ):
        raise RuntimeError('Isolated test deployment required')
    if ROOT.is_symlink() or ROOT.stat().st_uid != 0 or ROOT.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe recovery directory')


def recover():
    """Restore the consistent cold original state if fresh recovery fails."""
    if (ROOT / 'completed').exists() or (ROOT / 'recovered').exists():
        return
    if (ROOT / 'original-data').exists():
        for name in NAMES:
            # An absent container is safe; do not touch the NetBird stack.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=90)
        if DATA.exists():
            DATA.rename(ROOT / 'failed-restore-data')
        (ROOT / 'original-data').rename(DATA)
    if (ROOT / 'original-config').exists():
        for name in FILES:
            shutil.copyfile(ROOT / 'original-config' / name, PROJECT / name)
            (PROJECT / name).chmod(0o644 if name == 'compose.yaml' else 0o600)
    run(COMPOSE + ['up', '-d', '--wait', '--wait-timeout', '600'], timeout=660)
    run(['docker', 'exec', 'pearlandpaws-test-nginx-1', 'nginx', '-t'])
    run(['docker', 'exec', 'pearlandpaws-test-nginx-1', 'nginx', '-s', 'reload'])
    (ROOT / 'recovered').write_text('Original test state recovered\n')


def restore():
    if any((ROOT / name).exists() for name in ('original-data', 'completed', 'recovered')):
        raise RuntimeError('Recovery drill already started')
    saved = ROOT / 'original-config'
    saved.mkdir(mode=0o700)
    for name in FILES:
        shutil.copyfile(PROJECT / name, saved / name)
        (saved / name).chmod(0o600)
    run(COMPOSE + ['down'])
    # Quarantine a cold database; the restore must initialize fresh PGDATA.
    DATA.rename(ROOT / 'original-data')
    for name in FILES:
        (PROJECT / name).unlink()
    try:
        run([str(PROJECT / 'restore.sh'), str(ROOT / 'controller-backup.tar.gz.age')], timeout=1000)
        run(['docker', 'exec', 'pearlandpaws-test-nginx-1', 'nginx', '-t'])
        run(['docker', 'exec', 'pearlandpaws-test-nginx-1', 'nginx', '-s', 'reload'])
        (ROOT / 'restore-command-passed').write_text('Fresh state restored; awaiting controller assertions\n')
    except BaseException:
        recover()
        raise


def main():
    os.umask(0o077)
    guard()
    with (ROOT / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if sys.argv[1:] == ['restore']:
            restore()
        elif sys.argv[1:] == ['recover']:
            recover()
        else:
            raise RuntimeError('Use restore or recover')
    print(json.dumps({'test_recovery_command': 'passed'}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'test_recovery_command': 'failed', 'diagnostics': 'withheld'}))
        sys.exit(1)
