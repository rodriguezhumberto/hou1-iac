"""Discover private records from unambiguous server groups and plan split DNS."""
from ipaddress import IPv4Address, IPv4Network
import re

from ansible.errors import AnsibleFilterError

MARKER = 'Managed by hou1-iac.'
NAME = 'hou1-private-dns'


def domain_name(value):
    if not isinstance(value, str) or len(value) > 253 or not all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
        for label in value.split('.')
    ):
        raise AnsibleFilterError('Private DNS requires valid lowercase DNS names.')
    return value


def nameserver_body(value):
    return {key: sorted(value[key]) if key in ('groups', 'domains') else value[key]
            for key in ('name', 'description', 'enabled', 'groups', 'domains',
                        'nameservers', 'primary', 'search_domains_enabled')}


def dns_plan(peers, groups, records, resolver_ip, domain, subnet, existing):
    domain_name(domain)
    network = IPv4Network(subnet)

    def address(value):
        ip = IPv4Address(value)
        if ip not in network:
            raise AnsibleFilterError('DNS peer address is outside the NetBird subnet.')
        return str(ip)

    def group_id(name):
        matches = [g for g in groups if g['name'] == name]
        if len(matches) != 1:
            raise AnsibleFilterError('Private DNS requires exactly one group: ' + name)
        return matches[0]['id']

    def belongs(peer, name):
        return any(g['id'] == group_id(name) for g in peer.get('groups', []))

    resolver_ip = address(resolver_ip)
    resolvers = [p for p in peers if p['ip'] == resolver_ip and belongs(p, 'prod-servers')]
    if len(resolvers) != 1:
        raise AnsibleFilterError('DNS resolver must be the enrolled production peer.')
    resolved = []
    for record in records:
        name = domain_name(record['name'])
        if name != domain and not name.endswith('.' + domain):
            raise AnsibleFilterError('Private record is outside the split DNS domain.')
        if record['peer_group'] not in ('test-servers', 'prod-servers'):
            raise AnsibleFilterError('DNS records must select a server environment group.')
        matches = [p for p in peers if belongs(p, record['peer_group'])]
        if len(matches) != 1:
            raise AnsibleFilterError('DNS records require exactly one peer in ' + record['peer_group'])
        resolved.append(dict(name=name, address=address(matches[0]['ip'])))
    if not resolved or len({r['name'] for r in resolved}) != len(resolved):
        raise AnsibleFilterError('Private DNS requires nonempty, unique records.')
    body = dict(name=NAME, description=MARKER, enabled=True,
                groups=sorted([group_id('administrators'), group_id('staff')]),
                domains=[domain], nameservers=[dict(ip=resolver_ip, ns_type='udp', port=53)],
                primary=False, search_domains_enabled=True)
    matches = [n for n in existing if n['name'] == NAME]
    if len(matches) > 1 or (matches and matches[0]['description'] != MARKER):
        raise AnsibleFilterError('Duplicate or operator-owned hou1-private-dns nameserver group.')
    for other in existing:
        if (other['name'] != NAME and other.get('enabled') and
                set(other.get('groups', [])) & set(body['groups']) and
                any(d == domain or domain.endswith('.' + d) or d.endswith('.' + domain)
                    for d in other.get('domains', []))):
            raise AnsibleFilterError('An enabled nameserver group overlaps the private DNS domain.')
    current = matches[0] if matches else None
    return dict(records=resolved, body=body, id=current['id'] if current else None,
                changed=not current or nameserver_body(current) != body)


class FilterModule:
    def filters(self):
        return {'netbird_dns_plan': dns_plan}
