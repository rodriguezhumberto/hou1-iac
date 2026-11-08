"""Translate API policy responses and plan managed application and DNS access."""
from copy import deepcopy
import json

from ansible.errors import AnsibleFilterError

MARKER = 'Managed by hou1-iac.'


def policy_body(policy):
    body = deepcopy(policy)
    body.pop('id', None)
    body['source_posture_checks'] = body.get('source_posture_checks') or []
    for rule in body['rules']:
        rule.pop('id', None)
        for key in ('sources', 'destinations'):
            if key in rule:
                rule[key] = sorted(g['id'] if isinstance(g, dict) else g for g in rule[key] or [])
        for key in list(rule):
            if rule[key] is None or (key in ('ports', 'port_ranges', 'authorized_groups') and not rule[key]):
                del rule[key]
        if 'ports' in rule:
            rule['ports'] = sorted(rule['ports'])
    body['rules'].sort(key=lambda rule: json.dumps(rule, sort_keys=True))
    return body


def policy_plan(groups, policies, dns_port=1053, server_group='prod-servers'):
    if server_group not in ('test-servers', 'prod-servers'):
        raise AnsibleFilterError('Policies require an explicit test or production server group')
    ids = {}
    for name in ('test-servers', 'prod-servers', 'administrators', 'staff', 'All'):
        matches = [g for g in groups if g['name'] == name]
        if len(matches) != 1:
            raise AnsibleFilterError('Policies require exactly one group named ' + name)
        ids[name] = matches[0]['id']
    desired = []
    for source, ports in [('administrators', ['22', '443']), ('staff', ['443'])]:
        name = 'hou1-' + source
        desired.append(dict(name=name, description=MARKER, enabled=True, source_posture_checks=[], rules=[dict(
            name=name, description=MARKER, enabled=True, action='accept', protocol='tcp',
            bidirectional=False, sources=[ids[source]],
            destinations=([ids[server_group]] if server_group == 'test-servers'
                           else sorted([ids['test-servers'], ids['prod-servers']])), ports=ports)]))
    # NetBird 0.82 omits repeated source groups in multi-rule API responses.
    # Single-rule policies keep readback verification reliable without inferring access.
    for protocol in ('tcp', 'udp'):
        name = 'hou1-dns-' + protocol
        desired.append(dict(name=name, description=MARKER, enabled=True, source_posture_checks=[], rules=[dict(
            name=name, description=MARKER, enabled=True, action='accept', protocol=protocol,
            bidirectional=False, sources=sorted([ids['administrators'], ids['staff']]),
            destinations=[ids[server_group]], ports=[str(dns_port)])]))
    plan = []
    for body in desired:
        name = body['name']
        matches = [p for p in policies if p['name'] == name]
        if len(matches) > 1:
            raise AnsibleFilterError('Duplicate managed policy name: ' + name)
        existing = matches[0] if matches else None
        if existing and existing.get('description') != MARKER:
            raise AnsibleFilterError('Policy name is already operator-owned: ' + name)
        plan.append(dict(body=body, id=existing['id'] if existing else None,
                         changed=not existing or policy_body(existing) != policy_body(body)))

    # Identify the upstream default by its all-groups rule. Its description can
    # vary between NetBird releases and deployments, so it is not a safety key.
    defaults = [p for p in policies if p['name'] == 'Default']
    if len(defaults) > 1:
        raise AnsibleFilterError('Multiple Default policies; review them in the dashboard.')
    default = defaults[0] if defaults else None
    if default and default['enabled']:
        rules = default['rules']
        normalized = policy_body(default)
        default_rule = normalized['rules'][0] if len(normalized.get('rules', [])) == 1 else {}
        if (len(rules) != 1 or
                rules[0].get('name') != 'Default' or
                any(default_rule.get(key) != value for key, value in {
                    'enabled': True, 'action': 'accept', 'protocol': 'all', 'bidirectional': True,
                    'sources': [ids['All']], 'destinations': [ids['All']],
                }.items()) or rules[0].get('ports') or rules[0].get('port_ranges') or
                normalized.get('source_posture_checks')):
            raise AnsibleFilterError('Default policy was customized; review and disable it explicitly.')
    return dict(managed=plan, default=default)


def monitoring_plan(groups, peers, policies, monitor_ip, server_ip):
    """Restrict one monitoring peer to one isolated target on TCP 9100."""
    from ipaddress import IPv4Address, IPv4Network
    network = IPv4Network('100.91.0.0/16')
    if (monitor_ip == server_ip or IPv4Address(monitor_ip) not in network
            or IPv4Address(server_ip) not in network):
        raise AnsibleFilterError('Monitoring requires distinct isolated VPN peers')
    environment = [g for g in groups if g['name'] == 'test-servers']
    if len(environment) != 1:
        raise AnsibleFilterError('Monitoring requires the isolated test-servers group')
    members = {p['id'] if isinstance(p, dict) else p for p in environment[0].get('peers', [])}
    planned = []
    for name, address in [('hou1-monitoring-test', monitor_ip), ('hou1-metrics-test', server_ip)]:
        matches = [p for p in peers if p['ip'] == address]
        if len(matches) != 1 or matches[0]['id'] not in members:
            raise AnsibleFilterError('Metrics peers must be unique enrolled test servers')
        peer = matches[0]['id']
        found = [g for g in groups if g['name'] == name]
        if len(found) > 1:
            raise AnsibleFilterError('Duplicate metrics group')
        current = found[0] if found else None
        if current and {p['id'] if isinstance(p, dict) else p for p in current.get('peers', [])} != {peer}:
            raise AnsibleFilterError('Existing metrics group has unexpected members; review manually')
        planned.append(dict(id=current['id'] if current else None, body=dict(name=name, peers=[peer])))
    policy = None
    if all(g['id'] for g in planned):
        name = 'hou1-monitoring-test-metrics'
        marker = 'Managed by hou1-iac monitoring.'
        body = dict(name=name, description=marker, enabled=True, source_posture_checks=[], rules=[dict(
            name=name, description=marker, enabled=True, action='accept', protocol='tcp',
            bidirectional=False, sources=[planned[0]['id']], destinations=[planned[1]['id']], ports=['9100'])])
        found = [p for p in policies if p['name'] == name]
        if len(found) > 1 or (found and found[0].get('description') != marker):
            raise AnsibleFilterError('Duplicate or operator-owned monitoring policy')
        current = found[0] if found else None
        policy = dict(body=body, id=current['id'] if current else None,
                      changed=not current or policy_body(current) != policy_body(body))
    return dict(groups=planned, policy=policy)


class FilterModule:
    def filters(self):
        return {'netbird_policy_body': policy_body, 'netbird_policy_plan': policy_plan, 'netbird_monitoring_plan': monitoring_plan}
