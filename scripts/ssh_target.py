"""Bind SSH destinations to explicit deployment addresses and host fingerprints."""

import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def private_file(value):
    path = Path(value).expanduser()
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError('Rehearsal inputs must be private files owned by the operator')
    return path


def read_config(path):
    value = json.loads(private_file(path).read_text())
    if not isinstance(value, dict) or value.get('environment') != 'test':
        raise ValueError('An explicit test target configuration is required')
    return value


def local(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15, **kwargs)
    if result.returncode:
        raise ValueError('Local SSH target verification failed')
    return result.stdout


def pinned_ssh(config, *, peer=False, environment='test'):
    """Return SSH argv bound to an explicit address and one verified host key.

    Server targets must use the selected overlay. Test is the default; admission
    can select prod only from its independently validated inventory profile.
    A peer host may use its own LAN address with a separate fingerprint.
    Only local ssh -G and ssh-keygen commands execute here.
    """
    if environment not in ('test', 'prod') or config.get('environment') != environment:
        raise ValueError('SSH target environment differs from the selected deployment')
    address = ipaddress.ip_address(config.get('ssh_host_ip', ''))
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError('Declare a usable SSH destination address')
    subnet = '100.91.0.0/16' if environment == 'test' else '100.90.0.0/16'
    if not peer and address not in ipaddress.ip_network(subnet):
        raise ValueError('SSH server must use the selected environment overlay')
    if peer and address in ipaddress.ip_network('100.90.0.0/16'):
        raise ValueError('A rehearsal peer cannot use the production overlay')
    fingerprint = config.get('ssh_fingerprint', '')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint):
        raise ValueError('Declare the independently verified ED25519 fingerprint')
    argv = config.get('ssh')
    if (not isinstance(argv, list) or len(argv) != 4 or argv[:2] != ['ssh', '-F']
            or not all(isinstance(value, str) for value in argv)
            or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', argv[-1])):
        raise ValueError('Use ssh -F /absolute/private/config alias without extra arguments')
    config_path = private_file(argv[2])
    values = dict(line.split(' ', 1) for line in local(
        ['ssh', '-G', '-F', str(config_path), argv[-1]]).splitlines() if ' ' in line)
    if values.get('hostname') != str(address):
        raise ValueError('SSH resolves to a different destination address')
    if (values.get('stricthostkeychecking') not in ('yes', 'true')
            or values.get('identitiesonly') != 'yes'):
        raise ValueError('SSH must enforce host-key checking and explicit identities')
    user = values.get('user', '')
    port = values.get('port', '')
    if (not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user) or user == 'root'
            or not port.isdecimal() or not 0 < int(port) < 65536):
        raise ValueError('Declare a non-root SSH user and valid port')
    known_setting = values.get('userknownhostsfile', '')
    # ssh -G emits a quoted pathname containing spaces without its quotes.
    # Freeze it as one file below rather than retaining the original setting.
    files = ([known_setting] if Path(known_setting).expanduser().is_file()
             else shlex.split(known_setting))
    if len(files) != 1 or '%' in files[0]:
        raise ValueError('Use one explicit private known-hosts file')
    known_hosts = private_file(files[0])
    alias = values.get('hostkeyalias', str(address))
    if not re.fullmatch(r'[A-Za-z0-9_.:\[\]-]+', alias):
        raise ValueError('Invalid SSH host-key alias')
    lookup = alias if 'hostkeyalias' in values or int(port) == 22 else f'[{address}]:{port}'
    entries = local(['ssh-keygen', '-F', lookup, '-f', str(known_hosts)])
    keys = []
    for line in entries.splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if fields[0].startswith('@'):
            raise ValueError('Rehearsal host keys must be explicit unmarked ED25519 entries')
        if len(fields) >= 3 and fields[1] == 'ssh-ed25519':
            keys.append(line)
    if not keys:
        raise ValueError('The expected ED25519 host key is missing')
    checked = local(['ssh-keygen', '-lf', '/dev/stdin', '-E', 'sha256'],
                    input='\n'.join(keys) + '\n')
    if {line.split()[1] for line in checked.splitlines()} != {fingerprint}:
        raise ValueError('SSH host fingerprint differs from the declared test identity')
    # Freeze the checked destination and disable trust from other host-key sources
    # and connection reuse. Never use an existing multiplexed connection.
    options = {
        'Hostname': str(address), 'User': user, 'Port': port,
        'HostKeyAlias': lookup, 'UserKnownHostsFile': shlex.quote(str(known_hosts)),
        'StrictHostKeyChecking': 'yes', 'IdentitiesOnly': 'yes',
        'HostKeyAlgorithms': 'ssh-ed25519', 'GlobalKnownHostsFile': '/dev/null',
        'KnownHostsCommand': 'none', 'VerifyHostKeyDNS': 'no', 'UpdateHostKeys': 'no',
        'ControlPath': 'none', 'ControlMaster': 'no', 'BatchMode': 'yes',
        'ClearAllForwardings': 'yes', 'PermitLocalCommand': 'no', 'RemoteCommand': 'none',
        'ConnectTimeout': '10',
    }
    return ['ssh', '-F', str(config_path),
            *[arg for name, value in options.items() for arg in ('-o', f'{name}={value}')], argv[-1]]


def peer_probe_command(config, transport):
    """Replace a configured peer SSH prefix with its validated transport."""
    command = config.get('staff_probe_command')
    expected = config['peer']['ssh']
    if (not isinstance(command, list) or command[:len(expected)] != expected
            or len(command) != len(expected) + 1 or not isinstance(command[-1], str)):
        raise ValueError('The staff probe must use the declared peer SSH destination')
    return [*transport, command[-1]]


def monitor_transports(config):
    """Check every monitor destination before drills can change test services."""
    server = pinned_ssh(config)
    peer = pinned_ssh(config['peer'], peer=True)
    return server, peer, peer_probe_command(config, peer)
