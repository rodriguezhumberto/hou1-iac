"""The health probe must distinguish bounded leases from production bypasses."""
import copy
import runpy
import unittest
from helpers import ROOT

PROBE = runpy.run_path(str(ROOT / 'ansible/playbooks/roles/rehearsal_gates/files/probe.py'))


def match(protocol, op, name):
    return {'match': {'op': op, 'left': {'payload': {
        'protocol': protocol, 'field': 'daddr'}}, 'right': name}}


def deny(protocol, name):
    return {'expr': [match(protocol, '==', name), {'counter': {}}, {'drop': None}]}


def lease(protocol, name, allowed):
    return {'expr': [match(protocol, '!=', name),
                     match(protocol, '==', allowed), {'accept': None}]}


class IsolationProbeTest(unittest.TestCase):
    def setUp(self):
        self.denies = [deny('ip', '@denied4'), deny('ip6', '@denied6')]

    def test_baseline_and_explicitly_excluding_leases(self):
        for prefix in [[],
                       [lease('ip6', '@denied6', '@packages6'),
                        lease('ip', '@denied4', '@packages4')],
                       [lease('ip6', '@denied6', '@registry6'),
                        lease('ip', '@denied4', '@registry4'),
                        lease('ip6', '@denied6', '@packages6'),
                        lease('ip', '@denied4', '@packages4')]]:
            with self.subTest(prefix=prefix):
                self.assertTrue(PROBE['isolation_guard_ready'](prefix + self.denies))

    def test_unconditional_or_destination_allow_before_deny_is_rejected(self):
        for prefix in [[{'expr': [{'accept': None}]}],
                       [{'expr': [match('ip', '==', '@packages4'), {'accept': None}]}]]:
            with self.subTest(prefix=prefix):
                self.assertFalse(PROBE['isolation_guard_ready'](prefix + self.denies))

    def test_wrong_family_or_inverted_exclusion_cannot_hide_bypass(self):
        for protocol, op, name in [('ip6', '!=', '@denied4'), ('ip', '==', '@denied4')]:
            prefix = {'expr': [match(protocol, op, name), {'accept': None}]}
            self.assertFalse(PROBE['isolation_guard_ready']([prefix] + self.denies))

    def test_missing_or_conditionally_restricted_deny_is_rejected(self):
        self.assertFalse(PROBE['isolation_guard_ready'](self.denies[:1]))
        changed = copy.deepcopy(self.denies)
        changed[0]['expr'].insert(1, match('ip', '==', '198.51.100.10'))
        self.assertFalse(PROBE['isolation_guard_ready'](changed))

    def test_terminal_verdict_cannot_precede_exclusion(self):
        prefix = lease('ip', '@denied4', '@packages4')
        prefix['expr'].insert(0, {'accept': None})
        self.assertFalse(PROBE['isolation_guard_ready']([prefix] + self.denies))

    def test_ordinary_accept_after_both_denies_is_safe(self):
        self.assertTrue(PROBE['isolation_guard_ready'](
            self.denies + [{'expr': [{'accept': None}]}]))


if __name__ == '__main__':
    unittest.main()
