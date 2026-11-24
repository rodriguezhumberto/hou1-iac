#!/usr/bin/env python3
"""Restore a fresh host once; leave published ports on loopback for review."""
import argparse
import importlib.util
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parent
RECOVERY_BASE = Path('/var/lib/hou1-host-recovery')
APP = AUTH = AUTH_DATA = VPN = MEDIA = None
PROJECTS = {}
LAB_DNS_POLICY = Path('/etc/hou-lab-dns-firewall.json')
LAB_DNS_HELPER = '/usr/local/libexec/hou-lab-dns-firewall'

spec = importlib.util.spec_from_file_location('host_recovery_contract', Path(__file__).resolve().with_name('contract.py'))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def configure(cfg):
    global APP, AUTH, AUTH_DATA, VPN, MEDIA, PROJECTS
    contract.isolation(cfg)
    deployment = contract.layout(cfg)
    paths = deployment['paths']
    APP, AUTH, AUTH_DATA, VPN = [Path(paths[key]) for key in ('app', 'auth', 'auth_data', 'netbird')]
    MEDIA = Path(paths['cms']) / 'media'
    PROJECTS = {key: (deployment['projects'][key], path)
                for key, path in [('app', APP), ('auth', AUTH), ('netbird', VPN)]}


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


def copy_certificates(environment, source, destination):
    # Supported Nginx loads keys as root. Normalize to root:root, directories
    # 0750/files 0640; archive numeric owners are deliberately not trusted.
    if environment == 'test':
        copy_tree(source, destination)
    else:
        # Keep certificate symlink lineages, without reinstalling old ACME
        # accounts, DNS grants, renewal configuration or hooks.
        destination.mkdir(mode=0o750)
        for name in ('live', 'archive'):
            if (source / name).is_dir():
                copy_tree(source / name, destination / name)


def copy_netbird(source, destination, secrets_file):
    copy_tree(source, destination)
    # prepare.py validates against config.yaml, reconstructing legacy points
    # without generating or rotating any encryption material.
    shutil.copyfile(secrets_file, destination / 'secrets.json')
    os.chown(destination / 'secrets.json', 0, 0)
    (destination / 'secrets.json').chmod(0o600)


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


def managed_override(config):
    """Use the app_compose ownership format for this validated recovery rendering."""
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return ('# Managed by hou1-iac. Automatically read by every existing docker compose call.\n'
            '# Generated app_compose SHA256: ' + fingerprint + '\n' + json.dumps(config, indent=2) + '\n')


def hold_public_ports(configs):
    configs['netbird']['services']['netbird-server']['ports'] = ['127.0.0.1:3478:3478/udp']
    configs['app']['services']['nginx']['ports'] = ['127.0.0.1:80:80', '127.0.0.1:443:443']


def install(cfg):
    paths_absent([APP, AUTH, AUTH_DATA, VPN, MEDIA.parent, Path(cfg['runtime_age_path'])])
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
        write('request.json', cfg)
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
    for name in ['.env']:
        shutil.copyfile(payload / 'app-config' / name, APP / name)
        (APP / name).chmod(0o600)
    override = ROOT / 'app.override.json'
    if override.exists():
        (APP / 'docker-compose.override.yml').write_text(managed_override(json.loads(override.read_text())))
    else:
        shutil.copyfile(payload / 'app-config/docker-compose.override.yml', APP / 'docker-compose.override.yml')
    (APP / 'docker-compose.override.yml').chmod(0o600)
    if receipt is not None:
        (APP / 'application-image.json').write_text(json.dumps(receipt, indent=2) + '\n')
        (APP / 'application-image.json').chmod(0o644)
    certs = APP / 'certbot/conf'
    certs.parent.mkdir(mode=0o700, exist_ok=True)
    if certs.exists():
        shutil.rmtree(certs)  # Only source checkout files; host destinations were absent.
    copy_certificates(cfg['environment'], payload / 'app-config/certbot/conf', certs)
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
    copy_netbird(payload / 'netbird-files', VPN, ROOT / 'netbird-secrets.json')
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
    for name in ['netbird.conf', 'authentik.conf']:
        shutil.copyfile(ROOT / name, VPN / 'ingress' / name)
    (VPN / 'nginx-include.conf').write_text('include /etc/nginx/netbird/*.conf;\n')
    networks = cfg['deployment']['networks']
    internal = ['--internal'] if cfg.get('quarantine', False) else []
    run('docker', 'network', 'create', *internal, networks['auth'])
    run('docker', 'network', 'create', *internal, '--subnet', networks['subnet'], '--ip-range', networks['dynamic_range'], networks['netbird'])
    app_network = configs['app'].get('networks', {}).get('app-network', {})
    if app_network.get('external'):
        run('docker', 'network', 'create', *internal, app_network['name'])
    run('ufw', '--force', 'reset')
    run('ufw', 'default', 'deny', 'incoming')
    run('ufw', 'default', 'allow', 'outgoing')
    run('ufw', 'allow', 'from', cfg['bootstrap_cidr'], 'to', 'any', 'port', '22', 'proto', 'tcp')
    run('ufw', '--force', 'enable')
    apply_test_isolation(cfg)
    return configs


def apply_test_isolation(cfg):
    mode = contract.isolation(cfg)
    if cfg['environment'] == 'test' and not cfg.get('quarantine', False) and mode == 'host':
        isolation = {key: cfg[key] for key in ['environment', 'deny_cidrs', 'test_addresses', 'allow_local_cidrs', 'allow_update_cidrs']}
        (ROOT / 'isolation.json').write_text(json.dumps(isolation) + '\n')
        run('python3', str(ROOT / 'isolation.py'), str(ROOT / 'isolation.json'))


def verify_quarantine(cfg):
    if not cfg.get('quarantine', False):
        return
    ids = set()
    for project in PROJECTS:
        ids.update(dc(project, 'ps', '--all', '--quiet').decode().split())
    if not ids:
        raise ValueError('No quarantined recovery services')
    containers = json.loads(run('docker', 'inspect', *sorted(ids)))
    network_ids = set()
    for item in containers:
        state = item['State']
        if not state['Running'] or state.get('Health', {}).get('Status', 'healthy') != 'healthy':
            raise ValueError('Quarantined recovery service is not ready')
        if item['HostConfig']['NetworkMode'] == 'host' or item['HostConfig']['Privileged']:
            raise ValueError('Quarantine cannot use host networking or privileges')
        for bindings in (item['HostConfig']['PortBindings'] or {}).values():
            if any(binding['HostIp'] != '127.0.0.1' for binding in bindings):
                raise ValueError('Quarantined ports must stay on loopback')
        attached = item['NetworkSettings']['Networks']
        if not attached:
            raise ValueError('Quarantined service has no declared network')
        network_ids.update(value['NetworkID'] for value in attached.values())
    networks = json.loads(run('docker', 'network', 'inspect', *sorted(network_ids)))
    if any(not item['Internal'] or not set(item.get('Containers', {})).issubset(ids) for item in networks):
        raise ValueError('Recovery network is external or shared with another workload')


def prepare_container_dns(cfg):
    """Create only restored services, then reconcile DNS before starting writers."""
    if cfg['environment'] != 'test' or cfg.get('quarantine', False) or not LAB_DNS_POLICY.exists():
        return
    required = set()
    for project, services in [('auth', ['postgresql', 'authentik-server', 'authentik-worker']),
                              ('netbird', ['netbird-server', 'dashboard']),
                              ('app', ['astro-app', 'nginx'])]:
        document = json.loads(dc(project, 'config', '--format', 'json'))
        for service in services:
            required.update(document['networks'][name]['name']
                            for name in document['services'][service]['networks'])
        dc(project, 'create', '--no-build', '--no-recreate', '--pull', 'never', *services)
    run(LAB_DNS_HELPER, '--require-networks', *sorted(required))


def restore(cfg):
    started = time.monotonic()
    install(cfg)
    for project in PROJECTS:
        dc(project, 'config', '--quiet')
    prepare_container_dns(cfg)
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
    verify_quarantine(cfg)
    report = dict(passed=True, environment=cfg['environment'], staff_revision=cfg['staff_revision'], postgres_tables=tables,
                  sessions_revoked_before_writers=True, identity_bootstrap_run=False, cms_seed_run=False,
                  netbird_connection_flags_reset_before_server=True,
                  netbird_stale_connections_cleared=stale_connections,
                  cold_registry_pulls=True, cold_application_build='application_image' not in cfg,
                  quarantine=cfg.get('quarantine', False),
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


def publication_write(path, value):
    """Persist publication intent/checkpoints before externally visible changes."""
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publication_firewall():
    """Read canonical UFW rules; comments distinguish this attempt's additions."""
    rules = []
    for line in run('ufw', 'show', 'added').decode().splitlines():
        fields = shlex.split(line)
        if fields[:2] != ['ufw', 'allow']:
            continue
        if fields[2:3] == ['in']:
            fields.pop(2)
        if len(fields) == 3:
            rules.append((fields[2], None))
        elif len(fields) == 5 and fields[3] == 'comment':
            rules.append((fields[2], fields[4]))
    return rules


def publication_identity(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def publication_cleanup(cfg):
    """Seal an interrupted publication, retaining the error and original intent."""
    if cfg.get('quarantine', False):
        raise ValueError('Quarantined replacement has no publication cleanup')
    path = ROOT / 'publication-intent.json'
    journal = json.loads(path.read_text())
    if journal.get('config_sha256') != publication_identity(cfg):
        raise ValueError('Publication cleanup request differs from the saved intent')
    if (ROOT / 'publish-report.json').exists():
        raise ValueError('Successful publication must not be rolled back as an interruption')
    expected = {'app': ('nginx', ['127.0.0.1:80:80', '127.0.0.1:443:443']),
                'netbird': ('netbird-server', ['127.0.0.1:3478:3478/udp'])}
    if (journal.get('schema') != 1 or set(journal.get('compose', {})) != set(expected)
            or journal.get('firewall_comment') != 'hou1-recovery-' + cfg['id']
            or not isinstance(journal.get('owned_firewall'), list)
            or len(set(journal['owned_firewall'])) != len(journal['owned_firewall'])
            or not set(journal['owned_firewall']).issubset({'80/tcp', '443/tcp', '3478/udp'})):
        raise ValueError('Invalid publication cleanup intent')
    for project, (service, ports) in expected.items():
        if json.loads(journal['compose'][project])['services'][service]['ports'] != ports:
            raise ValueError('Publication cleanup intent is not private')
    errors = []
    for project, service in [('app', 'nginx'), ('netbird', 'netbird-server')]:
        try:
            dc(project, 'stop', '--timeout', '30', service, timeout=100)
        except Exception:
            errors.append('stop-' + project)
    # Resolve destinations from the validated deployment, never from a journal
    # path supplied by the caller. Restore private bindings even if stop failed.
    for project, original in journal['compose'].items():
        try:
            file = PROJECTS[project][1] / ('docker-compose.yml' if project == 'app' else 'compose.yaml')
            publication_write(file, original)
        except Exception:
            errors.append('compose-' + project)
    for port in journal['owned_firewall']:
        try:
            matches = [(rule, comment) for rule, comment in publication_firewall() if rule == port]
            if not matches:
                continue
            if matches != [(port, journal['firewall_comment'])]:
                raise ValueError('Publication firewall ownership changed')
            run('ufw', '--force', 'delete', 'allow', port, 'comment', journal['firewall_comment'])
        except Exception:
            errors.append('firewall-' + port)
    journal['cleanup'] = {'passed': not errors, 'failed_steps': errors}
    publication_write(path, json.dumps(journal, indent=2) + '\n')
    if not (ROOT / 'first-error.json').exists():
        publication_write(ROOT / 'first-error.json', json.dumps({
            'action': 'publish', 'error_type': 'InterruptedPublication',
            'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'publication_cleanup_passed': not errors}, indent=2) + '\n')
    if errors:
        raise RuntimeError('Publication cleanup incomplete; retain intent and retry cleanup only')
    return {'passed': True, 'private_bindings_restored': True, 'publication_retry_allowed': False}


def recover_publication(cfg):
    with (ROOT / 'publication.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return publication_cleanup(cfg)


def publish(cfg):
    if cfg.get('quarantine', False):
        raise ValueError('Quarantined replacement cannot be published; use a separate reviewed handoff')
    with (ROOT / 'publication.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return publish_locked(cfg)


def publish_locked(cfg):
    if cfg.get('quarantine', False):
        raise ValueError('Quarantined replacement cannot be published; use a separate reviewed handoff')
    if (ROOT / 'publication-intent.json').exists():
        publication_cleanup(cfg)
        raise ValueError('Interrupted publication sealed; retain evidence and use a fresh attempt')
    report = json.loads((ROOT / 'restore-report.json').read_text())
    if (not report['passed'] or (cfg['environment'] == 'production' and
            (report.get('environment') != 'production' or report.get('staff_revision') != cfg['staff_revision']
             or report.get('application_image') != cfg.get('application_image')
             or report.get('ports_bound_to_loopback') is not True
             or report.get('sessions_revoked_before_writers') is not True))):
        raise ValueError('Restore checkpoint has not passed')
    changes, originals = [], {}
    for project, service, before, after in [
        ('netbird', 'netbird-server', ['127.0.0.1:3478:3478/udp'], ['3478:3478/udp']),
        ('app', 'nginx', ['127.0.0.1:80:80', '127.0.0.1:443:443'], ['80:80', '443:443']),
    ]:
        file = PROJECTS[project][1] / ('docker-compose.yml' if project == 'app' else 'compose.yaml')
        originals[project] = file.read_text()
        config = json.loads(originals[project])
        if config['services'][service]['ports'] != before:
            raise ValueError('Unexpected publication baseline')
        config['services'][service]['ports'] = after
        changes.append((file, config))
    existing = {port for port, _ in publication_firewall()}
    journal = {'schema': 1, 'config_sha256': publication_identity(cfg), 'compose': originals,
               'firewall_comment': 'hou1-recovery-' + cfg['id'],
               'owned_firewall': [port for port in ['80/tcp', '443/tcp', '3478/udp'] if port not in existing]}
    publication_write(ROOT / 'publication-intent.json', json.dumps(journal, indent=2) + '\n')
    try:
        for file, config in changes:
            publication_write(file, json.dumps(config, indent=2) + '\n')
        for port in journal['owned_firewall']:
            run('ufw', 'allow', port, 'comment', journal['firewall_comment'])
        dc('netbird', 'up', '-d', '--pull', 'never', 'netbird-server')
        dc('app', 'up', '-d', '--pull', 'never', '--no-deps', 'nginx')
        dc('app', 'exec', '-T', 'nginx', 'nginx', '-t')
        report = {'passed': True, 'environment': cfg['environment'], 'ports_published': True,
                  'access_reconciliation_operator_asserted': True, 'host_enrollment_pending': True}
        publication_write(ROOT / 'publish-report.json', json.dumps(report, indent=2) + '\n')
        return report
    except Exception:
        publication_cleanup(cfg)
        raise


def main(action):
    os.umask(0o077)
    cfg = json.loads((ROOT / 'request.json').read_text())
    if cfg['environment'] not in ('test', 'production') or ROOT != RECOVERY_BASE / cfg['id']:
        raise ValueError('Wrong recovery root or environment')
    if action != 'recover-publication' and ((ROOT / 'first-error.json').exists() or (ROOT / (action + '-report.json')).exists()):
        raise ValueError('Do not resume an earlier or completed attempt')
    # A rejected action must leave the healthy quarantine and its evidence intact.
    if action == 'publish' and cfg.get('quarantine', False):
        raise ValueError('Quarantined replacement cannot be published; use a separate reviewed handoff')
    configure(cfg)
    if cfg['environment'] == 'production' and not cfg.get('application_image'):
        raise ValueError('Production recovery requires a CI artifact')
    if action == 'recover-publication':
        return recover_publication(cfg)
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
    parser.add_argument('action', choices=['restore', 'publish', 'recover-publication'], nargs='?', default='restore')
    try:
        print(json.dumps(main(parser.parse_args().action)))
    except Exception:
        parser.exit(1, 'Replacement recovery stopped; inspect protected diagnostics. No automatic retry.\n')
