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


def monitoring_plan(groups, peers, policies, monitor_ip, server_ip,
                    subnet, server_group):
    """Restrict one enrolled monitoring peer to one environment target on TCP 9100."""
    from ipaddress import IPv4Address, IPv4Network
    network = IPv4Network(subnet)
    if (server_group not in ('test-servers', 'prod-servers') or not network.subnet_of(IPv4Network('100.64.0.0/10'))
            or monitor_ip == server_ip or any(IPv4Address(ip) not in network
            or IPv4Address(ip) in (network.network_address, network.broadcast_address)
            for ip in (monitor_ip, server_ip))):
        raise AnsibleFilterError('Monitoring requires distinct environment VPN peers')
    suffix = 'test' if server_group == 'test-servers' else 'production'
    environment = [g for g in groups if g['name'] == server_group]
    if len(environment) != 1:
        raise AnsibleFilterError('Monitoring requires exactly one declared environment server group')
    members = {p['id'] if isinstance(p, dict) else p for p in environment[0].get('peers', [])}
    planned = []
    for name, address in [('hou1-monitoring-' + suffix, monitor_ip), ('hou1-metrics-' + suffix, server_ip)]:
        matches = [p for p in peers if p['ip'] == address]
        if len(matches) != 1 or matches[0]['id'] not in members:
            raise AnsibleFilterError('Metrics peers must be unique enrolled environment servers')
        peer = matches[0]['id']
        found = [g for g in groups if g['name'] == name]
        if len(found) > 1:
            raise AnsibleFilterError('Duplicate metrics group')
        current = found[0] if found else None
        if current and {p['id'] if isinstance(p, dict) else p for p in (current.get('peers') or [])} != {peer}:
            raise AnsibleFilterError('Existing metrics group has unexpected members; review manually')
        planned.append(dict(id=current['id'] if current else None, body=dict(name=name, peers=[peer])))
    policy = None
    if all(g['id'] for g in planned):
        name = 'hou1-monitoring-' + suffix + '-metrics'
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


def backup_plan(groups, peers, policies, receiver_ip, source_ip, subnet, environment):
    """Only one enrolled receiver can reach one source on TCP22; never grant admin membership."""
    from ipaddress import IPv4Address, IPv4Network
    network = IPv4Network(subnet)
    if environment not in ('test', 'production') or receiver_ip == source_ip or any(
            IPv4Address(ip) not in network for ip in (receiver_ip, source_ip)):
        raise AnsibleFilterError('Backup transport requires distinct private environment peers')
    parent = [g for g in groups if g['name'] == ('test-servers' if environment == 'test' else 'prod-servers')]
    if len(parent) != 1: raise AnsibleFilterError('Missing backup environment group')
    members = {p['id'] if isinstance(p, dict) else p for p in parent[0].get('peers', [])}
    planned = []
    for suffix, address in [('receiver', receiver_ip), ('source', source_ip)]:
        found_peers = [p for p in peers if p['ip'] == address]
        if len(found_peers) != 1 or found_peers[0]['id'] not in members:
            raise AnsibleFilterError('Backup peers must be unique enrolled environment servers')
        name = 'hou1-backup-' + environment + '-' + suffix
        matches = [g for g in groups if g['name'] == name]
        peer_id = found_peers[0]['id']
        if len(matches) > 1 or (matches and {p['id'] if isinstance(p, dict) else p for p in (matches[0].get('peers') or [])} != {peer_id}):
            raise AnsibleFilterError('Existing backup group has unexpected membership')
        planned.append(dict(id=matches[0]['id'] if matches else None, body=dict(name=name, peers=[peer_id])))
    policy = None
    if all(g['id'] for g in planned):
        name = 'hou1-backup-' + environment + '-ssh'
        marker = 'Managed by hou1-iac backups.'
        body = dict(name=name, description=marker, enabled=True, source_posture_checks=[], rules=[dict(
            name=name, description=marker, enabled=True, action='accept', protocol='tcp',
            bidirectional=False, sources=[planned[0]['id']], destinations=[planned[1]['id']], ports=['22'])])
        matches = [p for p in policies if p['name'] == name]
        if len(matches) > 1 or (matches and matches[0].get('description') != marker):
            raise AnsibleFilterError('Backup policy collides with an operator policy')
        current = matches[0] if matches else None
        policy = dict(body=body, id=current['id'] if current else None,
                      changed=not current or policy_body(current) != policy_body(body))
    return dict(groups=planned, policy=policy)


def recovery_source_groups(groups, peers, policies, monitor_ip, server_ip, subnet, lost_peer_id):
    """Pin existing managed source groups before retiring a lost test server."""
    metrics = monitoring_plan(groups, peers, policies, monitor_ip, server_ip, subnet, 'test-servers')
    backups = backup_plan(groups, peers, policies, monitor_ip, server_ip, subnet, 'test')
    selected = []
    for plan in (metrics, backups):
        if plan['policy'] is None or plan['policy']['changed']:
            raise AnsibleFilterError('Recovery requires the existing exact managed monitoring and backup policies')
        source = plan['groups'][1]
        if source['id'] is None or source['body']['peers'] != [lost_peer_id]:
            raise AnsibleFilterError('Recovery group does not belong only to the pinned lost peer')
        current = next(group for group in groups if group['id'] == source['id'])
        if current.get('resources'):
            raise AnsibleFilterError('Recovery cannot reassign a group with resource grants')
        selected.append(dict(id=source['id'], name=source['body']['name'], policy=plan['policy']))
    return selected


class FilterModule:
    def filters(self):
        return {'netbird_backup_plan': backup_plan, 'netbird_policy_body': policy_body, 'netbird_policy_plan': policy_plan, 'netbird_monitoring_plan': monitoring_plan, 'netbird_recovery_source_groups': recovery_source_groups}
