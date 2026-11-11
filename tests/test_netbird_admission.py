"""Approval may change only after environment isolation and effective IdP checks."""

import copy
import importlib.util
import io
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
from contextlib import redirect_stdout
from identity_fixtures import identity_profile, identity_names

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
                       'staff_users': [{'email': 'private@example.invalid', 'role': 'admin'}],
                       'IDENTITY_NAMES': identity_names('test')}
        self.encrypted = self.root / 'admission.enc.json'
        result = subprocess.run(['sops', '--encrypt', '--config', '/dev/null', '--age', recipient,
                                 '--input-type', 'json', '--output-type', 'json', '/dev/stdin'],
                                input=json.dumps(self.config), text=True, capture_output=True, check=True)
        self.encrypted.write_text(result.stdout)

    def test_real_sops_round_trip_hides_emails_and_roles_and_supports_environment_key(self):
        ciphertext = self.encrypted.read_text()
        self.assertNotIn('private@example.invalid', ciphertext)
        self.assertTrue(json.loads(ciphertext)['staff_users'][0]['role'].startswith('ENC[AES256_GCM,'))
        for section in ('staff', 'role_groups', 'scope_mapping_names'):
            for name in self.config['IDENTITY_NAMES'][section].values():
                self.assertNotIn(name, ciphertext)
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
        self.profile = identity_profile('test')
        self.config = {'environment': 'test', 'google_emails': ['allowed@gmail.com'],
                       'netbird_url': 'https://netbird-test.example.invalid',
                       'fixture_users': ['test-admin'], 'authentik_issuer': 'https://auth-test.example.invalid/application/o/netbird-test/',
                       'netbird_subnet': '100.91.0.0/16'}
        self.users = [
            {'pk': 1, 'username': 'allowed', 'email': 'allowed@gmail.com', 'path': 'goauthentik.io/sources/google', 'type': 'external', 'is_active': True},
            {'pk': 2, 'username': 'test-admin', 'email': 'admin@example.invalid', 'path': 'users', 'type': 'internal', 'is_active': True},
            {'pk': 3, 'username': 'other', 'email': 'other@gmail.com', 'path': 'goauthentik.io/sources/google', 'type': 'external', 'is_active': True},
        ]
        self.policies = [{'pk': 'google', 'name': 'Google approved accounts', 'expression': ''}]
        self.bindings = [{'pk': 'g-' + f, 'target': f, 'policy': 'google', 'enabled': True, 'negate': False, 'failure_result': False} for f in ['login', 'enroll']]
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
        client = self.profile['netbird']['client_id']
        if path == 'core/applications/' + client + '/':
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
        report = m.configure(self.config, self.auth, self.netbird, self.profile)
        self.assertEqual(report['existing_allowlisted_pending_users_reconciled'], 1)
        self.assertFalse(self.account['settings']['extra']['user_approval_required'])
        self.assertTrue(self.account['settings']['extra']['peer_approval_enabled'])
        self.assertFalse(self.account['settings']['local_auth_disabled'])
        self.assertEqual([u['role'] for u in self.nb_users], ['user', 'user'])
        self.assertEqual([u['pending_approval'] for u in self.nb_users], [False, True])
        m.configure(self.config, self.auth, self.netbird, self.profile)
        self.assertEqual(len(self.policies), 2)
        self.assertEqual(len(self.bindings), 3)

    def test_unsafe_source_or_failed_live_policy_check_keeps_manual_approval(self):
        for fault in ['source-flow', 'live-policy', 'fail-open-binding']:
            with self.subTest(fault=fault):
                self.setUp()
                if fault == 'source-flow': self.flows[0]['policy_engine_mode'] = 'any'
                elif fault == 'fail-open-binding': self.bindings[0]['failure_result'] = True
                else: self.fail_policy = True
                with self.assertRaises(ValueError): m.configure(self.config, self.auth, self.netbird, self.profile)
                self.assertEqual(self.nb_calls, [])
                self.assertTrue(self.account['settings']['extra']['user_approval_required'])

    def test_application_policy_rejects_inactive_spoofed_and_unlisted_accounts(self):
        expression = m.admission_expression(['allowed@gmail.com'], [2])
        for overrides in [{'is_active': False}, {'path': 'users'}, {'email': 'other@gmail.com'}]:
            self.assertFalse(evaluate(expression, {**self.users[0], **overrides}))
        self.assertTrue(evaluate(expression, self.users[0]))
        self.assertTrue(evaluate(expression, self.users[1]))

    def test_refuses_unknown_environment_or_unbounded_google_admission(self):
        for overrides in [{'environment': 'staging'}, {'google_emails': []}, {'google_emails': ['*@gmail.com']}, {'fixture_users': ['allowed']}, {'staff_users': [{'email': 'other@gmail.com', 'role': 'admin'}]}, {'staff_users': [{'email': 'allowed@gmail.com', 'role': 'owner'}]}]:
            self.setUp()
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                m.configure({**self.config, **overrides}, self.auth, self.netbird, self.profile)
            self.assertEqual(self.nb_calls, [])


class ProductionAdmissionTest(AdmissionTest):
    """Run the full admission contract against the production profile too."""
    def setUp(self):
        super().setUp()
        self.profile = identity_profile('prod')
        self.config.update(environment='prod', fixture_users=[],
                           netbird_url='https://netbird.example.invalid',
                           authentik_issuer='https://auth.example.invalid/application/o/netbird-prod/',
                           netbird_subnet='100.90.0.0/16')
        self.account['settings']['network_range'] = self.config['netbird_subnet']

    def test_production_has_no_fixture_bypass_and_preserves_local_emergency_identity(self):
        original = copy.deepcopy(self.users[1])
        report = m.configure(self.config, self.auth, self.netbird, self.profile)
        self.assertEqual(report['environment'], 'prod')
        policy = next(p for p in self.policies if p['name'] == 'HOU1 prod NetBird allowlist')
        self.assertTrue(evaluate(policy['expression'], self.users[0]))
        self.assertFalse(evaluate(policy['expression'], self.users[1]))
        self.assertEqual(self.users[1], original)

    def test_admission_accepts_inventory_values_without_environment_naming_conventions(self):
        self.profile.update(issuer_host='login.identity.invalid', netbird_url='https://vpn.edge.invalid',
                            netbird_subnet='100.100.0.0/16', compose_project='custom-identity',
                            compose_file='/opt/custom/compose.yaml', compose_service='identity-api')
        self.profile['netbird']['client_id'] = 'custom-vpn'
        self.config.update(netbird_url=self.profile['netbird_url'], netbird_subnet=self.profile['netbird_subnet'],
                           authentik_issuer='https://login.identity.invalid/application/o/custom-vpn/')
        self.account['settings']['network_range'] = self.profile['netbird_subnet']
        report = m.configure(self.config, self.auth, self.netbird, self.profile)
        self.assertEqual(report['environment'], 'prod')
        self.assertFalse(self.account['settings']['extra']['user_approval_required'])

    def test_inventory_cannot_enable_a_production_fixture_bypass(self):
        self.profile['allow_fixtures'] = True
        self.config['fixture_users'] = ['test-admin']
        with patch.object(self, 'auth') as auth, patch.object(self, 'netbird') as nb:
            with self.assertRaises(ValueError):
                m.configure(self.config, auth, nb, self.profile)
            auth.assert_not_called()
            nb.assert_not_called()

    def test_cross_environment_inputs_fail_before_either_api_is_called(self):
        bad = [
            {'environment': 'test'}, {'fixture_users': ['test-admin']},
            {'netbird_url': 'https://netbird-test.example.invalid'},
            {'netbird_url': 'https://netbird.example.invalid/'},
            {'netbird_url': 'https://user@netbird.example.invalid'},
            {'netbird_url': 'https://netbird.example.invalid:443'},
            {'netbird_url': 'https://netbird.example.invalid?other=test'},
            {'netbird_url': 'https://netbird.example.invalid#test'},
            {'netbird_url': 'http://netbird.example.invalid'},
            {'authentik_issuer': 'https://auth-test.example.invalid/application/o/netbird-test/'},
            {'authentik_issuer': 'https://auth.example.invalid/application/o/netbird-test/'},
            {'authentik_issuer': 'https://auth.other.invalid/application/o/netbird-prod/'},
            {'netbird_subnet': '100.91.0.0/16'},
        ]
        for override in bad:
            with self.subTest(override=override), patch.object(self, 'auth') as auth, patch.object(self, 'netbird') as nb:
                with self.assertRaises(ValueError):
                    m.configure({**self.config, **override}, auth, nb, self.profile)
                auth.assert_not_called()
                nb.assert_not_called()

    def test_pagination_and_failed_connector_subnet_checks_never_disable_approval(self):
        original_auth, original_nb = self.auth, self.netbird
        for fault in ('users', 'bindings', 'connector', 'subnet'):
            def auth(method, path, body=None):
                value = original_auth(method, path, body)
                if (fault == 'users' and path.startswith('core/users/?')
                        or fault == 'bindings' and path.startswith('policies/bindings/?')):
                    return {**value, 'pagination': {'next': 2}}
                return value

            def netbird(method, path, body=None):
                value = original_nb(method, path, body)
                if fault == 'connector' and path == '/identity-providers':
                    value[0]['issuer'] = 'https://auth-test.example.invalid/application/o/netbird-test/'
                if fault == 'subnet' and path == '/accounts':
                    value[0]['settings']['network_range'] = '100.91.0.0/16'
                return value

            with self.subTest(fault=fault), self.assertRaises(ValueError):
                m.configure(self.config, auth, netbird, self.profile)
            self.assertTrue(self.account['settings']['extra']['user_approval_required'])
            self.assertFalse(any(method != 'GET' for method, _, _ in self.nb_calls))

    def test_cli_discovers_compose_service_and_binds_encrypted_token_to_origin(self):
        config = {**self.config, 'ssh': ['ssh', 'operator@vpn-host'],
                  'netbird_token_file': 'token.enc.json', 'age_key_file': '/private/scoped.key'}
        token = {'netbird_management_url': config['netbird_url'], 'netbird_api_token': 'private-fixture'}

        def configure(c, auth, netbird, profile):
            self.assertEqual(profile, self.profile)
            self.assertEqual(c, config)
            self.assertEqual(auth('GET', 'core/users/'), {})
            return {'environment': 'prod'}

        output = io.StringIO()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        profile_file = Path(temp.name) / 'profile.json'
        profile_file.write_text(json.dumps(identity_profile('prod', private=False)))
        with patch('sys.argv', ['admission', '--config', 'admission.enc.json', '--profile', str(profile_file), '--identity-secrets', 'auth.enc.yaml', '--age-key-file', '/private/scoped.key']), \
                patch.object(m, 'load_encrypted_config', side_effect=[config, {'IDENTITY_NAMES': identity_names('prod')}, token]) as decrypt, \
                patch.object(m, 'configure', side_effect=configure), \
                patch.object(m.subprocess, 'run', side_effect=[SimpleNamespace(stdout='a' * 64), SimpleNamespace(stdout='{}')]) as run, redirect_stdout(output):
            m.main()
        self.assertEqual(decrypt.call_args_list[1].args, ('auth.enc.yaml', '/private/scoped.key'))
        self.assertEqual(decrypt.call_args_list[2].args, ('token.enc.json', '/private/scoped.key'))
        discovery = run.call_args_list[0].args[0][-1]
        self.assertIn('docker compose -p ' + self.profile['compose_project'], discovery)
        self.assertIn('-f ' + self.profile['compose_file'], discovery)
        self.assertIn('ps -q ' + self.profile['compose_service'], discovery)
        command = run.call_args_list[1].args[0]
        self.assertIn('a' * 64, command[-1])
        self.assertNotIn('hou1-authentik-prod-authentik-server-1', command[-1])
        self.assertNotIn(token['netbird_api_token'], str(command) + output.getvalue())
        for container in ('', 'not-a-container-id', 'a' * 64 + '\n' + 'b' * 64):
            with self.subTest(container=container), \
                    patch('sys.argv', ['admission', '--config', 'admission.enc.json', '--profile', str(profile_file), '--identity-secrets', 'auth.enc.yaml']), \
                    patch.object(m, 'load_encrypted_config', side_effect=[config, {'IDENTITY_NAMES': identity_names('prod')}, token]), \
                    patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout=container)) as run:
                with self.assertRaisesRegex(ValueError, 'exactly one running'):
                    m.main()
                self.assertEqual(run.call_count, 1)
        token['netbird_management_url'] = 'https://netbird-test.example.invalid'
        with patch('sys.argv', ['admission', '--config', 'admission.enc.json', '--profile', str(profile_file), '--identity-secrets', 'auth.enc.yaml']), \
                patch.object(m, 'load_encrypted_config', side_effect=[config, {'IDENTITY_NAMES': identity_names('prod')}, token]), \
                patch.object(m, 'configure') as configure, patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'different NetBird deployment'):
                m.main()
            configure.assert_not_called()
            run.assert_not_called()


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
                return {'results': [{'pk': role, 'name': name} for role, name in identity_names()['role_groups'].items()]}
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

        args = ([{'email': user['email'], 'role': 'admin'}], [user], {'id': 'connector'}, auth, netbird, identity_names()['role_groups'])
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
            return {'results': [{'pk': r, 'name': n} for r, n in identity_names()['role_groups'].items()]}
        def netbird(method, path, body=None):
            self.assertEqual(method, 'GET')
            return [{'id': 'staff', 'name': 'staff'}] if path == '/groups' else []
        result = m.configure_staff_access([{'email': 'new@gmail.com', 'role': 'reader'}], [], {'id': 'connector'}, auth, netbird, identity_names()['role_groups'])
        self.assertEqual(result['staff_users_awaiting_first_login'], 1)
        self.assertEqual(result['staff_users_configured'], 0)
