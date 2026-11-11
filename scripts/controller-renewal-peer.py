#!/usr/bin/env python3
"""Provision a persistent, isolated NetBird peer for certificate delivery only."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

IMAGE = 'netbirdio/netbird@sha256:eca5b274c635bfbb04ec6724a0712d1146190dd450eea199eeb83916fdd94965'
OWNER = 'controller-renewal-peer/v1'


def validate(config):
    if set(config) != {'environment', 'management_url', 'subnet', 'server_group', 'targets', 'runtime', 'container'}:
        raise ValueError('Declare the complete peer contract')
    if config['environment'] not in ('test', 'production'):
        raise ValueError('Unknown environment')
    if (not isinstance(config['server_group'], str) or config['server_group'] == 'All'
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{1,62}', config['server_group'])):
        raise ValueError('Declare the environment server group explicitly')
    url = urlsplit(config['management_url'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment
            or url.hostname in ('api.netbird.io', 'app.netbird.io') or url.port not in (None, 443)):
        raise ValueError('Use one self-hosted HTTPS origin')
    subnet = ipaddress.IPv4Network(config['subnet'])
    if not subnet.subnet_of(ipaddress.IPv4Network('100.64.0.0/10')):
        raise ValueError('Use the declared private overlay')
    if (not isinstance(config['targets'], list) or not 1 <= len(config['targets']) <= 10
            or len(set(config['targets'])) != len(config['targets'])):
        raise ValueError('Declare unique receiver addresses')
    for target in config['targets']:
        address = ipaddress.IPv4Address(target)
        if address not in subnet or address in (subnet.network_address, subnet.broadcast_address):
            raise ValueError('Receiver is outside the overlay')
    if (not re.fullmatch(r'/[A-Za-z0-9_./-]+', config['runtime'])
            or '..' in Path(config['runtime']).parts
            or not re.fullmatch(r'[a-z][a-z0-9-]{2,62}', config['container'])):
        raise ValueError('Declare protected runtime and container names')
    root = Path(config['runtime'])
    if (not root.is_dir() or root.is_symlink() or root.stat().st_uid != os.getuid()
            or root.stat().st_mode & 0o077):
        raise ValueError('Runtime must be controller-owned and private')


def command(argv):
    return subprocess.run(argv, check=True, capture_output=True, timeout=100).stdout


def ids(groups):
    return {group['id'] if isinstance(group, dict) else group for group in groups}


def target_peers(config, peers):
    selected = []
    expected_group = config['server_group']
    for address in config['targets']:
        matches = [peer for peer in peers if peer['ip'] == address]
        if len(matches) != 1 or expected_group not in {g['name'] for g in matches[0]['groups']}:
            raise ValueError('Each receiver must be one enrolled environment server')
        selected.append(matches[0]['id'])
    return set(selected)


def require_isolated_source(source, groups, policies, expected):
    implicit_all = {group['id'] for group in groups if group['name'] == 'All'}
    memberships = ids(source['groups']) | implicit_all
    if memberships - implicit_all != ids(expected['rules'][0]['sources']):
        raise ValueError('Renewal peer must belong only to its dedicated source group')
    for policy in policies:
        if policy['name'] == expected['name']:
            require_owned_policy(policy, expected)
            continue
        if not policy['enabled']:
            continue
        for rule in policy['rules']:
            if rule['enabled'] and rule['action'] == 'accept':
                if (memberships & ids(rule['sources']) or
                        rule['bidirectional'] and memberships & ids(rule['destinations'])):
                    raise ValueError('An unrelated policy grants this peer access')


def policy_body(config, source_id, targets_id):
    return {'name': 'tls-renewal-' + config['environment'], 'description': OWNER, 'enabled': True,
            'rules': [{'name': 'receiver-ssh', 'enabled': True, 'action': 'accept',
                       'bidirectional': False, 'protocol': 'tcp', 'ports': ['22'],
                       'sources': [source_id], 'destinations': [targets_id]}]}


def require_persistent_peer(peer):
    if any(peer.get(key) is not False for key in (
            'ephemeral', 'inactivity_expiration_enabled', 'login_expiration_enabled', 'login_expired')):
        raise ValueError('Renewal peer must be persistent without login or inactivity expiry')


def require_owned_policy(policy, expected):
    if (policy['name'] != expected['name'] or policy.get('description') != OWNER
            or policy['enabled'] is not True or len(policy['rules']) != 1):
        raise ValueError('Unexpected renewal policy owner or rule count')
    actual, wanted = policy['rules'][0], expected['rules'][0]
    for key in ('enabled', 'action', 'bidirectional', 'protocol', 'ports'):
        if actual.get(key) != wanted[key]:
            raise ValueError('Unexpected renewal policy permissions')
    for key in ('sources', 'destinations'):
        if ids(actual[key]) != set(wanted[key]):
            raise ValueError('Unexpected renewal policy membership')
    if actual.get('port_ranges') or policy.get('source_posture_checks'):
        raise ValueError('Unexpected additional policy selectors')


def require_container(container, config, running=True):
    host = container['HostConfig']
    mounts = container['Mounts']
    if (running and not container['State']['Running'] or container['Config']['Image'] != IMAGE
            or host['NetworkMode'] != 'bridge' or host['Privileged']
            or host['CapAdd'] != ['NET_ADMIN'] or host.get('PortBindings')
            or host['RestartPolicy']['Name'] != 'unless-stopped'
            or len(mounts) != 1 or mounts[0]['Source'] != config['runtime']
            or mounts[0]['Destination'] != '/var/lib/netbird' or not mounts[0]['RW']
            or 'no-new-privileges=true' not in host['SecurityOpt']):
        raise ValueError('Unexpected renewal peer container boundary')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Management API redirects are forbidden')


def provision(config, token):
    validate(config)
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise ValueError('Missing protected management token')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def api(method, path, body=None):
        request = urllib.request.Request(config['management_url'] + '/api' + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Token ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=20) as response:
            data = response.read()
        return json.loads(data) if data else {}

    groups, peers, policies = api('GET', '/groups'), api('GET', '/peers'), api('GET', '/policies')
    targets = target_peers(config, peers)
    source_name = 'tls-renewal-controller-' + config['environment']
    target_name = 'tls-renewal-receivers-' + config['environment']

    def group(name, members):
        matches = [g for g in groups if g['name'] == name]
        if len(matches) > 1:
            raise ValueError('Duplicate renewal group')
        if matches:
            existing = matches[0]
            if members is not None and ids(existing['peers']) != members or existing.get('resources'):
                raise ValueError('Existing renewal group has different membership')
            return existing
        return api('POST', '/groups', {'name': name, 'peers': sorted(members or []), 'resources': []})

    destination = group(target_name, targets)
    source = group(source_name, None)
    expected = policy_body(config, source['id'], destination['id'])
    # The one policy exempted from the broad-access check must already match exactly.
    for policy in policies:
        if policy['name'] == expected['name']:
            require_owned_policy(policy, expected)
    existing = command(['docker', 'ps', '-a', '-q', '--filter', 'name=^/' + config['container'] + '$']).strip()
    if not existing:
        if source['peers']:
            raise ValueError('A renewal peer already exists without this runtime')
        # Check global policies before enrolling: the implicit All group must not grant access.
        possible_groups = [{'id': source['id']}]
        require_isolated_source({'groups': possible_groups}, groups, policies, expected)
        setup = api('POST', '/setup-keys', {'name': source_name, 'type': 'one-off',
                    'usage_limit': 1, 'expires_in': 600, 'ephemeral': False, 'auto_groups': [source['id']]})
        key_path = Path(config['runtime']) / '.setup-key'
        try:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', setup['id']) or not setup.get('key'):
                raise ValueError('Invalid enrollment response')
            if key_path.exists():
                raise ValueError('Remove the interrupted enrollment grant before retrying')
            descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(setup['key'])
            command(['docker', 'run', '-d', '--name', config['container'], '--hostname', config['container'],
                     '--restart', 'unless-stopped', '--network', 'bridge', '--cap-add', 'NET_ADMIN',
                     '--device', '/dev/net/tun', '--security-opt', 'no-new-privileges=true',
                     '--pids-limit', '128', '--memory', '256m', '--cpus', '0.5',
                     '--log-driver', 'local', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=2',
                     '--volume', config['runtime'] + ':/var/lib/netbird',
                     '--entrypoint', '/usr/local/bin/netbird', IMAGE, 'service', 'run',
                     '--log-file', 'console', '--log-level', 'error'])
            for attempt in range(20):
                try:
                    command(['docker', 'exec', config['container'], 'netbird', 'status', '--check', 'live'])
                    break
                except subprocess.CalledProcessError:
                    if attempt == 19:
                        raise
                    time.sleep(1)
            command(['docker', 'exec', config['container'], 'netbird', 'up',
                     '--management-url', config['management_url'], '--setup-key-file', '/var/lib/netbird/.setup-key',
                     '--interface-name', 'wt0', '--wireguard-port', '51821', '--disable-dns',
                     '--disable-client-routes', '--disable-server-routes', '--block-inbound', '--no-browser'])
        finally:
            key_path.unlink(missing_ok=True)
            api('DELETE', '/setup-keys/' + setup['id'])
    else:
        details, = json.loads(command(['docker', 'inspect', config['container']]))
        require_container(details, config, running=False)
        if not details['State']['Running']:
            command(['docker', 'start', config['container']])
    details, = json.loads(command(['docker', 'inspect', config['container']]))
    require_container(details, config)
    if command(['docker', 'exec', config['container'], 'netbird', 'version']).decode().strip() != '0.82.0':
        raise ValueError('Unexpected NetBird client version')
    settings = json.loads(command(['docker', 'exec', config['container'], 'cat', '/var/lib/netbird/default.json']))
    if any(settings.get(flag) is not True for flag in ('BlockInbound', 'DisableDNS', 'DisableClientRoutes', 'DisableServerRoutes')):
        raise ValueError('Renewal peer must block inbound traffic, DNS and routed networks')
    for attempt in range(20):
        status = json.loads(command(['docker', 'exec', config['container'], 'netbird', 'status', '--json']))
        if status['management']['connected']:
            break
        time.sleep(1)
    if (not status['management']['connected'] or
            urlsplit(status['management']['url']).hostname != urlsplit(config['management_url']).hostname):
        raise ValueError('Renewal peer did not connect to the selected control plane')
    address = status['netbirdIp'].split('/')[0]
    if ipaddress.IPv4Address(address) not in ipaddress.IPv4Network(config['subnet']) or address in config['targets']:
        raise ValueError('Unexpected renewal peer address')
    peer, = [p for p in api('GET', '/peers') if p['ip'] == address]
    require_persistent_peer(peer)
    source = api('GET', '/groups/' + source['id'])
    if ids(source['peers']) != {peer['id']} or peer['name'] != config['container']:
        raise ValueError('Renewal source group must contain only this peer')
    require_isolated_source(peer, api('GET', '/groups'), api('GET', '/policies'), expected)
    owned = [p for p in api('GET', '/policies') if p['name'] == expected['name']]
    if len(owned) > 1:
        raise ValueError('Duplicate renewal policy')
    if owned:
        require_owned_policy(owned[0], expected)
        policy = owned[0]
    else:
        policy = api('POST', '/policies', expected)
    require_owned_policy(api('GET', '/policies/' + policy['id']), expected)
    return {'passed': True, 'environment': config['environment'], 'address': address,
            'container': config['container'], 'image': IMAGE, 'version': '0.82.0', 'namespace_isolated': True,
            'setup_key_removed': True, 'persistent': True, 'only_receiver_tcp_22': True}


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if sys.argv[1:]:
            raise ValueError('Inputs are accepted only through protected stdin')
        data = json.load(sys.stdin)
        print(json.dumps(provision(data['config'], data['token'])))
    except Exception:
        sys.exit('Renewal peer provisioning stopped; private diagnostics withheld.')
