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
        admin, staff = self.desired
        self.assertEqual(admin['rules'][0]['ports'], ['22', '443'])
        self.assertEqual(staff['rules'][0]['ports'], ['443'])
        for policy in self.desired:
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
        self.assertEqual(len(result['managed']), 2)
        self.assertEqual(unrelated, before)

    def test_customized_default_is_not_disabled(self):
        default = {'id': 'default', 'name': 'Default', 'enabled': True,
                   'description': MODULE['DEFAULT_DESCRIPTION'],
                   'rules': [{'name': 'Default', 'enabled': True, 'action': 'accept',
                              'protocol': 'all', 'bidirectional': True,
                              'sources': ['All-id'], 'destinations': ['All-id']}]}
        self.assertEqual(plan(self.groups, [default])['default'], default)
        for change in [{'description': 'Customized'}, {'rules': []}]:
            with self.assertRaises(AnsibleFilterError):
                plan(self.groups, [dict(default, **change)])
        changed = deepcopy(default)
        changed['rules'][0]['sources'] = ['staff-id']
        with self.assertRaises(AnsibleFilterError):
            plan(self.groups, [changed])


if __name__ == '__main__':
    unittest.main()
