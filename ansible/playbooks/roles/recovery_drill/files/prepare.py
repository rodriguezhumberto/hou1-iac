#!/usr/bin/env python3
"""Prepare a private, test-only recovery bundle on the controller."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
from urllib.parse import urlsplit

import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('recovery_certificates', HERE / 'certificates.py')
certificates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(certificates)
spec = importlib.util.spec_from_file_location('application_artifact', HERE.parents[1] / 'application_image/files/artifact.py')
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


def environment(path):
    values = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value.strip().strip('"').strip("'")
    return values


def isolated_origin(value, suffix):
    # Validate the saved URL before reducing it to an origin. Discovery issuers
    # omit the default HTTPS port; token validation still uses the exact issuer.
    url = urlsplit(value)
    if (url.scheme != 'https' or url.port not in (None, 443) or url.username or url.password
            or not url.hostname or not url.hostname.endswith('.' + suffix)
            or not re.fullmatch(r'[a-z0-9.-]+', url.hostname)):
        raise ValueError('Recovered endpoint is outside the declared isolated test suffix')
    return 'https://' + url.hostname


def validate(request, metadata):
    project = request['project']
    if (request['environment'] != 'test' or metadata['environment'] != 'test'
            or not re.fullmatch(r'hou1-recovery-drill-[a-z0-9][a-z0-9-]{5,39}', project)
            or request['root'] != '/var/tmp/' + project
            or not re.fullmatch(r'[a-f0-9]{40}', request['staff_revision'])
            or metadata['staff_revision'] != request['staff_revision']):
        raise ValueError('Require matching immutable test revision and a dedicated drill root')
    suffix = request['test_suffix']
    if not re.fullmatch(r'[a-z0-9.-]+', suffix) or not re.search(r'(^|[.-])test([.-]|$)', suffix):
        raise ValueError('Declare the isolated test DNS suffix')
    source = Path(request['app_source'])
    if not source.is_absolute() or '..' in source.parts or 'test' not in source.name:
        raise ValueError('Declare the existing test app source checkout')
    if (len(request['primary_containers']) != 7
            or len(set(request['primary_containers'])) != 7
            or any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name)
                   or 'test' not in name for name in request['primary_containers'])):
        raise ValueError('Declare exactly seven distinct test primary containers in service order')
    for key in ('admin_username', 'reader_username'):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{1,63}', request[key]) or 'test' not in request[key] and not request[key].startswith('vps-staff-'):
            raise ValueError('Use separate internal test fixture usernames')


def prepare(request, payload, output):
    os.umask(0o077)
    payload, output = Path(payload).resolve(), Path(output)
    metadata = json.loads((payload / 'recovery.json').read_text())
    validate(request, metadata)
    receipt = artifact.saved(payload, request['staff_revision'])
    if output.exists():
        raise ValueError('Staging output must not exist')
    app = environment(payload / 'app-config/.env')
    netbird = yaml.safe_load((payload / 'netbird-files/config.yaml').read_text())
    origins = {
        key: isolated_origin(value, request['test_suffix'])
        for key, value in {
            'auth_origin': app['STAFF_OIDC_ISSUER'],
            'staff_origin': app['STAFF_OIDC_REDIRECT_URI'],
            'netbird_origin': netbird['server']['exposedAddress'],
            'public_origin': app['PUBLIC_SITE_ORIGIN'],
        }.items()
    }
    if len(set(origins.values())) != 4:
        raise ValueError('Use distinct test service origins')
    root = request['root']
    services = {}
    for name, original in yaml.safe_load((payload / 'authentik/compose.yaml').read_text())['services'].items():
        if name not in ('postgresql', 'authentik-server', 'authentik-worker'):
            raise ValueError('Unexpected saved Authentik service')
        service = copy.deepcopy(original)
        service.update(restart='no', networks=['recovery'], env_file=['./payload/authentik/authentik.env'])
        for key in ('ports', 'container_name', 'build'):
            service.pop(key, None)
        service['volumes'] = ([root + '/runtime/postgres:/var/lib/postgresql/data'] if name == 'postgresql' else
                              [root + '/payload/authentik-files/' + folder + ':' + target for folder, target in
                               [('media', '/media'), ('templates', '/templates'), ('certs', '/certs'), ('config', '/blueprints/custom')]])
        services[name] = service
    for name, original in yaml.safe_load((payload / 'netbird-files/compose.yaml').read_text())['services'].items():
        if name not in ('netbird-server', 'dashboard'):
            raise ValueError('Unexpected saved NetBird service')
        service = copy.deepcopy(original)
        service.update(restart='no', networks=['recovery'])
        for key in ('ports', 'container_name', 'build'):
            service.pop(key, None)
        if name == 'netbird-server':
            service['volumes'] = [root + '/payload/netbird-files/config.yaml:/etc/netbird/config.yaml:ro', root + '/payload/netbird-files/data:/var/lib/netbird']
        else:
            service['env_file'] = ['./payload/netbird-files/dashboard.env']
            service.pop('volumes', None)
        services[name] = service
    saved_app = yaml.safe_load((payload / 'app-config/docker-compose.yml').read_text())['services']['astro-app']
    services['astro-app'] = dict(image=receipt['image'] if receipt else 'hou1-recovery-app:' + request['staff_revision'], restart='no',
                                env_file=['./payload/app-config/.env'], environment=saved_app['environment'],
                                healthcheck=saved_app['healthcheck'], networks=['recovery'], mem_limit='768m', pids_limit=256,
                                volumes=[root + '/payload/cms/database:/var/data', root + '/payload/cms/media:/var/data/media'])
    aliases = [urlsplit(value).hostname for value in origins.values()]
    services['proxy'] = dict(image='nginx:1.28.0-alpine', restart='no', networks={'recovery': {'aliases': aliases}},
                             mem_limit='128m', pids_limit=128, volumes=[root + '/proxy.conf:/etc/nginx/nginx.conf:ro', root + '/payload/app-config/certbot/conf:/etc/letsencrypt:ro'])
    if set(services) != {'postgresql', 'authentik-server', 'authentik-worker', 'netbird-server', 'dashboard', 'astro-app', 'proxy'}:
        raise ValueError('Recovery requires exactly the seven expected services')
    for service in services.values():
        if (service.get('privileged') or service.get('network_mode') or service.get('pid') or service.get('devices')
                or service.get('cap_add') or service.get('volumes_from') or service.get('extra_hosts')
                or service.get('external_links') or service.get('links') or service.get('extends')
                or service.get('secrets') or service.get('configs') or service.get('ipc') or service.get('userns_mode')
                or any(not mount.split(':', 1)[0].startswith(root + '/') for mount in service.get('volumes', []))):
            raise ValueError('Saved service violates drill isolation')
        service['logging'] = {'driver': 'json-file', 'options': {'max-size': '5m', 'max-file': '2'}}
    compose = {'services': services, 'networks': {'recovery': {'internal': True, 'name': request['project']}}}
    common = 'proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Forwarded-Proto https; proxy_set_header X-Forwarded-Host $host; proxy_http_version 1.1; proxy_set_header Connection "";'
    servers = []
    for origin, backend in [('auth_origin', 'authentik-server:9000'), ('staff_origin', 'astro-app:4321'), ('netbird_origin', 'netbird-server:80'), ('public_origin', 'astro-app:4321')]:
        host = urlsplit(origins[origin]).hostname
        locations = 'location / { proxy_pass http://$backend; ' + common + ' }'
        if origin == 'netbird_origin':
            locations = 'location ~ ^/(api|oauth2)/ { proxy_pass http://$backend; ' + common + ' } location / { set $dashboard dashboard:80; proxy_pass http://$dashboard; ' + common + ' }'
        if origin == 'public_origin':
            locations = 'location ^~ /api/staff/ { return 404; } location /staff { return 404; } ' + locations
        servers.append('server { listen 443 ssl; server_name ' + host + '; ssl_certificate /etc/letsencrypt/live/' + host + '/fullchain.pem; ssl_certificate_key /etc/letsencrypt/live/' + host + '/privkey.pem; resolver 127.0.0.11 valid=2s ipv6=off; set $backend ' + backend + '; client_max_body_size 20m; ' + locations + ' }')
    proxy, mapping = certificates.rewrite_proxy('events {}\nhttp { access_log off; error_log /dev/stderr crit; ' + '\n'.join(servers) + '\n}\n', payload / 'app-config/certbot/conf')
    output.mkdir(mode=0o700, parents=True)
    cfg = {**request, **origins, 'issuer': app['STAFF_OIDC_ISSUER']}
    if receipt is not None:
        cfg['application_image'] = receipt
    (output / 'drill-input.json').write_text(json.dumps(cfg, indent=2) + '\n')
    (output / 'compose.json').write_text(json.dumps(compose, indent=2) + '\n')
    (output / 'proxy.conf').write_text(proxy)
    (output / 'certificate-mapping.json').write_text(json.dumps(mapping, indent=2) + '\n')
    for name in ('runtime.py', 'build.py', 'check.py', 'http_probe.py', 'identity_smoke.py', 'driver.py'):
        shutil.copyfile(HERE / name, output / name)
    shutil.copyfile(HERE.parents[1] / 'application_image/files/artifact.py', output / 'artifact.py')
    shutil.copyfile(HERE.parents[4] / 'scripts/verify-recovery.py', output / 'safe_archive.py')
    with tarfile.open(output / 'bundle.tar', 'w') as archive:
        archive.add(payload, arcname='payload')
        archive.add(payload / 'cms', arcname='original-cms')
        for path in sorted(output.iterdir()):
            if path.name != 'bundle.tar':
                archive.add(path, arcname=path.name)
    return {'prepared': True, 'project': request['project'], 'staff_revision': request['staff_revision'], 'published_ports': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('request', 'payload', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(json.loads(Path(args.request).read_text()), args.payload, args.output)))
