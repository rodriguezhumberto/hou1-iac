"""Policy comparison must detect permission drift without touching unrelated rules."""
from copy import deepcopy
from pathlib import Path
import runpy
import unittest

from ansible.errors import AnsibleFilterError

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                           'ansible/playbooks/filter_plugins/netbird_policies.py'))
plan = MODULE['policy_plan']


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.groups = [{'id': name + '-id', 'name': name} for name in
                       ['test-servers', 'prod-servers', 'administrators', 'staff', 'All']]
        self.desired = [dict(id='policy-' + str(i), **item['body'])
                        for i, item in enumerate(plan(self.groups, [])['managed'])]

    def test_access_and_response_normalization(self):
        admin, staff = self.desired[:2]
        self.assertEqual(admin['rules'][0]['ports'], ['22', '443'])
        self.assertEqual(staff['rules'][0]['ports'], ['443'])
        for policy in self.desired:
            self.assertEqual(len(policy['rules']), 1)
        for policy, protocol in zip(self.desired[2:], ('tcp', 'udp')):
            rule = policy['rules'][0]
            self.assertEqual(rule['protocol'], protocol)
            self.assertEqual(rule['ports'], ['1053'])
            self.assertEqual(rule['sources'], ['administrators-id', 'staff-id'])
            self.assertEqual(rule['destinations'], ['prod-servers-id'])
            self.assertFalse(rule['bidirectional'])
        for policy in self.desired[:2]:
            rule = policy['rules'][0]
            self.assertFalse(rule['bidirectional'])
            self.assertEqual(rule['protocol'], 'tcp')
            self.assertEqual(set(rule['destinations']), {'test-servers-id', 'prod-servers-id'})
            rule['id'] = 'server-generated-rule-id'
            rule['ports'].reverse()
            for key in ['sources', 'destinations']:
                rule[key] = [{'id': value, 'name': 'display-name'} for value in reversed(rule[key])]
            policy['source_posture_checks'] = None
        self.assertFalse(any(p['changed'] for p in plan(self.groups, self.desired)['managed']))

    def test_permission_drift_is_reconciled(self):
        for key, value in [('ports', ['22', '443']), ('protocol', 'all'),
                           ('bidirectional', True), ('enabled', False),
                           ('sources', ['All-id']), ('destinations', ['All-id'])]:
            with self.subTest(key=key):
                policies = deepcopy(self.desired)
                policies[1]['rules'][0][key] = value
                result = plan(self.groups, policies)['managed']
                self.assertFalse(result[0]['changed'])
                self.assertTrue(result[1]['changed'])
        policies = deepcopy(self.desired)
        policies[1]['rules'].append(deepcopy(policies[1]['rules'][0]))
        self.assertTrue(plan(self.groups, policies)['managed'][1]['changed'])

    def test_dns_port_is_configurable_and_old_port_is_replaced(self):
        old = deepcopy(self.desired)
        for policy in old[2:]:
            policy['rules'][0]['ports'] = ['53']
        result = plan(self.groups, old, 1053)['managed']
        self.assertFalse(any(p['changed'] for p in result[:2]))
        self.assertTrue(all(p['changed'] for p in result[2:]))
        for policy in plan(self.groups, [], 1054)['managed'][2:]:
            self.assertEqual(policy['body']['rules'][0]['ports'], ['1054'])

    def test_legacy_multi_rule_policies_are_replaced_without_assuming_missing_sources(self):
        old = deepcopy(self.desired[:2])
        for policy in old:
            for dns in self.desired[2:]:
                rule = deepcopy(dns['rules'][0])
                rule['sources'] = None  # NetBird 0.82 omits the repeated source group.
                policy['rules'].append(rule)
        result = plan(self.groups, old)['managed']
        self.assertTrue(all(p['changed'] for p in result))
        self.assertEqual([p['id'] for p in result[:2]], [p['id'] for p in old])
        self.assertTrue(all(len(p['body']['rules']) == 1 for p in result))
        broken = deepcopy(self.desired)
        broken[0]['rules'][0]['sources'] = None
        self.assertTrue(plan(self.groups, broken)['managed'][0]['changed'])

    def test_name_conflicts_and_missing_groups_fail(self):
        for policies in [self.desired + [self.desired[0]],
                         [dict(self.desired[0], description='Operator policy')]]:
            with self.assertRaises(AnsibleFilterError):
                plan(self.groups, policies)
        with self.assertRaises(AnsibleFilterError):
            plan(self.groups[:-1], [])
        unrelated = {'id': 'custom', 'name': 'custom', 'enabled': True, 'rules': []}
        before = deepcopy(unrelated)
        result = plan(self.groups, self.desired + [unrelated])
        self.assertEqual(len(result['managed']), 4)
        self.assertEqual(unrelated, before)

    def test_customized_default_is_not_disabled(self):
        default = {'id': 'default', 'name': 'Default', 'enabled': True,
                   'description': 'This is the default policy',
                   'rules': [{'name': 'Default', 'enabled': True, 'action': 'accept',
                              'protocol': 'all', 'bidirectional': True,
                              'sources': ['All-id'], 'destinations': ['All-id']}]}
        self.assertEqual(plan(self.groups, [default])['default'], default)
        for change in [{'rules': []}]:
            with self.assertRaises(AnsibleFilterError):
                plan(self.groups, [dict(default, **change)])
        changed = deepcopy(default)
        changed['rules'][0]['sources'] = ['staff-id']
        with self.assertRaises(AnsibleFilterError):
            plan(self.groups, [changed])


if __name__ == '__main__':
    unittest.main()
