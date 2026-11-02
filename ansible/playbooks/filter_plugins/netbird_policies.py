"""Translate API policy responses and plan the two managed access policies."""
from copy import deepcopy

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
    return body


def policy_plan(groups, policies):
    ids = {}
    for name in ('test-servers', 'prod-servers', 'administrators', 'staff', 'All'):
        matches = [g for g in groups if g['name'] == name]
        if len(matches) != 1:
            raise AnsibleFilterError('Policies require exactly one group named ' + name)
        ids[name] = matches[0]['id']
    plan = []
    for source, ports in [('administrators', ['22', '443']), ('staff', ['443'])]:
        name = 'hou1-' + source
        body = dict(name=name, description=MARKER, enabled=True, source_posture_checks=[], rules=[dict(
            name=name, description=MARKER, enabled=True, action='accept', protocol='tcp',
            bidirectional=False, sources=[ids[source]],
            destinations=sorted([ids['test-servers'], ids['prod-servers']]), ports=ports)])
        matches = [p for p in policies if p['name'] == name]
        if len(matches) > 1:
            raise AnsibleFilterError('Duplicate managed policy name: ' + name)
        existing = matches[0] if matches else None
        if existing and existing.get('description') != MARKER:
            raise AnsibleFilterError('Policy name is already operator-owned: ' + name)
        plan.append(dict(body=body, id=existing['id'] if existing else None,
                         changed=not existing or policy_body(existing) != body))

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


class FilterModule:
    def filters(self):
        return {'netbird_policy_body': policy_body, 'netbird_policy_plan': policy_plan}
