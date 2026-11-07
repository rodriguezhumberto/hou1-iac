"""The test approval switch may change only after effective IdP admission checks."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import textwrap
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('admission', Path(__file__).resolve().parents[1] / 'scripts/rehearsal-netbird-admission.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


@unittest.skipUnless(shutil.which('sops') and shutil.which('age-keygen'), 'SOPS/age binaries required')
class EncryptedConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / 'age.key'
        subprocess.run(['age-keygen', '-o', str(self.key)], check=True, capture_output=True)
        recipient = subprocess.run(['age-keygen', '-y', str(self.key)],
                                   check=True, capture_output=True, text=True).stdout.strip()
        self.config = {'environment': 'test', 'google_emails': ['private@example.invalid'],
                       'staff_users': [{'email': 'private@example.invalid', 'role': 'admin'}]}
        self.encrypted = self.root / 'admission.enc.json'
        result = subprocess.run(['sops', '--encrypt', '--config', '/dev/null', '--age', recipient,
                                 '--input-type', 'json', '--output-type', 'json', '/dev/stdin'],
                                input=json.dumps(self.config), text=True, capture_output=True, check=True)
        self.encrypted.write_text(result.stdout)

    def test_real_sops_round_trip_hides_emails_and_roles_and_supports_environment_key(self):
        ciphertext = self.encrypted.read_text()
        self.assertNotIn('private@example.invalid', ciphertext)
        self.assertNotIn('"admin"', ciphertext)
        self.assertEqual(m.load_encrypted_config(self.encrypted, self.key), self.config)
        with patch.dict(os.environ, {'SOPS_AGE_KEY_FILE': str(self.key)}):
            self.assertEqual(m.load_encrypted_config(self.encrypted), self.config)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['admission.enc.json', 'age.key'])

    def test_plaintext_wrong_key_and_tampering_fail_without_disclosing_values(self):
        wrong_key = self.root / 'wrong.key'
        subprocess.run(['age-keygen', '-o', str(wrong_key)], check=True, capture_output=True)
        plaintext = self.root / 'plaintext.json'
        plaintext.write_text(json.dumps(self.config))
        # A well-formed encrypted document with an altered encrypted field must
        # fail MAC validation even when the identity can decrypt the data key.
        tampered = self.root / 'tampered.enc.json'
        document = json.loads(self.encrypted.read_text())
        document['google_emails'] = ['leaked@example.invalid']
        tampered.write_text(json.dumps(document))
        for path, key in [(plaintext, self.key), (self.encrypted, wrong_key), (tampered, self.key)]:
            with self.subTest(path=path.name), self.assertRaises(ValueError) as caught:
                m.load_encrypted_config(path, key)
            self.assertEqual(str(caught.exception), 'Encrypted configuration could not be decrypted and validated')
            self.assertTrue(caught.exception.__suppress_context__)

    def test_missing_identity_fails_before_running_sops(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Supply --age-key-file'):
                m.load_encrypted_config(self.encrypted)
            run.assert_not_called()


def evaluate(expression, user, context=None):
    ns = {'request': SimpleNamespace(user=SimpleNamespace(is_authenticated=True, **user)), 'context': context or {}}
    exec('def test():\n' + textwrap.indent(expression, '    '), ns)
    return ns['test']()


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.config = {'environment': 'test', 'google_emails': ['allowed@gmail.com'],
                       'fixture_users': ['test-admin'], 'authentik_issuer': 'https://auth-test.example.invalid/application/o/netbird-test/',
                       'netbird_subnet': '100.91.0.0/16'}
        self.users = [
            {'pk': 1, 'username': 'allowed', 'email': 'allowed@gmail.com', 'path': 'goauthentik.io/sources/google', 'type': 'external', 'is_active': True},
            {'pk': 2, 'username': 'test-admin', 'email': 'admin@example.invalid', 'path': 'users', 'type': 'internal', 'is_active': True},
            {'pk': 3, 'username': 'other', 'email': 'other@gmail.com', 'path': 'goauthentik.io/sources/google', 'type': 'external', 'is_active': True},
        ]
        self.policies = [{'pk': 'google', 'name': 'Google approved accounts', 'expression': ''}]
        self.bindings = [{'pk': 'g-' + f, 'target': f, 'policy': 'google', 'enabled': True, 'negate': False} for f in ['login', 'enroll']]
        self.app = {'pk': 'app', 'policy_engine_mode': 'any'}
        self.flows = [{'pk': f, 'policy_engine_mode': 'all'} for f in ['login', 'enroll']]
        self.account = {'id': 'account', 'settings': {'network_range': '100.91.0.0/16', 'local_auth_disabled': False,
            'extra': {'user_approval_required': True, 'peer_approval_enabled': True}}}
        self.nb_users = [{'id': str(i), 'email': u['email'], 'role': 'user', 'idp_id': 'connector', 'pending_approval': True} for i, u in enumerate([self.users[0], self.users[2]])]
        self.nb_calls = []
        self.fail_policy = False

    def auth(self, method, path, body=None):
        if path.startswith('core/users/?'): return {'results': copy.deepcopy(self.users)}
        if path == 'sources/oauth/google/': return {'enabled': True, 'provider_type': 'google', 'authentication_flow': 'login', 'enrollment_flow': 'enroll'}
        if path.startswith('flows/instances/?'): return {'results': self.flows}
        if path == 'core/applications/netbird-test/':
            if method == 'PATCH': self.app.update(body)
            return self.app
        if path == 'policies/expression/?page_size=1000': return {'results': copy.deepcopy(self.policies)}
        if path == 'policies/bindings/?page_size=1000': return {'results': copy.deepcopy(self.bindings)}
        if path in ['policies/expression/', 'policies/bindings/']:
            obj = {'pk': 'managed', **body}
            (self.policies if 'expression' in path else self.bindings).append(obj)
            return obj
        if path.startswith('policies/expression/'):
            obj = next(p for p in self.policies if p['pk'] == path.split('/')[2]);obj.update(body);return obj
        if path.startswith('policies/bindings/'):
            obj = next(p for p in self.bindings if p['pk'] == path.split('/')[2]);obj.update(body);return obj
        if path.endswith('/test/'):
            policy = next(p for p in self.policies if p['pk'] == path.split('/')[2])
            user = next(u for u in self.users if u['pk'] == body['user'])
            return {'passing': False if self.fail_policy else evaluate(policy['expression'], user, body.get('context'))}
        raise AssertionError((method, path))

    def netbird(self, method, path, body=None):
        self.nb_calls.append((method, path, copy.deepcopy(body)))
        if path == '/identity-providers': return [{'id': 'connector', 'issuer': self.config['authentik_issuer'], 'type': 'authentik'}]
        if path == '/accounts': return [copy.deepcopy(self.account)]
        if path == '/accounts/account': self.account['settings'] = body['settings'];return self.account
        if path == '/users': return copy.deepcopy(self.nb_users)
        if path.endswith('/approve'):
            user = next(u for u in self.nb_users if u['id'] == path.split('/')[2])
            user['pending_approval'] = False
            return copy.deepcopy(user)
        raise AssertionError((method, path))

    def test_only_allowlisted_users_join_and_other_settings_and_roles_survive(self):
        report = m.configure(self.config, self.auth, self.netbird)
        self.assertEqual(report['existing_allowlisted_pending_users_reconciled'], 1)
        self.assertFalse(self.account['settings']['extra']['user_approval_required'])
        self.assertTrue(self.account['settings']['extra']['peer_approval_enabled'])
        self.assertFalse(self.account['settings']['local_auth_disabled'])
        self.assertEqual([u['role'] for u in self.nb_users], ['user', 'user'])
        self.assertEqual([u['pending_approval'] for u in self.nb_users], [False, True])
        m.configure(self.config, self.auth, self.netbird)
        self.assertEqual(len(self.policies), 2)
        self.assertEqual(len(self.bindings), 3)

    def test_unsafe_source_or_failed_live_policy_check_keeps_manual_approval(self):
        for fault in ['source-flow', 'live-policy']:
            with self.subTest(fault=fault):
                self.setUp()
                if fault == 'source-flow': self.flows[0]['policy_engine_mode'] = 'any'
                else: self.fail_policy = True
                with self.assertRaises(ValueError): m.configure(self.config, self.auth, self.netbird)
                self.assertEqual(self.nb_calls, [])
                self.assertTrue(self.account['settings']['extra']['user_approval_required'])

    def test_application_policy_rejects_inactive_spoofed_and_unlisted_accounts(self):
        expression = m.admission_expression(['allowed@gmail.com'], [2])
        for overrides in [{'is_active': False}, {'path': 'users'}, {'email': 'other@gmail.com'}]:
            self.assertFalse(evaluate(expression, {**self.users[0], **overrides}))
        self.assertTrue(evaluate(expression, self.users[0]))
        self.assertTrue(evaluate(expression, self.users[1]))

    def test_refuses_production_or_unbounded_google_admission(self):
        for overrides in [{'environment': 'prod'}, {'google_emails': []}, {'google_emails': ['*@gmail.com']}, {'fixture_users': ['allowed']}, {'staff_users': [{'email': 'other@gmail.com', 'role': 'admin'}]}, {'staff_users': [{'email': 'allowed@gmail.com', 'role': 'owner'}]}]:
            self.setUp()
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                m.configure({**self.config, **overrides}, self.auth, self.netbird)
            self.assertEqual(self.nb_calls, [])


class StaffAccessTest(unittest.TestCase):
    def test_app_admin_gets_staff_network_access_without_network_admin_privileges(self):
        user = {'pk': 7, 'email': 'allowed@gmail.com', 'is_active': True,
                'path': 'goauthentik.io/sources/google', 'groups': ['unrelated', 'reader']}
        nb_user = {'id': 'human', 'email': user['email'], 'idp_id': 'connector',
                   'role': 'user', 'is_blocked': False, 'pending_approval': False,
                   'auto_groups': ['existing']}
        peer = {'id': 'device', 'user_id': nb_user['id'], 'groups': [{'id': 'existing'}]}
        updates = []

        def auth(method, path, body=None):
            if path.startswith('core/groups/?'):
                return {'results': [{'pk': role, 'name': name} for role, name in m.STAFF_ROLES.items()]}
            if path == 'core/users/7/':
                if method == 'PATCH': user.update(body);updates.append(('auth', copy.deepcopy(body)))
                return copy.deepcopy(user)
            raise AssertionError((method, path))

        def netbird(method, path, body=None):
            if path == '/groups': return [{'id': 'staff-group', 'name': 'staff'}]
            if path == '/users': return [copy.deepcopy(nb_user)]
            if path == '/peers': return [copy.deepcopy(peer)]
            if path == '/users/human':
                nb_user.update(body)
                peer['groups'] = [{'id': group} for group in nb_user['auto_groups']]
                updates.append(('netbird', copy.deepcopy(body)))
                return nb_user
            raise AssertionError((method, path))

        args = ([{'email': user['email'], 'role': 'admin'}], [user], {'id': 'connector'}, auth, netbird)
        report = m.configure_staff_access(*args)
        self.assertEqual(report, {'staff_users_configured': 1, 'staff_users_awaiting_first_login': 0})
        self.assertEqual(user['groups'], ['admin', 'unrelated'])
        self.assertEqual(nb_user['auto_groups'], ['existing', 'staff-group'])
        self.assertEqual(nb_user['role'], 'user')
        self.assertNotIn('administrators', nb_user['auto_groups'])
        self.assertEqual(len(updates), 2)
        m.configure_staff_access(*args)
        self.assertEqual(len(updates), 2)

    def test_unknown_identity_waits_without_granting_any_role_or_network_access(self):
        def auth(method, path, body=None):
            self.assertEqual(method, 'GET')
            return {'results': [{'pk': r, 'name': n} for r, n in m.STAFF_ROLES.items()]}
        def netbird(method, path, body=None):
            self.assertEqual(method, 'GET')
            return [{'id': 'staff', 'name': 'staff'}] if path == '/groups' else []
        result = m.configure_staff_access([{'email': 'new@gmail.com', 'role': 'reader'}], [], {'id': 'connector'}, auth, netbird)
        self.assertEqual(result['staff_users_awaiting_first_login'], 1)
        self.assertEqual(result['staff_users_configured'], 0)
