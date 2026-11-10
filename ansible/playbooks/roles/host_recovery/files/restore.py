#!/usr/bin/env python3
"""Restore a fresh test host once; leave published ports on loopback for review."""
import argparse
import importlib.util
import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parent
RECOVERY_BASE = Path('/var/lib/hou1-host-recovery')
APP = Path('/opt/pearlandpaws-test')
AUTH = Path('/opt/hou1-authentik/test')
AUTH_DATA = Path('/var/lib/hou1-authentik/test')
VPN = Path('/opt/hou1-netbird')
MEDIA = Path('/var/lib/pearlandpaws/test/cms/media')
PROJECTS = {'auth': ('hou1-authentik-test', AUTH), 'netbird': ('hou1-netbird-test', VPN),
            'app': ('pearlandpaws-test', APP)}
SESSION_TABLES = ['django_session', 'authentik_core_authenticatedsession',
                  'authentik_providers_oauth2_authorizationcode',
                  'authentik_providers_oauth2_accesstoken', 'authentik_providers_oauth2_refreshtoken']


def run(*args, input=None, env=None, timeout=300):
    process = subprocess.run(list(args), input=input, capture_output=True, env=env, timeout=timeout)
    with (ROOT / 'private.log').open('ab') as log:
        log.write(process.stdout + process.stderr)
    if process.returncode:
        raise RuntimeError('Recovery command failed; inspect private log')
    return process.stdout


def dc(project, *args, **kwargs):
    name, directory = PROJECTS[project]
    argv = ['docker', 'compose', '--project-directory', str(directory), '-p', name]
    argv += ['-f', str(directory / ('docker-compose.yml' if project == 'app' else 'compose.yaml'))]
    if project == 'app':
        argv += ['-f', str(APP / 'docker-compose.override.yml')]
    return run(*argv, *args, **kwargs)


def paths_absent(paths):
    for path in paths:
        if path.exists() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError('Recovery would overwrite or traverse an existing destination')


def copy_tree(source, destination, uid=0, gid=0, public=False):
    shutil.copytree(source, destination, symlinks=True)
    for path in [destination, *destination.rglob('*')]:
        if not path.is_symlink():
            os.chown(path, uid, gid)
            path.chmod((0o755 if public else 0o750) if path.is_dir() else (0o644 if public else 0o640))


def write(name, data):
    (ROOT / name).write_text(json.dumps(data, indent=2) + '\n')


def sql(query):
    return dc('auth', 'exec', '-T', 'postgresql', 'psql', '-h', '127.0.0.1', '-XqAt',
              '-v', 'ON_ERROR_STOP=1', '-U', 'authentik', '-d', 'authentik', '-c', query).decode().strip()


def wait_database():
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if sql('SELECT 1') == '1':
                return
        except RuntimeError:
            pass
        time.sleep(1)
    raise TimeoutError('Fresh PostgreSQL query readiness failed')


def revoke_sessions(database):
    for project, services in [('auth', ['authentik-server', 'authentik-worker']), ('app', ['astro-app'])]:
        for service in services:
            if run('docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECTS[project][0],
                   '--filter', 'label=com.docker.compose.service=' + service).strip():
                raise ValueError('Stop restored writers before clearing sessions')
    sql('BEGIN; TRUNCATE ' + ','.join(SESSION_TABLES) + ' CASCADE; COMMIT;')
    if any(sql('SELECT count(*) FROM ' + table) != '0' for table in SESSION_TABLES):
        raise ValueError('Restored identity sessions remain')
    with sqlite3.connect(database) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Restored SQLite integrity failed')
        db.execute('UPDATE staff_sessions SET revoked_at = ? WHERE revoked_at IS NULL', (int(time.time() * 1000),))
        if db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0]:
            raise ValueError('Restored staff sessions remain')


def reset_peer_connections(database):
    # A backup records old transport status, not live sessions. Clear it offline
    # so peers must reconnect before the API can report them as connected.
    if run('docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECTS['netbird'][0],
           '--filter', 'label=com.docker.compose.service=netbird-server').strip():
        raise ValueError('Stop restored NetBird before clearing connection flags')
    with sqlite3.connect(database.as_uri() + '?mode=rw', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Restored NetBird SQLite integrity failed')
        count = db.execute('SELECT count(*) FROM peers WHERE peer_status_connected <> 0').fetchone()[0]
        db.execute('UPDATE peers SET peer_status_connected = 0 WHERE peer_status_connected <> 0')
        if db.execute('SELECT count(*) FROM peers WHERE peer_status_connected <> 0').fetchone()[0]:
            raise ValueError('Saved NetBird connection flags remain')
    return count


def saved_environment(path):
    return {key: value.strip().strip('"').strip("'") for line in path.read_text().splitlines()
            if line and not line.startswith('#') and '=' in line for key, value in [line.split('=', 1)]}


def hold_public_ports(configs):
    configs['netbird']['services']['netbird-server']['ports'] = ['127.0.0.1:3478:3478/udp']
    configs['app']['services']['nginx']['ports'] = ['127.0.0.1:80:80', '127.0.0.1:443:443']


def install(cfg):
    paths_absent([APP, AUTH, AUTH_DATA, VPN, MEDIA.parents[1], Path(cfg['runtime_age_path'])])
    if run('docker', 'ps', '-aq').strip() or run('docker', 'image', 'ls', '-aq').strip():
        raise ValueError('Cold-host recovery requires empty container and image stores')
    payload = ROOT / 'payload'
    # CI-backed recovery pulls the saved application artifact; legacy archives
    # retain their explicitly recorded source-build recovery method.
    for image in sorted(set(cfg['image_lock'].values())):
        run('docker', 'pull', '--platform', 'linux/amd64', image, timeout=900)
    run('git', 'clone', '--branch', 'recovery', '--single-branch', str(ROOT / 'staff.bundle'), str(APP))
    run('git', '-C', str(APP), 'checkout', '--detach', cfg['staff_revision'])
    if run('git', '-C', str(APP), 'rev-parse', 'HEAD').decode().strip() != cfg['staff_revision']:
        raise ValueError('Wrong staff source revision')
    for path in [APP, *APP.rglob('*')]:
        if not path.is_symlink() and '.git' not in path.relative_to(APP).parts:
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    receipt = cfg.get('application_image')
    if receipt is not None:
        spec = importlib.util.spec_from_file_location('application_artifact', ROOT / 'artifact.py')
        artifact = importlib.util.module_from_spec(spec); spec.loader.exec_module(artifact)
        receipt = artifact.verify_saved(receipt, cfg['staff_revision'])
        cfg['application_image'] = receipt
    else:
        env = saved_environment(payload / 'app-config/.env')
        keys = ['PUBLIC_HCAPTCHA_SITE_KEY', 'PUBLIC_VAPID_PUBLIC_KEY', 'PUBLIC_SITE_ORIGIN',
                'STAFF_OIDC_ISSUER', 'STAFF_OIDC_CLIENT_ID', 'STAFF_OIDC_REDIRECT_URI',
                'STAFF_OIDC_POST_LOGOUT_REDIRECT_URI', 'STAFF_OIDC_SCOPES']
        command = ['docker', 'build', '--no-cache', '--pull', '--platform', 'linux/amd64',
                   '--label', 'org.opencontainers.image.revision=' + cfg['staff_revision'],
                   '-t', 'hou1-restored-staff:' + cfg['staff_revision'], '-f', str(ROOT / 'Dockerfile.recovery')]
        for key in keys:
            command += ['--build-arg', key]
        run(*command, str(APP), env={**os.environ, **{key: env.get(key, '') for key in keys}}, timeout=1200)
    # Runtime secret files never enter the application build context.
    for name in ['.env', 'docker-compose.override.yml']:
        shutil.copyfile(payload / 'app-config' / name, APP / name)
        (APP / name).chmod(0o600)
    if receipt is not None:
        (APP / 'application-image.json').write_text(json.dumps(receipt, indent=2) + '\n')
        (APP / 'application-image.json').chmod(0o644)
    certs = APP / 'certbot/conf'
    certs.parent.mkdir(mode=0o700, exist_ok=True)
    if certs.exists():
        shutil.rmtree(certs)  # Only source checkout files; host destinations were absent.
    copy_tree(payload / 'app-config/certbot/conf', certs)
    (APP / 'certbot/www').mkdir(mode=0o755, exist_ok=True)
    (APP / 'nginx-logs').mkdir(mode=0o755, exist_ok=True)
    if (APP / 'database').exists():
        shutil.rmtree(APP / 'database')  # Only the checked-out source fixture, never a host database.
    copy_tree(payload / 'cms/database', APP / 'database', 100, 102)
    MEDIA.parent.mkdir(parents=True, mode=0o750)
    copy_tree(payload / 'cms/media', MEDIA, 100, 102)
    AUTH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    AUTH.mkdir(mode=0o700)
    for name in ['authentik.env', 'oidc-clients.env', 'secrets.enc.yaml']:
        shutil.copyfile(payload / 'authentik' / name, AUTH / name)
        (AUTH / name).chmod(0o600)
    AUTH_DATA.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
    AUTH_DATA.mkdir(mode=0o750)
    for name in ['media', 'templates', 'certs', 'config']:
        copy_tree(payload / 'authentik-files' / name, AUTH_DATA / name, 1000, 1000)
    (AUTH_DATA / 'postgres').mkdir(mode=0o700)
    os.chown(AUTH_DATA / 'postgres', 70, 70)
    copy_tree(payload / 'netbird-files', VPN)
    key = Path(cfg['runtime_age_path'])
    key.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copyfile(payload / 'host-recovery/0', key)
    key.chmod(0o600)
    configs = {'auth': json.loads((ROOT / 'auth.compose.json').read_text()),
               'netbird': json.loads((ROOT / 'netbird.compose.json').read_text()),
               'app': json.loads((ROOT / 'app.compose.json').read_text())}
    # Docker published ports bypass UFW. Bind to loopback until the explicit
    # publication checkpoint; do not use the host firewall as the sole guard.
    hold_public_ports(configs)
    for project, config in configs.items():
        directory = PROJECTS[project][1]
        filename = 'docker-compose.yml' if project == 'app' else 'compose.yaml'
        (directory / filename).write_text(json.dumps(config, indent=2) + '\n')
        (directory / filename).chmod(0o600)
    (VPN / 'ingress').mkdir(mode=0o755)
    for name in ['netbird.conf', 'authentik-test.conf']:
        shutil.copyfile(ROOT / name, VPN / 'ingress' / name)
    (VPN / 'nginx-include.conf').write_text('include /etc/nginx/netbird/*.conf;\n')
    run('docker', 'network', 'create', 'hou1-authentik-test')
    run('docker', 'network', 'create', '--subnet', '172.30.80.0/29', '--ip-range', '172.30.80.4/30', 'hou1-netbird-ingress')
    run('ufw', '--force', 'reset')
    run('ufw', 'default', 'deny', 'incoming')
    run('ufw', 'default', 'allow', 'outgoing')
    run('ufw', 'allow', 'from', cfg['bootstrap_cidr'], 'to', 'any', 'port', '22', 'proto', 'tcp')
    run('ufw', '--force', 'enable')
    isolation = {key: cfg[key] for key in ['environment', 'deny_cidrs', 'test_addresses', 'allow_local_cidrs', 'allow_update_cidrs']}
    (ROOT / 'isolation.json').write_text(json.dumps(isolation) + '\n')
    run('python3', str(ROOT / 'isolation.py'), str(ROOT / 'isolation.json'))
    return configs


def restore(cfg):
    started = time.monotonic()
    install(cfg)
    for project in PROJECTS:
        dc(project, 'config', '--quiet')
    dc('auth', 'up', '-d', '--pull', 'never', 'postgresql')
    wait_database()
    if sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'") != '0':
        raise ValueError('Never import a recovery dump into an existing schema')
    dump = (ROOT / 'payload/authentik/postgresql.sql').read_bytes()
    dc('auth', 'exec', '-T', 'postgresql', 'psql', '-h', '127.0.0.1', '-X', '-v', 'ON_ERROR_STOP=1',
       '-U', 'authentik', '-d', 'authentik', input=dump)
    tables = int(sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"))
    if tables < 1:
        raise ValueError('Empty recovered PostgreSQL schema')
    revoke_sessions(APP / 'database/forms.db')
    stale_connections = reset_peer_connections(VPN / 'data/store.db')
    dc('auth', 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '900', 'authentik-server', 'authentik-worker', timeout=1000)
    dc('netbird', 'up', '-d', '--pull', 'never', 'netbird-server', 'dashboard')
    dc('app', 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '180', 'astro-app', 'nginx')
    dc('app', 'exec', '-T', 'nginx', 'nginx', '-t')
    report = dict(passed=True, environment='test', staff_revision=cfg['staff_revision'], postgres_tables=tables,
                  sessions_revoked_before_writers=True, identity_bootstrap_run=False, cms_seed_run=False,
                  netbird_connection_flags_reset_before_server=True,
                  netbird_stale_connections_cleared=stale_connections,
                  cold_registry_pulls=True, cold_application_build='application_image' not in cfg,
                  application_image=cfg.get('application_image'), ports_bound_to_loopback=True,
                  restore_seconds=round(time.monotonic() - started, 3),
                  next='Reconcile access changes, explicitly publish, replace only the lost HOU1 peer, then finalize VPN/security/monitoring/backups')
    write('restore-report.json', report)
    for name in ['payload', 'staff.bundle', 'bundle.tar']:
        path = ROOT / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    return report


def publish(cfg):
    if not json.loads((ROOT / 'restore-report.json').read_text())['passed']:
        raise ValueError('Restore checkpoint has not passed')
    changes = []
    for project, service, before, after in [
        ('netbird', 'netbird-server', ['127.0.0.1:3478:3478/udp'], ['3478:3478/udp']),
        ('app', 'nginx', ['127.0.0.1:80:80', '127.0.0.1:443:443'], ['80:80', '443:443']),
    ]:
        file = PROJECTS[project][1] / ('docker-compose.yml' if project == 'app' else 'compose.yaml')
        config = json.loads(file.read_text())
        if config['services'][service]['ports'] != before:
            raise ValueError('Unexpected publication baseline')
        config['services'][service]['ports'] = after
        changes.append((file, config))
    for file, config in changes:
        file.write_text(json.dumps(config, indent=2) + '\n')
    for port in ['80', '443']:
        run('ufw', 'allow', port + '/tcp')
    run('ufw', 'allow', '3478/udp')
    dc('netbird', 'up', '-d', '--pull', 'never', 'netbird-server')
    dc('app', 'up', '-d', '--pull', 'never', '--no-deps', 'nginx')
    dc('app', 'exec', '-T', 'nginx', 'nginx', '-t')
    report = {'passed': True, 'environment': 'test', 'ports_published': True,
              'access_reconciliation_operator_asserted': True, 'host_enrollment_pending': True}
    write('publish-report.json', report)
    return report


def main(action):
    os.umask(0o077)
    cfg = json.loads((ROOT / 'request.json').read_text())
    if cfg['environment'] != 'test' or ROOT != RECOVERY_BASE / cfg['id']:
        raise ValueError('Wrong recovery root or environment')
    if (ROOT / 'first-error.json').exists() or (ROOT / (action + '-report.json')).exists():
        raise ValueError('Do not resume an earlier or completed attempt')
    try:
        return {'restore': restore, 'publish': publish}[action](cfg)
    except Exception as error:
        write('first-error.json', {'action': action, 'error_type': type(error).__name__,
                                  'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        # Stop the ingress and restored writers; retain PostgreSQL and diagnostics.
        for project, services in [('app', ['nginx', 'astro-app']), ('auth', ['authentik-server', 'authentik-worker']), ('netbird', ['netbird-server', 'dashboard'])]:
            if PROJECTS[project][1].exists():
                try:
                    dc(project, 'stop', '--timeout', '30', *services, timeout=100)
                except Exception:
                    pass
        raise RuntimeError('Replacement recovery stopped; preserve evidence and use a fresh host after correction') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['restore', 'publish'], nargs='?', default='restore')
    try:
        print(json.dumps(main(parser.parse_args().action)))
    except Exception:
        parser.exit(1, 'Replacement recovery stopped; inspect protected diagnostics. No automatic retry.\n')
