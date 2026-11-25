"""Explicit test credentials update only declared, existing non-superusers."""
from contextlib import nullcontext
import importlib.util
import unittest
from unittest.mock import Mock

import yaml

from helpers import ROLES, guard_play, run_plays

SPEC = importlib.util.spec_from_file_location('test_identities', ROLES / 'authentik/files/seed-test-users.py')
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


class IdentitySeedTest(unittest.TestCase):
    def model(self, flags=()):
        user = Mock(is_active=True, is_superuser=False)
        user.check_password.return_value = False
        for name, value in flags:
            setattr(user, name, value)
        model = Mock()
        model.objects.select_for_update.return_value.get.return_value = user
        return model, user

    def test_existing_test_password_changes_once_then_is_a_noop(self):
        model, user = self.model()
        records = [{'username': 'fixture-reader', 'password': 'a' * 36}]
        report = seed.seed_users(records, model, nullcontext)
        self.assertEqual(report, {'changed': True, 'users': 1, 'grants_changed': False})
        user.set_password.assert_called_once_with('a' * 36)
        user.save.assert_called_once_with()
        user.check_password.return_value = True
        user.reset_mock()
        self.assertFalse(seed.seed_users(records, model, nullcontext)['changed'])
        user.set_password.assert_not_called()
        user.save.assert_not_called()

    def test_all_users_are_checked_before_any_password_changes(self):
        model, first = self.model()
        second = Mock(is_active=True, is_superuser=True)
        model.objects.select_for_update.return_value.get.side_effect = [first, second]
        records = [{'username': name, 'password': 'a' * 36} for name in ('reader', 'administrator')]
        with self.assertRaises(ValueError):
            seed.seed_users(records, model, nullcontext)
        first.set_password.assert_not_called()
        second.set_password.assert_not_called()
        model.objects.create.assert_not_called()

    def test_invalid_records_and_inactive_users_are_refused(self):
        model, user = self.model([('is_active', False)])
        for records in ([], [{'username': 'reader', 'password': 'short'}],
                        [{'username': 'reader', 'password': 'a' * 36, 'groups': ['admin']}],
                        [{'username': 'reader', 'password': 'a' * 36}] * 2):
            with self.subTest(records=records), self.assertRaises(ValueError):
                seed.seed_users(records, model, nullcontext)
        model.objects.select_for_update.assert_not_called()
        with self.assertRaises(ValueError):
            seed.seed_users([{'username': 'reader', 'password': 'a' * 36}], model, nullcontext)
        user.set_password.assert_not_called()

    def test_production_and_unspecified_sources_fail_before_decryption(self):
        tasks = yaml.safe_load((ROLES / 'authentik/tasks/seed-test-users.yml').read_text())
        guard = tasks[0]
        inventory = {'all': {'children': {'test_env': {'hosts': {'fixture': {}}}}}}
        base = dict(cms_environment='test', authentik_environment='test',
                    authentik_test_seed_source='/private/lab/test-identities.enc.json')
        plays = [dict(guard_play({**base, **change}, [guard], guard['name'] if change else None), hosts='fixture')
                 for change in ({}, {'cms_environment': 'production'}, {'authentik_environment': 'prod'},
                                {'authentik_test_seed_source': ''})]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        block = tasks[1]
        self.assertTrue(block['no_log'])
        command = next(t['ansible.builtin.command'] for t in block['block'] if 'ansible.builtin.command' in t)
        self.assertIn('stdin', command)
        self.assertNotIn('password', ' '.join(command['argv']))


if __name__ == '__main__':
    unittest.main()
