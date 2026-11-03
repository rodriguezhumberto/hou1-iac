"""Validate declared private records and plan split DNS."""
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


def dns_plan(groups, records, resolver_ip, domain, subnet, resolver_port, existing):
    domain_name(domain)
    network = IPv4Network(subnet)

    def address(value):
        ip = IPv4Address(value)
        if ip not in network:
            raise AnsibleFilterError('DNS address is outside the NetBird subnet.')
        return str(ip)

    def group_id(name):
        matches = [g for g in groups if g['name'] == name]
        if len(matches) != 1:
            raise AnsibleFilterError('Private DNS requires exactly one group: ' + name)
        return matches[0]['id']

    resolver_ip = address(resolver_ip)
    resolved = []
    for record in records:
        name = domain_name(record['name'])
        if name != domain and not name.endswith('.' + domain):
            raise AnsibleFilterError('Private record is outside the split DNS domain.')
        resolved.append(dict(name=name, address=address(record['address'])))
    if not resolved or len({r['name'] for r in resolved}) != len(resolved):
        raise AnsibleFilterError('Private DNS requires nonempty, unique records.')
    # Use exact records instead of the apex domain. macOS may otherwise prefer
    # a LAN resolver that advertises the same broad search domain.
    private_domains = sorted(r['name'] for r in resolved)
    body = dict(name=NAME, description=MARKER, enabled=True,
                groups=sorted([group_id('administrators'), group_id('staff')]),
                domains=private_domains,
                nameservers=[dict(ip=resolver_ip, ns_type='udp', port=resolver_port)],
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
