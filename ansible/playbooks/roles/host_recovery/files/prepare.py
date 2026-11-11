#!/usr/bin/env python3
"""Prepare an environment-bound replacement-host bundle without touching either VPS."""
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
spec = importlib.util.spec_from_file_location('application_artifact', ROLES / 'application_image/files/artifact.py')
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)
spec = importlib.util.spec_from_file_location('host_recovery_contract', HERE / 'contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
REPOSITORIES = {'postgresql': 'postgres', 'authentik-server': 'ghcr.io/goauthentik/server',
                'authentik-worker': 'ghcr.io/goauthentik/server',
                'netbird-server': 'netbirdio/netbird-server', 'dashboard': 'netbirdio/dashboard',
                'nginx': 'nginx', 'node': 'node'}


def validate(request, metadata, lock, app, netbird):
    contract.layout(request)
    production = request['environment'] == 'production'
    if (request['environment'] not in ('test', 'production') or metadata['environment'] != request['environment']
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]{5,39}', request['id'])
            or not re.fullmatch(r'[a-f0-9]{40}', request['staff_revision'])
            or metadata['staff_revision'] != request['staff_revision']
            or metadata['recovery_files'] != [request['runtime_age_path']]
            or not re.fullmatch(r'/root/\.config/sops/age/[a-z0-9-]+\.key', request['runtime_age_path'])):
        raise ValueError('Require matching immutable recovery and host-only runtime key')
    if not production:
        suffix = request['test_suffix']
        if (not re.fullmatch(r'[a-z0-9.-]+', suffix) or not re.search(r'(^|[.-])test([.-]|$)', suffix)
                or 'test' not in Path(request['runtime_age_path']).name):
            raise ValueError('Require the isolated test suffix and test runtime key')
    subnet = ipaddress.ip_network(request['netbird_subnet'], strict=True)
    source = ipaddress.ip_network(request['bootstrap_cidr'], strict=True)
    if (subnet.version != 4 or subnet.prefixlen < 16 or not subnet.subnet_of(ipaddress.ip_network('100.64.0.0/10'))
            or (not production and str(subnet) == '100.90.0.0/16')
            or source.version != 4 or source.prefixlen != 32 or not source.network_address.is_global
            or app.get('staff_vpn_subnet') != str(subnet)
            or app['HOU1_VPN_IP'] != request['server_ip']
            or ipaddress.ip_address(request['server_ip']) not in subnet):
        raise ValueError('Require the saved VPN boundary and one public bootstrap source')
    repositories = dict(REPOSITORIES)
    receipt = metadata.get('application_image')
    if production and receipt is None:
        raise ValueError('Production recovery requires a saved CI application image receipt')
    if receipt is not None:
        artifact.validate_receipt(receipt, request['staff_revision'])
        repositories.pop('node')
        repositories['astro-app'] = artifact.REPOSITORY
        if lock.get('astro-app') != receipt['image']:
            raise ValueError('Image lock must preserve the saved application artifact')
    if set(lock) != set(repositories):
        raise ValueError('Require all runtime image digests (and the build base only for legacy recovery)')
    for service, repo in repositories.items():
        if not re.fullmatch(re.escape(repo) + r'@sha256:[0-9a-f]{64}', lock[service]):
            raise ValueError('Unexpected image repository or mutable image reference')
    if lock['authentik-server'] != lock['authentik-worker']:
        raise ValueError('Authentik server and worker must use the same artifact')
    saved = {
        'auth': app['STAFF_OIDC_ISSUER'], 'staff': app['STAFF_OIDC_REDIRECT_URI'],
        'public': app['PUBLIC_SITE_ORIGIN'], 'netbird': netbird['server']['exposedAddress'],
    }
    origins = contract.endpoints(request, saved)
    if not production:
        for value in saved.values():
            drill.isolated_origin(value, suffix)
    if len(set(origins.values())) != 4:
        raise ValueError('Require separate environment origins')
    # The isolated rehearsal cannot depend on public geolocation downloads.
    # Production retains its saved updater setting and normal outbound policy.
    if not production and netbird['server'].get('disableGeoliteUpdate') is not True:
        raise ValueError('Require offline geolocation startup for test recovery')
    if not production and (not request['deny_cidrs'] or not request['test_addresses']):
        raise ValueError('Require the existing production isolation inputs')
    for key in ['deny_cidrs', 'allow_local_cidrs', 'allow_update_cidrs']:
        for value in request.get(key, []):
            ipaddress.ip_network(value, strict=True)
    return origins


def validate_compose(request, auth, vpn, web, override):
    """Saved Compose must not grant execution or attach unrelated external state."""
    deployment = request['deployment']
    networks = deployment['networks']
    for key, config in [('auth', auth), ('netbird', vpn), ('app', web), ('app', override)]:
        if config.get('name', deployment['projects'][key]) != deployment['projects'][key]:
            raise ValueError('Saved Compose project differs from the recovery contract')
        if any(config.get(field) for field in ('include', 'secrets', 'configs')):
            raise ValueError('Unexpected saved Compose resource')
        for network in config.get('networks', {}).values():
            if network.get('external') and network.get('name') not in (networks['auth'], networks['netbird']):
                raise ValueError('Unexpected external recovery network')
    for config in [auth, vpn, web, override]:
        if any(config.get(field) for field in ('include', 'secrets', 'configs')):
            raise ValueError('Unexpected Compose override resource')
        for service in config.get('services', {}).values():
            for item in service.get('env_file', []):
                if item not in ('./authentik.env', './dashboard.env', '.env', './.env'):
                    raise ValueError('Unexpected saved environment file')
            if service.get('build') and service is not web['services']['astro-app']:
                raise ValueError('Only the legacy application build may exist')
    if any(override.get(field) for field in ('volumes', 'name')):
        raise ValueError('Override must not replace project resources')
    for service in override.get('services', {}).values():
        if not set(service).issubset({'volumes', 'networks'}):
            raise ValueError('Recovery overrides may change only declared storage and ingress attachments')
    for network in override.get('networks', {}).values():
        if not network.get('external') or network.get('name') not in (networks['auth'], networks['netbird']):
            raise ValueError('Override must declare only the selected external ingress networks')

    if request['environment'] == 'production':
        if (auth.get('networks', {}).get('backend') != {'name': deployment['projects']['auth'] + '-backend', 'internal': True}
                or auth.get('networks', {}).get('ingress') != {'name': networks['auth'], 'external': True}
                or vpn.get('networks', {}).get('ingress') != {'name': networks['netbird'], 'external': True}
                or override.get('networks', {}).get('authentik-ingress') != {'name': networks['auth'], 'external': True}
                or override.get('networks', {}).get('netbird-ingress') != {'name': networks['netbird'], 'external': True}
                or override['services'].get('nginx', {}).get('networks', {}).get('netbird-ingress') != {'ipv4_address': networks['proxy_ip']}):
            raise ValueError('Saved ingress and internal database networks differ from the selected deployment')
        aliases = auth['services']['authentik-server'].get('networks', {}).get('ingress', {}).get('aliases', [])
        if aliases != ['authentik-server-prod']:
            raise ValueError('Saved identity alias differs from the selected production profile')


def prepare(request, payload, source_bundle, image_lock, output):
    os.umask(0o077)
    payload, output = Path(payload).resolve(), Path(output)
    metadata = json.loads((payload / 'recovery.json').read_text())
    lock = json.loads(Path(image_lock).read_text())
    app = drill.environment(payload / 'app-config/.env')
    netbird = yaml.safe_load((payload / 'netbird-files/config.yaml').read_text())
    origins = validate(request, metadata, lock, app, netbird)
    receipt = artifact.saved(payload, request['staff_revision'])
    auth = yaml.safe_load((payload / 'authentik/compose.yaml').read_text())
    vpn = yaml.safe_load((payload / 'netbird-files/compose.yaml').read_text())
    web = yaml.safe_load((payload / 'app-config/docker-compose.yml').read_text())
    if (set(auth['services']) != {'postgresql', 'authentik-server', 'authentik-worker'}
            or set(vpn['services']) != {'netbird-server', 'dashboard'}
            or vpn['services']['netbird-server']['image'] not in (
                'netbirdio/netbird-server:0.82.0', lock['netbird-server'])
            or set(web['services']) != {'astro-app', 'nginx', 'uptime-kuma'}):
        raise ValueError('Unexpected saved services or NetBird release')
    for config in [auth, vpn, web]:
        for service in config['services'].values():
            if any(service.get(key) for key in ['privileged', 'network_mode', 'pid', 'ipc', 'userns_mode', 'devices', 'cap_add', 'volumes_from', 'extends', 'secrets', 'configs', 'extra_hosts', 'links', 'external_links']):
                raise ValueError('Unexpected saved service privileges')
    deployment = contract.layout(request)
    paths = deployment['paths']
    allowed = [value + '/' for value in paths.values()]
    roots = [(paths['auth'], auth), (paths['netbird'], vpn), (paths['app'], web)]
    override = yaml.safe_load((payload / 'app-config/docker-compose.override.yml').read_text())
    if not set(override.get('services', {})).issubset({'astro-app', 'nginx'}):
        raise ValueError('Unexpected override service')
    for root, config in roots + [(paths['app'], override)]:
        for service in config.get('services', {}).values():
            if any(service.get(key) for key in ['privileged', 'network_mode', 'pid', 'ipc', 'userns_mode', 'devices', 'cap_add', 'volumes_from', 'extends', 'secrets', 'configs', 'extra_hosts', 'links', 'external_links']):
                raise ValueError('Unexpected service override privileges')
            for mount in service.get('volumes', []):
                if not isinstance(mount, str) or '$' in mount:
                    raise ValueError('Unexpected saved mount format')
                source = mount.split(':', 1)[0]
                if source == 'uptime-kuma-data':
                    continue  # Not started by this recovery workflow.
                resolved = str(Path(root, source).resolve())
                if not any(resolved.startswith(prefix) for prefix in allowed):
                    raise ValueError('Mount escapes the declared recovery roots')
    validate_compose(request, auth, vpn, web, override)
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
        if receipt is None and re.findall(r'^FROM (\S+)', dockerfile, re.M) != ['node:24.22.2', 'node:24.22.2']:
            raise ValueError('Review a changed build base before replacement recovery')
        if receipt is None:
            (output / 'Dockerfile.recovery').write_text(dockerfile.replace('FROM node:24.22.2 ', 'FROM ' + lock['node'] + ' '))
        shutil.rmtree(repository)
        for config in [auth, vpn, web]:
            for name, service in config['services'].items():
                if name in lock:
                    service['image'] = lock[name]
        web['services']['astro-app'].pop('build', None)
        web['services']['astro-app']['image'] = receipt['image'] if receipt else 'hou1-restored-staff:' + request['staff_revision']
        for name, value in [('auth.compose.json', auth), ('netbird.compose.json', vpn), ('app.compose.json', web)]:
            (output / name).write_text(json.dumps(value, indent=2) + '\n')
        values = dict(netbird_domain=origins['netbird'].removeprefix('https://'),
                      authentik_domain=origins['auth'].removeprefix('https://'),
                      authentik_environment='prod' if request['environment'] == 'production' else 'test', netbird_subnet=request['netbird_subnet'])
        certs = payload / 'app-config/certbot/conf'
        engine = Environment(undefined=StrictUndefined)
        mapping = {}
        for role, name in [('netbird_control_plane', 'netbird.conf'), ('authentik', 'authentik.conf')]:
            template = (ROLES / role / 'templates/nginx.conf.j2').read_text()
            rendered = engine.from_string(template).render(**values)
            rendered, found = drill.certificates.rewrite_proxy(rendered, certs)
            mapping.update(found)
            (output / name).write_text(rendered)
        for key in ['PRIMARY_HOSTNAMES', 'SECONDARY_HOSTNAMES', 'STAFF_HOSTNAME']:
            for host in app[key].split():
                if host not in request['hostnames']:
                    raise ValueError('Saved ingress hostname is outside the selected environment')
                mapping[host] = drill.certificates.select_lineage(certs, host)
        for host_key, cert_key in [('PRIMARY_HOSTNAMES', 'PRIMARY_CERT_DIR'), ('SECONDARY_HOSTNAMES', 'SECONDARY_CERT_DIR'), ('STAFF_HOSTNAME', 'STAFF_CERT_DIR')]:
            for host in app[host_key].split():
                if mapping[host] != app[cert_key]:
                    raise ValueError('Saved Nginx environment and certificate lineage differ')
        cfg = {**request, 'origins': origins, 'image_lock': lock}
        if receipt is not None:
            cfg['application_image'] = receipt
        (output / 'request.json').write_text(json.dumps(cfg, indent=2) + '\n')
        (output / 'certificate-report.json').write_text(json.dumps(mapping, indent=2) + '\n')
        shutil.copyfile(source_bundle, output / 'staff.bundle')
        shutil.copyfile(HERE / 'restore.py', output / 'restore.py')
        shutil.copyfile(HERE / 'contract.py', output / 'contract.py')
        shutil.copyfile(ROLES / 'application_image/files/artifact.py', output / 'artifact.py')
        if request['environment'] == 'test':
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
