#!/usr/bin/env python3
"""Prepare a test replacement-host bundle without touching either VPS."""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

from jinja2 import Environment, StrictUndefined
import yaml

HERE = Path(__file__).resolve().parent
ROLES = HERE.parents[1]
spec = importlib.util.spec_from_file_location('drill_prepare', ROLES / 'recovery_drill/files/prepare.py')
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)
REPOSITORIES = {'postgresql': 'postgres', 'authentik-server': 'ghcr.io/goauthentik/server',
                'authentik-worker': 'ghcr.io/goauthentik/server',
                'netbird-server': 'netbirdio/netbird-server', 'dashboard': 'netbirdio/dashboard',
                'nginx': 'nginx', 'node': 'node'}


def validate(request, metadata, lock, app, netbird):
    if (request['environment'] != 'test' or metadata['environment'] != 'test'
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]{5,39}', request['id'])
            or not re.fullmatch(r'[a-f0-9]{40}', request['staff_revision'])
            or metadata['staff_revision'] != request['staff_revision']
            or metadata['recovery_files'] != [request['runtime_age_path']]
            or not re.fullmatch(r'/root/\.config/sops/age/[a-z0-9-]*test[a-z0-9-]*\.key', request['runtime_age_path'])):
        raise ValueError('Require matching immutable test recovery and host-only runtime key')
    suffix = request['test_suffix']
    if not re.fullmatch(r'[a-z0-9.-]+', suffix) or not re.search(r'(^|[.-])test([.-]|$)', suffix):
        raise ValueError('Require the isolated test suffix')
    subnet = ipaddress.ip_network(request['netbird_subnet'], strict=True)
    source = ipaddress.ip_network(request['bootstrap_cidr'], strict=True)
    if (subnet.version != 4 or subnet.prefixlen < 16 or str(subnet) == '100.90.0.0/16'
            or source.version != 4 or source.prefixlen != 32 or not source.network_address.is_global
            or app.get('staff_vpn_subnet') != str(subnet)
            or ipaddress.ip_address(app['HOU1_VPN_IP']) not in subnet):
        raise ValueError('Require the saved test VPN boundary and one public bootstrap source')
    if set(lock) != set(REPOSITORIES):
        raise ValueError('Require all runtime and build image digests')
    for service, repo in REPOSITORIES.items():
        if not re.fullmatch(re.escape(repo) + r'@sha256:[0-9a-f]{64}', lock[service]):
            raise ValueError('Unexpected image repository or mutable image reference')
    if lock['authentik-server'] != lock['authentik-worker']:
        raise ValueError('Authentik server and worker must use the same artifact')
    origins = {key: drill.isolated_origin(value, suffix) for key, value in {
        'auth': app['STAFF_OIDC_ISSUER'], 'staff': app['STAFF_OIDC_REDIRECT_URI'],
        'public': app['PUBLIC_SITE_ORIGIN'], 'netbird': netbird['server']['exposedAddress'],
    }.items()}
    if len(set(origins.values())) != 4 or netbird['server'].get('disableGeoliteUpdate') is not True:
        raise ValueError('Require separate test origins and offline geolocation startup')
    if not request['deny_cidrs'] or not request['test_addresses']:
        raise ValueError('Require the existing production isolation inputs')
    for key in ['deny_cidrs', 'allow_local_cidrs', 'allow_update_cidrs']:
        for value in request[key]:
            ipaddress.ip_network(value, strict=True)
    return origins


def prepare(request, payload, source_bundle, image_lock, output):
    os.umask(0o077)
    payload, output = Path(payload).resolve(), Path(output)
    metadata = json.loads((payload / 'recovery.json').read_text())
    lock = json.loads(Path(image_lock).read_text())
    app = drill.environment(payload / 'app-config/.env')
    netbird = yaml.safe_load((payload / 'netbird-files/config.yaml').read_text())
    origins = validate(request, metadata, lock, app, netbird)
    auth = yaml.safe_load((payload / 'authentik/compose.yaml').read_text())
    vpn = yaml.safe_load((payload / 'netbird-files/compose.yaml').read_text())
    web = yaml.safe_load((payload / 'app-config/docker-compose.yml').read_text())
    if (set(auth['services']) != {'postgresql', 'authentik-server', 'authentik-worker'}
            or set(vpn['services']) != {'netbird-server', 'dashboard'}
            or vpn['services']['netbird-server']['image'] != 'netbirdio/netbird-server:0.82.0'
            or set(web['services']) != {'astro-app', 'nginx', 'uptime-kuma'}):
        raise ValueError('Unexpected saved services or NetBird release')
    for config in [auth, vpn, web]:
        for service in config['services'].values():
            if any(service.get(key) for key in ['privileged', 'network_mode', 'pid', 'devices', 'cap_add', 'volumes_from', 'extends']):
                raise ValueError('Unexpected saved service privileges')
    # All restored mounts must be within the original fixed test roots. No volumes
    # from a saved archive may attach a host socket, production tree or device.
    allowed = ['/opt/pearlandpaws-test/', '/opt/hou1-netbird/', '/var/lib/hou1-authentik/test/',
               '/var/lib/pearlandpaws/test/']
    roots = [('/opt/hou1-authentik/test', auth), ('/opt/hou1-netbird', vpn), ('/opt/pearlandpaws-test', web)]
    override = yaml.safe_load((payload / 'app-config/docker-compose.override.yml').read_text())
    if not set(override.get('services', {})).issubset({'astro-app', 'nginx'}):
        raise ValueError('Unexpected override service')
    for root, config in roots + [('/opt/pearlandpaws-test', override)]:
        for service in config.get('services', {}).values():
            if any(service.get(key) for key in ['privileged', 'network_mode', 'pid', 'devices', 'cap_add', 'volumes_from', 'extends']):
                raise ValueError('Unexpected service override privileges')
            for mount in service.get('volumes', []):
                if not isinstance(mount, str):
                    raise ValueError('Unexpected saved mount format')
                source = mount.split(':', 1)[0]
                if source == 'uptime-kuma-data':
                    continue  # Not started by this recovery workflow.
                resolved = str(Path(root, source).resolve())
                if not any(resolved.startswith(prefix) for prefix in allowed):
                    raise ValueError('Mount escapes the declared test roots')
    if any(service.get('ports') for service in auth['services'].values()):
        raise ValueError('Authentik and PostgreSQL must not publish ports')
    if (vpn['services']['netbird-server'].get('ports') != ['3478:3478/udp']
            or vpn['services']['dashboard'].get('ports')
            or web['services']['astro-app'].get('ports')
            or web['services']['nginx'].get('ports') != ['80:80', '443:443']
            or any(service.get('ports') for service in override['services'].values())):
        raise ValueError('Unexpected saved public service ports')
    if output.exists():
        raise ValueError('Never reuse a staging directory')
    output.mkdir(parents=True, mode=0o700)
    try:
        repository = output / 'source.git'
        subprocess.run(['git', 'clone', '--bare', '--branch', 'recovery', str(source_bundle), str(repository)], capture_output=True, check=True)
        def git(*args):
            return subprocess.check_output(['git', '--git-dir=' + str(repository), *args], stderr=subprocess.DEVNULL)
        if git('rev-parse', 'refs/heads/recovery').decode().strip() != request['staff_revision']:
            raise ValueError('Source bundle does not match the saved revision')
        dockerfile = git('show', request['staff_revision'] + ':astro-app/Dockerfile').decode()
        if re.findall(r'^FROM (\S+)', dockerfile, re.M) != ['node:24.22.2', 'node:24.22.2']:
            raise ValueError('Review a changed build base before replacement recovery')
        (output / 'Dockerfile.recovery').write_text(dockerfile.replace('FROM node:24.22.2 ', 'FROM ' + lock['node'] + ' '))
        shutil.rmtree(repository)
        for config in [auth, vpn, web]:
            for name, service in config['services'].items():
                if name in lock:
                    service['image'] = lock[name]
        web['services']['astro-app'].pop('build', None)
        web['services']['astro-app']['image'] = 'hou1-restored-staff:' + request['staff_revision']
        for name, value in [('auth.compose.json', auth), ('netbird.compose.json', vpn), ('app.compose.json', web)]:
            (output / name).write_text(json.dumps(value, indent=2) + '\n')
        values = dict(netbird_domain=origins['netbird'].removeprefix('https://'),
                      authentik_domain=origins['auth'].removeprefix('https://'),
                      authentik_environment='test', netbird_subnet=request['netbird_subnet'])
        certs = payload / 'app-config/certbot/conf'
        engine = Environment(undefined=StrictUndefined)
        mapping = {}
        for role, name in [('netbird_control_plane', 'netbird.conf'), ('authentik', 'authentik-test.conf')]:
            template = (ROLES / role / 'templates/nginx.conf.j2').read_text()
            rendered = engine.from_string(template).render(**values)
            rendered, found = drill.certificates.rewrite_proxy(rendered, certs)
            mapping.update(found)
            (output / name).write_text(rendered)
        for key in ['PRIMARY_HOSTNAMES', 'SECONDARY_HOSTNAMES', 'STAFF_HOSTNAME']:
            for host in app[key].split():
                drill.isolated_origin('https://' + host, request['test_suffix'])
                mapping[host] = drill.certificates.select_lineage(certs, host)
        for host_key, cert_key in [('PRIMARY_HOSTNAMES', 'PRIMARY_CERT_DIR'), ('SECONDARY_HOSTNAMES', 'SECONDARY_CERT_DIR'), ('STAFF_HOSTNAME', 'STAFF_CERT_DIR')]:
            for host in app[host_key].split():
                if mapping[host] != app[cert_key]:
                    raise ValueError('Saved Nginx environment and certificate lineage differ')
        cfg = {**request, 'origins': origins, 'image_lock': lock}
        (output / 'request.json').write_text(json.dumps(cfg, indent=2) + '\n')
        (output / 'certificate-report.json').write_text(json.dumps(mapping, indent=2) + '\n')
        shutil.copyfile(source_bundle, output / 'staff.bundle')
        shutil.copyfile(HERE / 'restore.py', output / 'restore.py')
        shutil.copyfile(ROLES / 'rehearsal_gates/files/isolation.py', output / 'isolation.py')
        with tarfile.open(output / 'bundle.tar', 'w') as archive:
            archive.add(payload, arcname='payload')
            for path in sorted(output.iterdir()):
                if path.name != 'bundle.tar':
                    archive.add(path, arcname=path.name)
        return {'prepared': True, 'staff_revision': request['staff_revision'], 'services_published': False}
    except Exception:
        shutil.rmtree(output)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['request', 'payload', 'source-bundle', 'image-lock', 'output']:
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(json.loads(Path(args.request).read_text()), args.payload, args.source_bundle, args.image_lock, args.output)))
