"""Validate explicit recovery destinations before reading or restoring secrets."""
import ipaddress
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit


def isolation(request, address=None, mac=None):
    """Bind test-only provider ownership to an independently verified policy receipt."""
    mode = request.get('isolation_mode', 'host')
    boundary = request.get('provider_isolation', {})
    if mode == 'host':
        if boundary != {}:
            raise ValueError('Host isolation cannot declare a provider boundary')
        return mode
    if (mode != 'provider' or request['environment'] != 'test' or request.get('quarantine', False)
            or not isinstance(boundary, dict)
            or set(boundary) != {'address', 'mac', 'policy_sha256', 'verified'}
            or boundary['verified'] is not True):
        raise ValueError('Provider isolation requires a verified, non-quarantined test boundary')
    target = ipaddress.IPv4Address(boundary['address'])
    if (str(target) != boundary['address'] or not any(target in ipaddress.ip_network(cidr)
            for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
            or not isinstance(boundary['mac'], str)
            or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', boundary['mac'])
            or int(boundary['mac'][:2], 16) & 1 or boundary['mac'] == '00:00:00:00:00:00'
            or not isinstance(boundary['policy_sha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', boundary['policy_sha256'])
            or address is not None and boundary['address'] != address
            or mac is not None and boundary['mac'] != mac.lower()
            or any(request.get(key) for key in
                   ('deny_cidrs', 'test_addresses', 'allow_local_cidrs', 'allow_update_cidrs'))):
        raise ValueError('Provider isolation must match the LAN target, MAC and policy digest without host isolation inputs')
    return mode


def origin(value):
    url = urlsplit(value)
    if (url.scheme != 'https' or url.port not in (None, 443) or url.username or url.password
            or not url.hostname or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', url.hostname)
            or url.query or url.fragment):
        raise ValueError('Require a credential-free HTTPS service endpoint')
    return 'https://' + url.hostname


def layout(request):
    deployment = request['deployment']
    paths = deployment['paths']
    expected = {'app', 'auth', 'auth_data', 'netbird', 'cms'}
    if set(paths) != expected or set(deployment['projects']) != {'app', 'auth', 'netbird'}:
        raise ValueError('Declare every recovery root and Compose project')
    for key, path in paths.items():
        base = '/opt/' if key in ('app', 'auth', 'netbird') else '/var/lib/'
        if (not isinstance(path, str) or not path.startswith(base)
                or not re.fullmatch(r'/[a-zA-Z0-9_/-]+', path)
                or str(PurePosixPath(path)) != path or '..' in PurePosixPath(path).parts
                or len(PurePosixPath(path).parts) < (3 if base == '/opt/' else 4)):
            raise ValueError('Recovery roots must be canonical dedicated application directories')
    for key, path in paths.items():
        if any(key != other and (path == value or PurePosixPath(path).is_relative_to(value))
               for other, value in paths.items()):
            raise ValueError('Recovery roots must not overlap')
    projects = list(deployment['projects'].values())
    networks = deployment['networks']
    names = projects + [networks['auth'], networks['netbird']]
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,62}', name) for name in names):
        raise ValueError('Invalid declared Compose project or external network')
    if len(set(projects)) != 3 or networks['auth'] == networks['netbird']:
        raise ValueError('Compose projects and external networks must be separate')
    network = ipaddress.IPv4Network(networks['subnet'], strict=True)
    dynamic = ipaddress.IPv4Network(networks['dynamic_range'], strict=True)
    proxy = ipaddress.IPv4Address(networks['proxy_ip'])
    if (not network.is_private or not dynamic.subnet_of(network) or proxy not in network
            or proxy in dynamic or proxy in (network.network_address, network.broadcast_address)):
        raise ValueError('Declare a private ingress subnet with a reserved proxy address')
    return deployment


def endpoints(request, saved):
    actual = {key: origin(value) for key, value in saved.items()}
    expected = request['origins']
    if (set(expected) != {'auth', 'staff', 'public', 'netbird'} or actual != expected
            or any(origin(value) != value for value in expected.values())
            or len(set(expected.values())) != 4):
        raise ValueError('Saved endpoints must match the selected environment exactly')
    hosts = request['hostnames']
    if (not isinstance(hosts, list) or len(hosts) != len(set(hosts))
            or any(origin('https://' + host) != 'https://' + host for host in hosts)
            or not {value.removeprefix('https://') for value in actual.values()}.issubset(hosts)):
        raise ValueError('Declare all allowed recovery certificate hostnames')
    return actual
