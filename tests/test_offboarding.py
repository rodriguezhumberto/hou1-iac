"""Offboarding safety boundaries; fixtures never contain real identities/secrets."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import offboard_remote as worker

spec = importlib.util.spec_from_file_location('offboard', ROOT / 'scripts/offboard-user.py')
co = importlib.util.module_from_spec(spec)
spec.loader.exec_module(co)

OP = '65ed67a8-f61b-497f-a1be-d65aad432ea9'
TARGET = '403626e9-21ef-42bf-9d4c-dcf2e6e970f5'
CONFIG = dict(environment='test', staff_issuer='https://auth-test.example.org/application/o/staff-test/',
              staff_application='staff-test', staff_client_id='staff-test',
              netbird_application='netbird-test', netbird_client_id='netbird-test',
              netbird_issuer='https://auth-test.example.org/application/o/netbird-test/',
              netbird_url='https://netbird-test.example.org', netbird_subnet='100.91.0.0/16',
              ssh=['ssh', '-F', '/private/config', 'fixture'],
              ssh_fingerprint='SHA256:' + 'a' * 43)
REMOTE_CONFIG = {k: CONFIG[k] for k in ('environment', 'staff_issuer', 'staff_application',
                                      'staff_client_id', 'netbird_application', 'netbird_client_id')}
REQUEST = dict(operation_id=OP, target_uuid=TARGET, reason='test-drill', reference='issue-47',
               config=REMOTE_CONFIG, action='begin')
PLAN = dict(target_uuid=TARGET, authentik_pk=42, staff_subject='immutable-sub',
            netbird_subject='immutable-sub', email_sha256=co.digest('fixture@example.invalid'),
            fixture_username_sha256=co.digest('offboard-test-fixture'))


def dex_id(sub='immutable-sub', connector='authentik-fixture'):
    fields = b''.join(bytes([tag, len(value)]) + value.encode() for tag, value in ((10, sub), (18, connector)))
    return base64.urlsafe_b64encode(fields).decode().rstrip('=')


class BindingTests(unittest.TestCase):
    def api(self, connector='authentik-fixture', role='user'):
        def request(method, path):
            return {'/identity-providers': [dict(id='authentik-fixture', issuer=CONFIG['netbird_issuer'],
                                               type='authentik', client_id='netbird-test')],
                    '/accounts': [dict(settings=dict(network_range='100.91.0.0/16'))],
                    '/users': [dict(id=dex_id(connector=connector), idp_id='authentik-fixture',
                                    role=role, is_service_user=False)]}[path]
        return request

    def test_immutable_binding_and_recovery_protection(self):
        self.assertEqual(co.netbird_binding(CONFIG, PLAN, self.api())['id'], dex_id())
        for api in (self.api(connector='wrong'), self.api(role='admin')):
            with self.assertRaises(worker.Failure):
                co.netbird_binding(CONFIG, PLAN, api)

    def test_no_email_or_malformed_id_fallback(self):
        for value in ('fixture@example.invalid', '', 'AAAA', dex_id() + 'AAAA'):
            with self.assertRaises(worker.Failure):
                co.decode_netbird_id(value)

    def test_environment_isolation(self):
        co.validate_config(CONFIG, 'test')
        for values in ({'environment': 'production'}, {'netbird_subnet': '100.90.0.0/16'},
                       {'staff_issuer': 'http://auth-test.example.org/'},
                       {'netbird_url': 'https://netbird-test.example.org/?bad=1'}):
            with self.assertRaises(worker.Failure):
                co.validate_config({**CONFIG, **values}, 'test')

    def test_ssh_pin_refuses_an_alternate_trusted_key(self):
        config = 'hostname fixture\nuser operator\nstricthostkeychecking yes\nidentitiesonly yes\nuserknownhostsfile /private/known_hosts\n'
        known = 'fixture ssh-ed25519 synthetic-key\n'
        expected = '256 ' + CONFIG['ssh_fingerprint'] + ' fixture (ED25519)\n'
        for fingerprints, accepted in ((expected, True), (expected + '256 SHA256:' + 'b' * 43 + ' alternate (ED25519)\n', False)):
            outputs = [subprocess.CompletedProcess([], 0, stdout=v) for v in (config, known, fingerprints)]
            with patch.object(co.subprocess, 'run', side_effect=outputs):
                if accepted:
                    transport, actor = co.pinned_ssh(CONFIG)
                    self.assertEqual(actor, 'operator')
                    self.assertIn('GlobalKnownHostsFile=/dev/null', transport)
                    self.assertIn('KnownHostsCommand=none', transport)
                else:
                    with self.assertRaisesRegex(worker.Failure, 'ssh_fingerprint_mismatch'):
                        co.pinned_ssh(CONFIG)

    def test_nonroot_and_actor_spoofing_rejected(self):
        with patch.object(worker.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(worker.Failure, 'privileged_management_required'):
                worker.actor()
        with self.assertRaisesRegex(worker.Failure, 'invalid_operation_request'):
            worker.validate_request({**REQUEST, 'actor': 'root'})
        with patch.dict(os.environ, {'SUDO_USER': 'root', 'SUDO_UID': '1234'}), patch.object(worker.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(worker.Failure, 'invalid_management_identity'):
                worker.actor()

    def test_authentik_pagination_covers_later_pages(self):
        def api(c, path):
            return {'pagination': {'next': 0 if 'page=2' in path else 2},
                    'results': [2 if 'page=2' in path else 1]}
        with patch.object(worker, 'auth', side_effect=api):
            self.assertEqual(worker.pages(CONFIG, 'core/users/?page_size=1'), [1, 2])

    def test_google_identity_resolves_only_immutable_subjects(self):
        user = dict(uuid=TARGET, pk=42, uid='a' * 64, type='external',
                    path='goauthentik.io/sources/google', is_superuser=False,
                    username='synthetic', email='fixture@example.invalid')

        def api(c, path):
            if path.startswith('core/applications/'):
                return {'provider': 1 if 'staff-test' in path else 2}
            return dict(client_id='staff-test' if '/1/' in path else 'netbird-test',
                        issuer_mode='per_provider', sub_mode='hashed_user_id')

        with patch.object(worker, 'pages', return_value=[user]), patch.object(worker, 'auth', side_effect=api):
            result = worker.resolve(REMOTE_CONFIG, TARGET)
            self.assertEqual(result['staff_subject'], user['uid'])
            self.assertEqual(result['netbird_subject'], user['uid'])
            self.assertIsNone(result['fixture_username_sha256'])
        with self.assertRaisesRegex(worker.Failure, 'mutable_oidc_subject'):
            worker.subject({'sub_mode': 'user_email'}, user)

    def test_recovery_and_ordinary_internal_identities_protected(self):
        for fields in ({'is_superuser': True}, {'type': 'service_account'},
                       {'type': 'internal', 'username': 'recovery'}):
            user = dict(uuid=TARGET, pk=42, uid='a' * 64, type='external',
                        path='goauthentik.io/sources/google', is_superuser=False,
                        username='synthetic', email='fixture@example.invalid')
            user.update(fields)
            with patch.object(worker, 'pages', return_value=[user]), patch.object(worker, 'auth') as api:
                with self.assertRaisesRegex(worker.Failure, 'recovery_or_unsupported_identity'):
                    worker.resolve(REMOTE_CONFIG, TARGET)
                api.assert_not_called()

    def test_static_release_checks_survive_outage_but_refuse_mismatch(self):
        state = {'staff_environment': 'test', 'netbird_image': 'netbirdio/netbird-server:0.82.0'}

        def inspect(command):
            self.assertEqual(command[:2], ['docker', 'inspect'])
            if 'astro-app' in command[-1]:
                return [{'Config': {'Labels': {'io.pearlandpaws.staff-offboarding-schema': '1'},
                                    'Env': ['STAFF_ENVIRONMENT=' + state['staff_environment'],
                                            'STAFF_OIDC_ISSUER=' + CONFIG['staff_issuer']]}}]
            image = ('ghcr.io/goauthentik/server:2025.10.4' if 'authentik' in command[-1]
                     else state['netbird_image'])
            return [{'Config': {'Image': image}}]

        with patch.object(worker, 'run', side_effect=inspect):
            self.assertTrue(worker.capabilities(REMOTE_CONFIG)['persistent_subject_blocking'])
            state['netbird_image'] = 'netbirdio/netbird-server:untested'
            with self.assertRaisesRegex(worker.Failure, 'untested_netbird_release'):
                worker.capabilities(REMOTE_CONFIG)
            state['staff_environment'] = 'production'
            with self.assertRaisesRegex(worker.Failure, 'staff_runtime_identity_mismatch'):
                worker.capabilities(REMOTE_CONFIG)

    def test_cached_plan_cannot_mutate_newly_protected_identity(self):
        user = dict(uuid=TARGET, pk=42, type='service_account', is_superuser=False,
                    username='synthetic', email='fixture@example.invalid', path='service')
        with patch.object(worker, 'auth', return_value=user):
            with self.assertRaisesRegex(worker.Failure, 'recovery_or_unsupported_identity'):
                worker.validated_user(REMOTE_CONFIG, PLAN)

    def test_revocation_checks_owner_and_readback(self):
        records = {p: [dict(pk=1, uuid='session', user=42), dict(pk=2, uuid='other', user={'pk': 43})]
                   for p in ('core/authenticated_sessions/', 'oauth2/authorization_codes/',
                             'oauth2/refresh_tokens/', 'oauth2/access_tokens/')}
        def pages(c, path):
            return list(records[path.split('?')[0]])
        def api(c, path, method):
            self.assertEqual(method, 'DELETE')
            root = path.rsplit('/', 2)[0] + '/'
            records[root] = records[root][1:]
        with patch.object(worker, 'validated_user', return_value={'pk': 42, 'is_active': False}), \
                patch.object(worker, 'pages', side_effect=pages), patch.object(worker, 'auth', side_effect=api):
            self.assertEqual(worker.revoke_idp(CONFIG, PLAN)['revoked_idp_records'], 4)
            self.assertTrue(all(len(v) == 1 and v[0]['pk'] == 2 for v in records.values()))
        with patch.object(worker, 'validated_user', return_value={'pk': 42, 'is_active': False}), \
                patch.object(worker, 'pages', return_value=[{'user': None}]):
            with self.assertRaisesRegex(worker.Failure, 'unknown_idp_record_owner'):
                worker.revoke_idp(CONFIG, PLAN)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = [patch.object(worker, 'AUDIT_BASE', Path(self.tmp.name) / 'operations'),
                        patch.object(worker, 'AUDIT_OWNER', os.getuid())]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_durable_retry_manifest_binding_and_private_audit(self):
        self.assertEqual(worker.manifest(CONFIG, REQUEST, PLAN), PLAN)
        self.assertEqual(worker.manifest(CONFIG, REQUEST), PLAN)
        with self.assertRaisesRegex(worker.Failure, 'operation_identity_mismatch'):
            worker.manifest(CONFIG, {**REQUEST, 'reference': 'another-ticket'})
        worker.append_audit(CONFIG, REQUEST, {'user': 'operator', 'uid': 1000}, 'begin')
        for path in worker.AUDIT_BASE.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        audit = (worker.AUDIT_BASE / 'test/offboarding/audit.jsonl').read_text()
        self.assertEqual(json.loads(audit)['actor']['user'], 'operator')
        self.assertNotIn('example.invalid', audit)

    def test_symlink_audit_rejected(self):
        _, path = worker.paths(CONFIG, OP)
        other = Path(self.tmp.name) / 'other'
        other.write_text('untouched')
        path.symlink_to(other)
        with self.assertRaises(OSError):
            worker.append_audit(CONFIG, REQUEST, {}, 'begin')
        self.assertEqual(other.read_text(), 'untouched')

    def test_audit_failure_prevents_mutation(self):
        worker.manifest(REMOTE_CONFIG, REQUEST, PLAN)
        with patch.object(worker, 'actor', return_value={'user': 'operator'}), \
                patch.object(worker, 'append_audit', side_effect=OSError('disk full')), \
                patch.object(worker, 'auth') as api:
            with self.assertRaises(OSError):
                worker.handle({**REQUEST, 'action': 'disable_authentik'})
            api.assert_not_called()

    def test_retry_does_not_require_live_idp_resolution(self):
        worker.manifest(REMOTE_CONFIG, REQUEST, PLAN)
        with patch.object(worker, 'actor', return_value={'user': 'operator'}), \
                patch.object(worker, 'capabilities') as capabilities, patch.object(worker, 'resolve') as resolve:
            self.assertEqual(worker.handle(REQUEST)['plan'], PLAN)
            capabilities.assert_called_once_with(REMOTE_CONFIG)
            resolve.assert_not_called()


class CoordinatorTests(unittest.TestCase):
    def remote(self, action, **extra):
        self.events.append((action, extra))
        if action in ('begin', 'plan'):
            return {'actor': {'user': 'operator'}, 'plan': PLAN}
        if action in self.fail:
            raise worker.Failure('controlled_service_outage')
        return {}

    def setUp(self):
        self.events, self.fail = [], set()

    def test_partial_outages_attempt_independent_denials(self):
        self.fail = {'block_staff', 'disable_authentik'}
        with patch.object(co, 'netbird_binding', side_effect=co.Unavailable('outage')), \
                patch.object(co, 'remove_admission', return_value={'admission_entries_removed': 1}):
            report = co.execute(CONFIG, REQUEST, self.remote, None, None, apply=True)
        self.assertFalse(report['complete'])
        self.assertEqual(report['steps']['admission']['status'], 'passed')
        self.assertEqual(report['steps']['idp_sessions']['status'], 'passed')
        self.assertTrue(any(action == 'revoke_idp' for action, _ in self.events))
        self.assertEqual(report['steps']['netbird']['status'], 'failed')
        self.assertEqual(self.events[-1], ('audit', {'step': 'complete', 'outcome': 'partial'}))

    def test_controller_audit_failure_stops_all_actions(self):
        self.fail = {'audit'}
        with patch.object(co, 'netbird_binding', return_value=None), patch.object(co, 'remove_admission') as admission:
            report = co.execute(CONFIG, REQUEST, self.remote, None, None, apply=True)
        admission.assert_not_called()
        self.assertFalse(report['complete'])
        self.assertFalse(any(action in ('block_staff', 'disable_authentik', 'revoke_idp') for action, _ in self.events))

    def test_dry_run_never_mutates(self):
        with patch.object(co, 'netbird_binding', return_value=None), patch.object(co, 'remove_admission') as admission:
            report = co.execute(CONFIG, REQUEST, self.remote, None, None)
        admission.assert_not_called()
        self.assertFalse(report['mutations'])
        self.assertEqual([action for action, _ in self.events], ['plan'])

    def test_real_sops_roundtrip_preserves_recipients_and_unrelated_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / 'age.key'
            result = subprocess.run(['age-keygen', '-o', str(key)], capture_output=True, text=True, check=True)
            recipient = result.stderr.strip().split()[-1]
            document = dict(environment='test', netbird_url=CONFIG['netbird_url'],
                            authentik_issuer=CONFIG['netbird_issuer'],
                            google_emails=['fixture@example.invalid', 'unrelated@example.invalid'],
                            fixture_users=['offboard-test-fixture', 'untouched'],
                            staff_users=[{'email': 'fixture@example.invalid', 'role': 'admin'},
                                         {'email': 'unrelated@example.invalid', 'role': 'reader'}])
            path = root / 'admission.enc.json'
            encrypted = subprocess.run(['sops', '-e', '--config', '/dev/null', '--age', recipient,
                                        '--input-type', 'json', '--output-type', 'json', '/dev/stdin'],
                                       input=json.dumps(document), capture_output=True, text=True, check=True)
            path.write_text(encrypted.stdout)
            path.chmod(0o600)
            config = {**CONFIG, 'admission_file': str(path)}
            self.assertEqual(co.remove_admission(config, PLAN, key)['admission_entries_removed'], 3)
            actual = co.sops_read(path, key)
            self.assertEqual(actual['google_emails'], ['unrelated@example.invalid'])
            self.assertEqual(actual['fixture_users'], ['untouched'])
            self.assertEqual(len(actual['staff_users']), 1)
            self.assertEqual(json.loads(path.read_text())['sops']['age'][0]['recipient'], recipient)
            self.assertNotIn('example.invalid', path.read_text())
            self.assertEqual(co.remove_admission(config, PLAN, key)['admission_entries_removed'], 0)


if __name__ == '__main__':
    unittest.main()
