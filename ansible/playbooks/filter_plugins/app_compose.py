"""Render declared application integration; inspect old output only for ownership."""
from copy import deepcopy
import hashlib
import json
import re

from ansible.errors import AnsibleFilterError
import yaml


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _target(mount):
    if isinstance(mount, dict):
        return mount.get('target')
    if isinstance(mount, str) and ':' in mount:
        return mount.split(':')[1]
    return mount


def _unmanaged(value):
    """Remove only fields owned by current or historical integration templates."""
    result = deepcopy(value)
    services = result.get('services', {})
    for service, targets in {
            'astro-app': {'/var/data/media'},
            'nginx': {'/etc/nginx/conf.d/netbird.conf', '/etc/nginx/netbird'}}.items():
        definition = services.get(service, {})
        if not isinstance(definition, dict):
            continue
        if isinstance(definition.get('volumes'), list):
            definition['volumes'] = [mount for mount in definition['volumes']
                                     if _target(mount) not in targets]
            if not definition['volumes']:
                definition.pop('volumes')
    nginx = services.get('nginx', {})
    networks = nginx.get('networks', {}) if isinstance(nginx, dict) else {}
    if isinstance(networks, dict):
        for name, owned in {'app-network': (), 'netbird-ingress': ('ipv4_address',),
                            'authentik-ingress': ('external', 'name')}.items():
            if isinstance(networks.get(name), dict):
                for key in owned:
                    networks[name].pop(key, None)
            elif name in networks and networks[name] is None:
                networks[name] = {}
            if networks.get(name) == {}:
                networks.pop(name)
        if not networks and isinstance(nginx, dict):
            nginx.pop('networks', None)
    for name in ('netbird-ingress', 'authentik-ingress'):
        network = result.get('networks', {}).get(name)
        if isinstance(network, dict):
            for key in ('external', 'name'):
                network.pop(key, None)
            if not network:
                result['networks'].pop(name)
    for name in ('astro-app', 'nginx'):
        if services.get(name) == {}:
            services.pop(name)
    for name in ('services', 'networks'):
        if result.get(name) == {}:
            result.pop(name)
    return result


def _merge(base, additions, path=''):
    result = deepcopy(base)
    for key, value in additions.items():
        location = f'{path}.{key}'.lstrip('.')
        if key not in result:
            result[key] = deepcopy(value)
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value, location)
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] += deepcopy(value)
        else:
            raise AnsibleFilterError(f'app_compose_extensions cannot replace managed {location}')
    return result


def compose_override(media_directory, ingress_directory, network, proxy_ip,
                     authentik_network=None, extensions=None):
    extensions = extensions if extensions is not None else {}
    if not isinstance(extensions, dict):
        raise AnsibleFilterError('app_compose_extensions must be a mapping')
    if extensions != _unmanaged(extensions):
        raise AnsibleFilterError('app_compose_extensions cannot declare managed mounts or network fields')
    if not authentik_network and (
            'authentik-ingress' in extensions.get('networks', {}) or
            'authentik-ingress' in extensions.get('services', {}).get('nginx', {}).get('networks', {})):
        raise AnsibleFilterError('app_compose_extensions references disabled authentik-ingress')
    desired = {
        'services': {
            'astro-app': {'volumes': [f'{media_directory}:/var/data/media']},
            'nginx': {
                'volumes': [f'{ingress_directory}/nginx-include.conf:/etc/nginx/conf.d/netbird.conf:ro',
                            f'{ingress_directory}/ingress:/etc/nginx/netbird:ro'],
                'networks': {'app-network': {}, 'netbird-ingress': {'ipv4_address': proxy_ip}},
            },
        },
        'networks': {'netbird-ingress': {'external': True, 'name': network}},
    }
    if authentik_network:
        desired['services']['nginx']['networks']['authentik-ingress'] = {}
        desired['networks']['authentik-ingress'] = {'external': True, 'name': authentik_network}
    return _merge(desired, extensions)


def _unclaimed(previous, declared, path=''):
    errors = []
    if isinstance(previous, dict) and isinstance(declared, dict):
        for key, value in previous.items():
            location = f'{path}.{key}'.lstrip('.')
            if key not in declared:
                errors.extend(_unclaimed(value, {}, location)
                              if isinstance(value, dict) and value else [location])
            else:
                errors.extend(_unclaimed(value, declared[key], location))
    elif isinstance(previous, list) and isinstance(declared, list) and path.endswith('.volumes'):
        declared_targets = {_target(mount) for mount in declared}
        errors.extend(f'{path}[{_target(mount)}]' for mount in previous
                      if _target(mount) not in declared_targets)
    elif isinstance(previous, list) and isinstance(declared, list):
        return []
    elif isinstance(previous, (dict, list)) or isinstance(declared, (dict, list)):
        errors.append(path)
    return errors


def migration_errors(text, extensions):
    try:
        previous = yaml.safe_load(text)
        if not isinstance(previous, dict):
            return ['override must contain a Compose mapping']
        signature = re.search(r'^# Generated app_compose SHA256: ([0-9a-f]{64})$', text, re.MULTILINE)
        if signature and signature[1] == fingerprint(previous):
            return []
        return _unclaimed(_unmanaged(previous), extensions)
    except (AttributeError, TypeError, ValueError, yaml.YAMLError) as error:
        return [f'invalid override: {error}']


class FilterModule:
    def filters(self):
        return {'app_compose_override': compose_override,
                'app_compose_fingerprint': fingerprint,
                'app_compose_migration_errors': migration_errors}
