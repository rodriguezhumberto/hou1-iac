"""Validate private lab resolver inputs before touching host DNS."""

import ipaddress
import re
from urllib.parse import urlsplit

from ansible.errors import AnsibleFilterError


def lab_dns_plan(namespace, address, records, upstreams, management_url, state='present'):
    def require(condition):
        if not condition:
            raise AnsibleFilterError('Invalid lab DNS inputs; use a test namespace, explicit private records and independent upstreams')

    def ipv4(value):
        result = ipaddress.IPv4Address(value)
        require(not (result.is_unspecified or result.is_loopback or result.is_multicast or result.is_link_local))
        return result

    def private(value):
        return any(value in ipaddress.ip_network(cidr) for cidr in
                   ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))

    try:
        require(state in ('present', 'absent'))
        require(isinstance(namespace, str) and len(namespace) <= 253 and 'test' in namespace and
                re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', namespace))
        listener = ipv4(address)
        require(private(listener))
        origin = urlsplit(management_url)
        require(origin.scheme == 'https' and origin.hostname == 'netbird-test.' + namespace
                and origin.port in (None, 443) and origin.path in ('', '/')
                and not origin.username and not origin.password and not origin.query and not origin.fragment)
        require(isinstance(records, list) and (bool(records) or state == 'absent'))
        selected = {}
        for record in records:
            require(isinstance(record, dict) and set(record) == {'name', 'address'})
            name = record['name']
            require(isinstance(name, str) and name.endswith('.' + namespace) and len(name) <= 253
                    and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', name)
                    and name not in selected)
            target = ipv4(record['address'])
            require(private(target) or target in ipaddress.ip_network('100.91.0.0/16'))
            selected[name] = str(target)
        require(isinstance(upstreams, list) and bool(upstreams))
        servers = []
        for upstream in upstreams:
            target = ipv4(upstream)
            require(target != listener and target not in ipaddress.ip_network('100.64.0.0/10'))
            servers.append(str(target))
        require(len(servers) == len(set(servers)))
        return {'namespace': namespace, 'address': str(listener), 'upstreams': servers,
                'records': [{'name': name, 'address': selected[name]} for name in sorted(selected)]}
    except (ValueError, TypeError, KeyError):
        raise AnsibleFilterError('Invalid lab DNS inputs; private values withheld') from None


class FilterModule:
    def filters(self):
        return {'lab_dns_plan': lab_dns_plan}
