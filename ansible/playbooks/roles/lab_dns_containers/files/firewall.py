#!/usr/bin/python3
"""Reconcile only owned DNS rules for declared Docker bridge networks."""

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

POLICY = Path('/etc/hou-lab-dns-firewall.json')
COMMENT = 'hou-lab-dns managed'
LEGACY = 'hou-lab container DNS'


def require(condition):
    if not condition:
        raise ValueError('Invalid container DNS policy or owned firewall rule')


def private_ipv4(value):
    address = ipaddress.ip_address(value)
    require(address.version == 4 and any(address in ipaddress.ip_network(cidr) for cidr in
            ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
    return str(address)


def private_subnet(value):
    network = ipaddress.ip_network(value)
    require(network.version == 4 and any(network.subnet_of(ipaddress.ip_network(cidr)) for cidr in
            ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
    return network


def policy(value):
    require(set(value) == {'version', 'state', 'address', 'networks'} and type(value['version']) is int and value['version'] == 1
            and value['state'] in ('present', 'absent'))
    private_ipv4(value['address'])
    names = value['networks']
    require(isinstance(names, list) and len(set(names)) == len(names) and
            all(isinstance(n, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', n) for n in names))
    return value


def command(argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL,
                                   timeout=60, env={**os.environ, 'LC_ALL': 'C'})


def rule_args(rule):
    interface, subnet, address, protocol, comment = rule
    return ['allow', *(['in', 'on', interface] if interface else []), 'from', subnet,
            'to', address, 'port', '53', 'proto', protocol, 'comment', comment]


def owned_rules(output):
    found = set()
    for line in output.splitlines():
        if not line.startswith('ufw '):
            continue
        args = shlex.split(line)[1:]
        if len(args) < 2 or args[-2] != 'comment' or args[-1] not in (COMMENT, LEGACY):
            continue
        interface = args[3] if args[1:3] == ['in', 'on'] else None
        start = 4 if interface else 1
        require(len(args) == start + 10)
        subnet, address, protocol = args[start + 1], args[start + 3], args[start + 7]
        private_subnet(subnet); private_ipv4(address)
        require(protocol in ('tcp', 'udp') and
                ((args[-1] == LEGACY and interface is None) or
                 (args[-1] == COMMENT and interface and re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface))))
        rule = interface, subnet, address, protocol, args[-1]
        require(args == rule_args(rule))
        found.add(rule)
    return found


def desired_rules(config, networks):
    desired = set()
    for network in networks:
        require(network['Name'] in config['networks'])
        require(network['Driver'] == 'bridge')
        if network.get('Internal', False):
            continue
        interface = network.get('Options', {}).get('com.docker.network.bridge.name')
        if not interface:
            require(re.fullmatch(r'[a-f0-9]{64}', network['Id']))
            interface = 'br-' + network['Id'][:12]
        require(re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface))
        subnets = network['IPAM'].get('Config') or []
        ipv4 = [ipaddress.ip_network(item['Subnet']) for item in subnets
                if ipaddress.ip_network(item['Subnet']).version == 4]
        require(bool(ipv4))
        for subnet in ipv4:
            private_subnet(str(subnet))
            for protocol in ('tcp', 'udp'):
                desired.add((interface, str(subnet), config['address'], protocol, COMMENT))
    return desired


def reconcile(config, required=(), execute=command):
    policy(config)
    require(config['state'] == 'present' or not required)
    require(set(required) <= set(config['networks']))
    desired = set()
    if config['state'] == 'present':
        existing = set(execute(['docker', 'network', 'ls', '--format', '{{.Name}}']).splitlines())
        require(set(required) <= existing)
        selected = sorted(existing & set(config['networks']))
        networks = json.loads(execute(['docker', 'network', 'inspect', *selected])) if selected else []
        require({n['Name'] for n in networks} == set(selected))
        desired = desired_rules(config, networks)
        for interface in sorted({rule[0] for rule in desired}):
            links = json.loads(execute(['ip', '-j', '-d', 'link', 'show', 'dev', interface]))
            require(len(links) == 1 and links[0].get('linkinfo', {}).get('info_kind') == 'bridge')
    installed = owned_rules(execute(['ufw', 'show', 'added']))
    # Add replacement paths first. A failed command leaves actual UFW state
    # authoritative, so the next attempt repairs an interrupted handoff.
    for rule in sorted(desired - installed):
        execute(['ufw', *rule_args(rule)])
    for rule in sorted(installed - desired, key=repr):
        execute(['ufw', '--force', 'delete', *rule_args(rule)])
    require(owned_rules(execute(['ufw', 'show', 'added'])) == desired)
    return {'changed': installed != desired, 'rules': len(desired)}


def prepare_compose(directory, compose, document, selected, execute):
    """Let Compose own network/container creation; start nothing until DNS is ready."""
    config = policy(json.loads(POLICY.read_text()))
    require(config['state'] == 'present')
    names = set(selected or document['services'])
    pending = list(names)
    while pending:
        for dependency in document['services'][pending.pop()].get('depends_on', {}):
            if dependency not in names:
                names.add(dependency); pending.append(dependency)
    used = {name for service in names for name in document['services'][service].get('networks', {})}
    required = sorted(document['networks'][name]['name'] for name in used)
    require(set(required) <= set(config['networks']))
    execute(compose + ['create', '--no-build', '--no-recreate', *(selected or [])], directory)
    result = subprocess.run(['/usr/local/libexec/hou-lab-dns-firewall', '--require-networks', *required],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-networks', nargs='*', default=[])
    parser.add_argument('--compose-directory', type=Path)
    parser.add_argument('--project')
    parser.add_argument('--file')
    args = parser.parse_args()
    if args.compose_directory:
        compose = ['docker', 'compose']
        if args.project:
            compose += ['--project-name', args.project]
        if args.file:
            compose += ['--file', args.file]

        def execute(argv, directory):
            return subprocess.check_output(argv, cwd=directory, stderr=subprocess.DEVNULL, timeout=600)

        document = json.loads(execute(compose + ['config', '--format', 'json'], args.compose_directory))
        print(json.dumps(prepare_compose(args.compose_directory, compose, document, None, execute)))
        return
    with open('/run/lock/hou-lab-dns-firewall.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(reconcile(json.loads(POLICY.read_text()), args.require_networks)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Container DNS reconciliation failed; private diagnostics withheld') from None
