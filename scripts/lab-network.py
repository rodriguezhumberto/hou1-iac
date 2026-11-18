#!/usr/bin/env python3
"""Run a command through an existing dedicated test VPN container on Linux."""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

HOSTS_FILE = Path('/etc/hosts')


class TransportError(ValueError):
    """A transport prerequisite failed without exposing private inputs."""


def require(condition, message):
    if not condition:
        raise TransportError(message)


def management_origin(value):
    url = urlsplit(value)
    require(url.scheme == 'https' and url.hostname and url.hostname.startswith('netbird-test.')
            and not url.username and not url.password and url.port in (None, 443)
            and url.path in ('', '/') and not url.query and not url.fragment,
            'Select a dedicated test management origin')
    return url.scheme, url.hostname, url.port or 443


def private_file(value, uid):
    path = Path(value).expanduser()
    require(path.is_absolute() and not path.is_symlink() and path.is_file(),
            'Use absolute, regular private files')
    info = path.stat()
    require(info.st_uid == uid and not info.st_mode & 0o077,
            'Private files must be operator-owned and mode 0600')
    return path


def load_config(path, uid):
    config = json.loads(private_file(path, uid).read_text())
    require(set(config) - {'hosts'} == {'version', 'environment', 'container', 'image',
            'state_directory', 'resolver_file', 'management_url', 'address'},
            'Declare the complete lab transport configuration')
    require(type(config['version']) is int and config['version'] == 1 and config['environment'] == 'test',
            'Only version 1 test transport configurations are supported')
    require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', config['container']) and
            re.fullmatch(r'[a-z0-9./:_-]+@sha256:[a-f0-9]{64}', config['image']),
            'Declare one container and its digest-pinned image')
    state = Path(config['state_directory'])
    require(state.is_absolute() and state.is_dir() and not state.is_symlink(),
            'Declare the existing dedicated peer state directory')
    require(state.stat().st_uid == uid and not state.stat().st_mode & 0o077,
            'The dedicated peer state directory must be operator-owned and mode 0700')
    resolver = private_file(config['resolver_file'], uid)
    servers = []
    for line in resolver.read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields:
            continue
        if fields[0] == 'options':
            require(len(fields) > 1 and all(re.fullmatch(r'(?:timeout|attempts):[1-9][0-9]?', value)
                    for value in fields[1:]), 'Resolver options support only positive timeout and attempts values')
            continue
        require(len(fields) == 2 and fields[0] == 'nameserver',
                'The namespace resolver file supports nameserver addresses and timeout/attempts options')
        servers.append(ipaddress.ip_address(fields[1]))
    require(bool(servers), 'Declare at least one namespace DNS server')
    management_origin(config['management_url'])
    validate_hosts(config)
    require(ipaddress.ip_address(config['address']) in ipaddress.ip_network('100.91.0.0/16'),
            'Select the test administrative peer overlay address')
    return config


def validate_hosts(config):
    hosts = config.get('hosts', {})
    namespace = management_origin(config['management_url'])[1].removeprefix('netbird-test.')
    require(isinstance(hosts, dict), 'Declare optional process-local host records as a mapping')
    for name, value in hosts.items():
        require(isinstance(name, str) and len(name) <= 253 and name.endswith('.' + namespace)
                and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', name),
                'Process-local host records must stay within the selected test namespace')
        address = ipaddress.IPv4Address(value)
        require(any(address in ipaddress.ip_network(cidr) for cidr in
                ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.91.0.0/16')),
                'Process-local host records must select lab LAN or test overlay addresses')
    return hosts


def hosts_view(original, config):
    """Replace only the legacy lab block in a private copy, preserving other aliases."""
    hosts = validate_hosts(config)
    output, covered = [], set()
    managed = False
    seen = False
    for line in original.splitlines(keepends=True):
        marker = line.rstrip('\r\n')
        if marker == '# BEGIN hou-lab bootstrap':
            require(not managed and not seen, 'The legacy lab hosts block is ambiguous')
            managed = True; seen = True
            continue
        if marker == '# END hou-lab bootstrap':
            require(managed, 'The legacy lab hosts block is incomplete')
            managed = False
            continue
        fields = line.split('#', 1)[0].split()
        if managed:
            require(not fields or len(fields) > 1 and all(name in hosts for name in fields[1:]),
                    'Preserve unrelated names found inside the legacy lab hosts block')
            continue
        if len(fields) > 1:
            for name in set(fields[1:]) & set(hosts):
                require(fields[0] == hosts[name], 'A declared lab name conflicts with an unrelated hosts entry')
                covered.add(name)
        output.append(line)
    require(not managed, 'The legacy lab hosts block is incomplete')
    base = ''.join(output)
    suffix = ''.join(hosts[name] + ' ' + name + '\n' for name in sorted(set(hosts) - covered))
    return base + ('\n' if base and not base.endswith('\n') else '') + '# Process-local lab names\n' + suffix


def inspect_peer(config):
    data, = json.loads(subprocess.check_output(['docker', 'inspect', config['container']],
                                              stderr=subprocess.DEVNULL, timeout=15))
    require(data['Name'] == '/' + config['container'] and data['State']['Running']
            and data['State']['Pid'] > 1 and data['Config']['Image'] == config['image'],
            'The declared dedicated VPN container is not running at its pinned image')
    host = data['HostConfig']
    require(host['NetworkMode'] == 'bridge' and not host['Privileged']
            and host['CapAdd'] == ['NET_ADMIN'], 'Unexpected VPN container isolation settings')
    require([(m['Source'], m['Destination'], m['RW']) for m in data['Mounts']] ==
            [(config['state_directory'], '/var/lib/netbird', True)],
            'The VPN container must mount only its dedicated peer state')
    status = json.loads(subprocess.check_output(['docker', 'exec', config['container'],
        'netbird', 'status', '--json'], stderr=subprocess.DEVNULL, timeout=20))
    require(management_origin(status['management']['url']) == management_origin(config['management_url'])
            and status['management']['connected'] and
            str(ipaddress.ip_interface(status['netbirdIp']).ip) == config['address'],
            'The VPN container is not connected to the declared test account and address')
    return {'id': data['Id'], 'pid': data['State']['Pid']}


def resolver_hash(config):
    return hashlib.sha256(Path(config['resolver_file']).read_bytes()).hexdigest()


def agent_socket(path, uid):
    info = Path(path).stat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid,
            'SSH_AUTH_SOCK must select an operator-owned socket')
    return str(Path(path).resolve())


def enter(config_path, contract, command):
    require(os.geteuid() == 0 and int(os.environ.get('SUDO_UID', '0')) == contract['uid']
            and int(os.environ.get('SUDO_GID', '-1')) == contract['gid'] and contract['uid'] > 0,
            'Namespace entry must come from the invoking non-root operator through sudo')
    require(os.readlink('/proc/self/ns/mnt') != contract['mount_namespace'],
            'Resolver changes require a new private mount namespace')
    config = load_config(config_path, contract['uid'])
    require(config == contract['config'] and resolver_hash(config) == contract['resolver_sha256']
            and inspect_peer(config) == contract['peer'],
            'The dedicated transport changed before namespace entry')
    agent = agent_socket(contract['agent'], contract['uid'])
    hosts = hosts_view(HOSTS_FILE.read_text(), config) if config.get('hosts') else None
    # The caller created a private mount namespace before entering this function.
    subprocess.run(['mount', '--bind', config['resolver_file'], '/etc/resolv.conf'], check=True)
    if hosts is not None:
        # The bind mount retains the inode after unlink; no generated hosts file
        # remains on the controller after the private namespace exits.
        with tempfile.TemporaryDirectory(prefix='hou-lab-hosts-') as folder:
            path = Path(folder) / 'hosts'
            path.write_text(hosts); path.chmod(0o644)
            subprocess.run(['mount', '--bind', str(path), '/etc/hosts'], check=True)
    operator = pwd.getpwuid(contract['uid'])
    argv = ['nsenter', '--target', str(contract['peer']['pid']), '--net',
            'setpriv', '--reuid', str(contract['uid']), '--regid', str(contract['gid']),
            '--init-groups', '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all',
            '--ambient-caps=-all', '/usr/bin/env', '-i',
            'HOME=' + operator.pw_dir, 'USER=' + operator.pw_name, 'LOGNAME=' + operator.pw_name,
            'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'SSH_AUTH_SOCK=' + agent, *command]
    os.execvp(argv[0], argv)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--inside-private-mount', help=argparse.SUPPRESS)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    try:
        require(bool(command), 'Supply a command after --')
        if args.inside_private_mount:
            enter(args.config, json.loads(args.inside_private_mount), command)
            return 0
        require(sys.platform == 'linux' and os.getuid() > 0, 'Run from a non-root Linux controller account')
        config = load_config(args.config, os.getuid())
        contract = {'uid': os.getuid(), 'gid': os.getgid(), 'config': config,
                    'peer': inspect_peer(config), 'resolver_sha256': resolver_hash(config),
                    'mount_namespace': os.readlink('/proc/self/ns/mnt'),
                    'agent': agent_socket(os.environ['SSH_AUTH_SOCK'], os.getuid())}
        argv = ['sudo', '-n', 'unshare', '--mount', '--propagation', 'private', '/usr/bin/python3',
                str(Path(__file__).resolve()), '--config', str(Path(args.config).resolve()),
                '--inside-private-mount', json.dumps(contract), '--', *command]
        os.execvp(argv[0], argv)
    except TransportError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Lab transport failed; check the private configuration, VPN status and sudo access', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
