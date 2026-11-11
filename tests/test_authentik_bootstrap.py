from pathlib import Path
import json
import importlib.util
import py_compile
import unittest
from unittest.mock import patch
from functools import partial
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import tempfile
import yaml

from jinja2 import Environment
from helpers import run_play
from identity_fixtures import identity_profile, identity_manifest

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'ansible/playbooks/roles/authentik'
SPEC = importlib.util.spec_from_file_location('authentik_bootstrap', ROLE / 'files/bootstrap.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class FixtureAPI(bootstrap.AuthentikAPI):
    """Exercise reconciliation against the pinned API's identity/lookup rules."""
    def __init__(self):
        self.objects = {
            'flows/instances': [
                {'pk': 'authorize', 'slug': 'default-provider-authorization-implicit-consent'},
                {'pk': 'invalidate', 'slug': 'default-provider-invalidation-flow'},
            ],
            'crypto/certificatekeypairs': [{'pk': 'rsa-key', 'name': 'authentik Self-signed Certificate'}],
            'propertymappings/provider/scope': [
                {'pk': scope, 'name': 'standard ' + scope, 'scope_name': scope}
                for scope in ['openid', 'profile', 'email']
            ],
        }
        self.writes = []

    def request(self, method, path, payload=None):
        parsed = urlsplit(path)
        parts = parsed.path.strip('/').split('/')
        if method == 'GET':
            resource = '/'.join(parts)
            if resource == 'policies/bindings' and 'target' in parse_qs(parsed.query):
                # The pinned API's filter uses the inherited model UUID, not
                # the child flow UUID used by its serializer and POST payload.
                parent_ids = {f.get('pbm_uuid') for f in self.objects['flows/instances']}
                assert all(target in parent_ids for target in parse_qs(parsed.query)['target'])
            if resource == 'core/applications':
                assert parse_qs(parsed.query)['superuser_full_list'] == ['true']
            # Authentik's search filters can return partial matches.
            return {'results': self.objects.get(resource, [])}
        self.writes.append((method, path, payload))
        if parts[-1] == 'add_user':
            return {}
        if method == 'PATCH':
            resource, lookup = '/'.join(parts[:-1]), parts[-1]
            if resource == 'sources/oauth':
                assert payload['provider_type'] == 'google'
            key = 'slug' if resource in ('core/applications', 'sources/oauth', 'flows/instances') else 'pk'
            obj = next(obj for obj in self.objects[resource] if obj[key] == lookup)
            obj.update(payload)
            return obj
        resource = '/'.join(parts)
        if resource == 'providers/oauth2':
            assert payload['authorization_flow'] == 'authorize'
            assert payload['invalidation_flow'] == 'invalidate'
            assert payload['signing_key'] == 'rsa-key'
            assert payload['issuer_mode'] == 'per_provider'
            assert all(item['matching_mode'] == 'strict' and item['url'].startswith(('https://', 'http://localhost:'))
                       for item in payload['redirect_uris'])
        obj = {'pk': str(len(self.writes)), **payload}
        self.objects.setdefault(resource, []).append(obj)
        return obj


class AuthentikBootstrapTest(unittest.TestCase):
    def google_api(self):
        api = FixtureAPI()
        api.objects['flows/instances'] += [
            {'pk': 'source-auth', 'pbm_uuid': 'auth-parent', 'slug': 'default-source-authentication', 'policy_engine_mode': 'any'},
            {'pk': 'source-enroll', 'pbm_uuid': 'enroll-parent', 'slug': 'default-source-enrollment', 'policy_engine_mode': 'any'},
        ]
        api.objects['stages/identification'] = [
            {'pk': 'identify', 'name': 'default-authentication-identification', 'sources': ['other-source']}]
        api.objects['stages/user_write'] = [
            {'pk': 'write', 'name': 'default-source-enrollment-write', 'user_type': 'external'}]
        return api

    def google_manifest(self, environment='test'):
        return identity_manifest(environment, google=True)

    def test_google_fresh_bootstrap_gates_flows_before_enabling_source_and_preserves_other_sources(self):
        manifest = self.google_manifest()
        bootstrap.validate_manifest(manifest, 'test', identity_profile('test'))
        api = self.google_api()
        bootstrap.apply_google(manifest['google'], api, 'fixture-secret')
        source = api.objects['sources/oauth'][0]
        source_write = next(i for i, w in enumerate(api.writes) if w[1] == '/sources/oauth/')
        binding_writes = [i for i, w in enumerate(api.writes) if w[1] == '/policies/bindings/']
        self.assertEqual(len(binding_writes), 2)
        self.assertTrue(all(i < source_write for i in binding_writes))
        self.assertEqual(source['pkce'], 'S256')
        self.assertEqual(source['user_matching_mode'], 'email_link')
        self.assertEqual(source['user_path_template'], 'goauthentik.io/sources/%(slug)s')
        self.assertEqual(api.objects['stages/user_write'][0]['user_type'], 'internal')
        self.assertEqual(api.objects['stages/identification'][0]['sources'], sorted(['other-source', source['pk']]))
        self.assertTrue(all(f['policy_engine_mode'] == 'all' for f in api.objects['flows/instances'][-2:]))
        first_count = len(api.writes)
        bootstrap.apply_google(manifest['google'], api, 'fixture-secret')
        self.assertEqual(len(api.writes), first_count)
        self.assertEqual(len(api.objects['sources/oauth']), 1)
        self.assertEqual(len(api.objects['policies/bindings']), 2)

    def test_delayed_defaults_finish_before_any_identity_write(self):
        for resource, field, value in [
                ('flows/instances', 'slug', 'default-provider-authorization-implicit-consent'),
                ('flows/instances', 'slug', 'default-provider-invalidation-flow'),
                ('crypto/certificatekeypairs', 'name', 'authentik Self-signed Certificate'),
                ('propertymappings/provider/scope', 'scope_name', 'openid'),
                ('propertymappings/provider/scope', 'scope_name', 'email'),
                ('flows/instances', 'slug', 'default-source-authentication'),
                ('flows/instances', 'slug', 'default-source-enrollment'),
                ('stages/identification', 'name', 'default-authentication-identification'),
                ('stages/user_write', 'name', 'default-source-enrollment-write')]:
            with self.subTest(default=value):
                api = self.google_api()
                obj = next(x for x in api.objects[resource] if x[field] == value)
                api.objects[resource].remove(obj)
                elapsed = [0]

                def initialize(delay):
                    self.assertEqual(api.writes, [])
                    elapsed[0] += delay
                    api.objects[resource].append(obj)

                with patch.object(bootstrap.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                        patch.object(bootstrap.time, 'sleep', side_effect=initialize) as sleep:
                    result = bootstrap.apply(self.google_manifest(), api, {
                        'STAFF_OIDC_CLIENT_SECRET': 'test-staff',
                        'NETBIRD_OIDC_CLIENT_SECRET': 'test-netbird',
                        'GOOGLE_OAUTH_CLIENT_SECRET': 'test-google'})
                sleep.assert_called_once_with(2)
                self.assertEqual(result['providers'], 2)
                self.assertTrue(api.objects['sources/oauth'][0]['enabled'])

    def test_default_readiness_timeout_leaves_identity_unmodified(self):
        api = self.google_api()
        api.objects['crypto/certificatekeypairs'] = []
        elapsed = [0]
        delays = []

        def advance(delay):
            self.assertEqual(api.writes, [])
            delays.append(delay)
            elapsed[0] += delay

        wait = partial(bootstrap.wait_for_defaults, timeout=5)
        with patch.object(bootstrap.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                patch.object(bootstrap.time, 'sleep', side_effect=advance), \
                patch.object(bootstrap, 'wait_for_defaults', wait):
            with self.assertRaisesRegex(TimeoutError, 'authentik Self-signed Certificate'):
                bootstrap.apply(self.google_manifest(), api, {})
        self.assertEqual(delays, [2, 2, 1])
        self.assertEqual(api.writes, [])

    def test_default_readiness_does_not_retry_api_errors_or_duplicate_objects(self):
        api = self.google_api()
        with patch.object(api, 'request', side_effect=HTTPError(
                'http://fixture.invalid/api', 401, 'Unauthorized', {}, None)), \
                patch.object(bootstrap.time, 'sleep') as sleep:
            with self.assertRaises(HTTPError):
                bootstrap.apply(self.google_manifest(), api, {})
            sleep.assert_not_called()
        api.objects['crypto/certificatekeypairs'] *= 2
        with patch.object(bootstrap.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'multiple exact matches'):
                bootstrap.apply(self.google_manifest(), api, {})
            sleep.assert_not_called()
        self.assertEqual(api.writes, [])

    def test_google_source_policy_rejects_unverified_and_unlisted_accounts(self):
        api = self.google_api()
        bootstrap.apply_google(self.google_manifest()['google'], api, 'fixture-secret')
        expression = api.objects['policies/expression'][0]['expression']
        for context, expected in [
            ({'email': 'allowed@example.invalid', 'email_verified': True}, True),
            ({'email': 'ALLOWED@example.invalid', 'verified_email': True}, True),
            ({'email': 'allowed@example.invalid', 'email_verified': False}, False),
            ({'email': 'allowed@example.invalid', 'email_verified': 'true'}, False),
            ({'email': 'stranger@example.invalid', 'email_verified': True}, False),
            ({}, False),
        ]:
            namespace = {'context': {'oauth_userinfo': context}}
            exec('def evaluate():\n' + '\n'.join('    ' + line for line in expression.splitlines()), namespace)
            self.assertEqual(namespace['evaluate'](), expected)

    def test_google_write_only_secret_update_uses_source_slug(self):
        api = self.google_api()
        google = self.google_manifest()['google']
        bootstrap.apply_google(google, api, 'fixture-secret')
        del api.objects['sources/oauth'][0]['consumer_secret']
        api.writes.clear()
        bootstrap.apply_google(google, api, 'rotated-fixture-secret')
        self.assertEqual(api.writes, [('PATCH', '/sources/oauth/google/', {
            'consumer_secret': 'rotated-fixture-secret', 'provider_type': 'google'})])

    def test_emergency_password_is_optional_and_rendered_from_private_inputs(self):
        env = Environment()
        template = env.from_string((ROLE / 'templates/authentik.env.j2').read_text())
        values = dict(authentik_secret_key='key', authentik_postgres_password='postgres', authentik_bootstrap_token='token')
        self.assertNotIn('AUTHENTIK_BOOTSTRAP_PASSWORD', template.render(**values))
        self.assertIn('AUTHENTIK_BOOTSTRAP_PASSWORD=fixture-emergency', template.render(
            **values, authentik_decrypted={'AUTHENTIK_EMERGENCY_PASSWORD': 'fixture-emergency'}))

    def test_failed_bootstrap_removes_private_manifest_without_logging_its_values(self):
        task = next(task for task in yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
                    if task['name'] == 'Apply private Authentik identity inputs and remove the runtime manifest')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = 'PRIVATE-IDENTITY-FIXTURE'
            (root / 'bootstrap.json').write_text(secret)
            (root / 'bootstrap.py').write_text('raise RuntimeError(' + repr(secret) + ')\n')
            result = run_play({
                'authentik_bootstrap_enabled': True, 'authentik_project_root': directory,
                'authentik_bootstrap_base_url': 'http://127.0.0.1:1', 'authentik_environment': 'test',
                'authentik_bootstrap_token': secret, 'staff_oidc_client_secret': secret,
                'netbird_oidc_client_secret': secret, 'authentik_decrypted': {},
            }, [{'block': [task], 'rescue': [{'ansible.builtin.set_fact': {'bootstrap_failed': True}}]},
                {'ansible.builtin.assert': {'that': 'bootstrap_failed | default(false)'}}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / 'bootstrap.json').exists())
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_google_missing_prerequisites_fail_before_enabling_source(self):
        for resource in ('flows/instances', 'stages/identification', 'stages/user_write'):
            api = self.google_api()
            api.objects[resource] = []
            with self.assertRaises(ValueError):
                bootstrap.apply_google(self.google_manifest()['google'], api, 'fixture-secret')
            self.assertEqual(api.writes, [])
        api = self.google_api()
        with self.assertRaises(ValueError):
            bootstrap.apply_google(self.google_manifest()['google'], api, '')
        self.assertEqual(api.writes, [])

    def test_google_manifest_refuses_broad_allowlists_in_both_environments(self):
        for environment in ('test', 'prod'):
            for value in ([], ['*@example.invalid'], ['UPPER@example.invalid'], ['bad email@example.invalid']):
                manifest = self.google_manifest(environment)
                manifest['google']['allowed_emails'] = value
                with self.subTest(environment=environment, value=value), self.assertRaises(ValueError):
                    bootstrap.validate_manifest(manifest, environment, identity_profile(environment))

    def test_production_google_bootstrap_is_idempotent_and_preserves_emergency_admin(self):
        manifest = self.google_manifest('prod')
        bootstrap.validate_manifest(manifest, 'prod', identity_profile('prod'))
        api = self.google_api()
        emergency = {'pk': 1, 'username': 'akadmin', 'is_superuser': True, 'groups': ['emergency']}
        api.objects['core/users'] = [emergency.copy()]
        secrets = {'STAFF_OIDC_CLIENT_SECRET': 'prod-staff-fixture',
                   'NETBIRD_OIDC_CLIENT_SECRET': 'prod-netbird-fixture',
                   'GOOGLE_OAUTH_CLIENT_SECRET': 'prod-google-fixture'}
        bootstrap.apply(manifest, api, secrets)
        count = len(api.writes)
        bootstrap.apply(manifest, api, secrets)
        self.assertEqual(len(api.writes), count)
        self.assertEqual(api.objects['core/users'], [emergency])
        self.assertEqual({p['client_id'] for p in api.objects['providers/oauth2']},
                         {'netbird-prod', 'pearland-paws-staff'})
        source = api.objects['sources/oauth'][0]
        self.assertTrue(source['enabled'])
        self.assertEqual(source['consumer_secret'], secrets['GOOGLE_OAUTH_CLIENT_SECRET'])
        expression = api.objects['policies/expression'][0]['expression']
        for email, verified, expected in [('allowed@example.invalid', True, True),
                                           ('unknown@example.invalid', True, False),
                                           ('allowed@example.invalid', 'true', False),
                                           ('allowed@example.invalid', False, False)]:
            ns = {'context': {'oauth_userinfo': {'email': email, 'email_verified': verified}}}
            exec('def evaluate():\n' + '\n'.join('    ' + line for line in expression.splitlines()), ns)
            self.assertEqual(ns['evaluate'](), expected)

    def test_production_manifest_rejects_test_client_substitution(self):
        for client_name in ('staff', 'netbird'):
            for key in ('client_id', 'provider_name', 'application_slug', 'issuer_host', 'redirect_uris', 'logout_uris'):
                manifest = self.google_manifest('prod')
                manifest[client_name][key] = self.google_manifest()[client_name][key]
                with self.subTest(client=client_name, key=key), self.assertRaises(ValueError):
                    bootstrap.validate_manifest(manifest, 'prod', identity_profile('prod'))

    def test_inventory_values_drive_bootstrap_without_naming_conventions(self):
        profile = identity_profile('prod')
        profile.update(issuer_host='login.identity.invalid', netbird_url='https://vpn.edge.invalid',
                       netbird_subnet='100.100.0.0/16', compose_project='custom-identity',
                       compose_file='/opt/custom-identity/compose.yaml', compose_service='identity-api')
        for name in ('staff', 'netbird'):
            profile[name].update(client_id='custom-' + name, application_name='Custom ' + name,
                                 redirect_uris=['https://' + name + '.edge.invalid/callback'],
                                 logout_uris=['https://' + name + '.edge.invalid/logout'])
        manifest = identity_manifest('prod', google=True, profile=profile)
        bootstrap.validate_manifest(manifest, 'prod', profile)
        api = self.google_api()
        bootstrap.apply(manifest, api, {'STAFF_OIDC_CLIENT_SECRET': 'staff-fixture',
                                      'NETBIRD_OIDC_CLIENT_SECRET': 'vpn-fixture',
                                      'GOOGLE_OAUTH_CLIENT_SECRET': 'google-fixture'})
        self.assertEqual({p['client_id'] for p in api.objects['providers/oauth2']},
                         {'custom-staff', 'custom-netbird'})
        self.assertEqual({a['slug'] for a in api.objects['core/applications']},
                         {'custom-staff', 'custom-netbird'})
        with self.assertRaises(ValueError):
            bootstrap.validate_manifest(manifest, 'prod', identity_profile('prod'))

    def test_profile_rejects_unsafe_endpoints_paths_and_client_contracts(self):
        changes = [('issuer_host', 'user@auth.example.invalid'),
                   ('netbird_url', 'https://vpn.example.invalid/extra'),
                   ('netbird_url', 'https://vpn.example.invalid?query'),
                   ('netbird_subnet', 'not-a-subnet'), ('netbird_subnet', '100.90.1.1/16'),
                   ('compose_project', 'stack; echo unsafe'), ('compose_file', 'relative/compose.yaml'),
                   ('compose_file', '/opt/../other/compose.yaml')]
        for key, value in changes:
            profile = identity_profile('prod')
            profile[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                bootstrap.validate_profile(profile)
        for key, value in [('client_id', 'unsafe/client'), ('redirect_uris', []),
                           ('redirect_uris', ['http://staff.example.invalid/callback']),
                           ('logout_uris', ['https://user@staff.example.invalid/logout'])]:
            profile = identity_profile('prod')
            profile['staff'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                bootstrap.validate_profile(profile)

    def test_profile_export_renders_inventory_without_private_identity_values(self):
        playbook = yaml.safe_load((ROOT / 'ansible/playbooks/export-identity-profile.yml').read_text())[0]
        self.assertFalse(playbook['gather_facts'])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'profile.json'
            for environment in ('test', 'prod'):
                result = run_play({'primary_domain': 'example.invalid', 'deploy_environment': environment,
                                   'identity_profile_output': str(output)}, playbook['tasks'])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(output.read_text()), identity_profile(environment))
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('google_emails', output.read_text())
                self.assertNotIn('staff_users', output.read_text())
            output.unlink()
            result = run_play({'primary_domain': 'example.invalid', 'deploy_environment': 'unknown',
                               'identity_profile_output': str(output)}, playbook['tasks'])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_role_uses_configured_project_and_rejects_a_different_compose_path(self):
        tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())[:2]
        profile = identity_profile('prod')
        profile['compose_project'] = 'custom-identity'
        variables = dict(authentik_environment='prod', authentik_identity_profiles={'prod': profile},
                         authentik_age_key_file='/private/key', authentik_secret_source='/private/prod.enc.yaml')
        result = run_play(variables, tasks + [{'ansible.builtin.assert': {
            'that': "authentik_compose_project == 'custom-identity'"}}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile['compose_file'] = '/opt/a-different-stack/compose.yaml'
        result = run_play(variables, tasks)
        self.assertNotEqual(result.returncode, 0)

    def test_production_google_opt_in_requires_bootstrap(self):
        task = next(t for t in yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
                    if t['name'] == 'Validate Authentik environment and isolated roots')
        variables = dict(authentik_environment='prod', authentik_project_root='/opt/hou1-authentik/prod',
                         authentik_data_root='/var/lib/hou1-authentik/prod',
                         authentik_compose_project='hou1-authentik-prod', authentik_age_key_file='/private/key',
                         authentik_secret_source='/private/prod.enc.yaml', authentik_google_enabled=True)
        for enabled in (True, False):
            result = run_play({**variables, 'authentik_bootstrap_enabled': enabled}, [task])
            self.assertEqual(result.returncode == 0, enabled, result.stdout + result.stderr)

    def test_pinned_schema_reconciles_twice_without_duplicate_objects(self):
        manifest = identity_manifest()
        bootstrap.validate_manifest(manifest, 'test', identity_profile('test'))
        api = FixtureAPI()
        secrets = {'STAFF_OIDC_CLIENT_SECRET': 'test-staff', 'NETBIRD_OIDC_CLIENT_SECRET': 'test-netbird'}
        self.assertEqual(bootstrap.apply(manifest, api, secrets), {'groups': 3, 'users': 0, 'providers': 2})
        initial_writes = len(api.writes)
        bootstrap.apply(manifest, api, secrets)
        self.assertEqual(len(api.writes), initial_writes)
        providers = api.objects['providers/oauth2']
        mappings = {obj['pk']: obj for obj in api.objects['propertymappings/provider/scope']}
        for provider in providers:
            scopes = [mappings[pk]['scope_name'] for pk in provider['property_mappings']]
            self.assertEqual(len(scopes), len(set(scopes)))
            self.assertIn('openid', scopes)
            self.assertIn('email', scopes)
            self.assertIn('groups', scopes)
        callback = 'https://netbird-test.example.invalid/oauth2/callback'
        self.assertIn(callback, [item['url'] for item in providers[1]['redirect_uris']])

    def test_exact_lookup_and_application_patch_use_slug(self):
        api = FixtureAPI()
        api.objects['core/applications'] = [
            {'pk': 'uuid-1', 'slug': 'staff-other', 'name': 'Other'},
            {'pk': 'uuid-2', 'slug': 'staff', 'name': 'Old'},
        ]
        self.assertEqual(api.ensure('core/applications', 'slug', 'staff', {'slug': 'staff', 'name': 'New'}), 'uuid-2')
        self.assertEqual(api.writes, [('PATCH', '/core/applications/staff/', {'name': 'New'})])

    def test_duplicate_exact_identity_is_rejected(self):
        api = FixtureAPI()
        api.objects['core/groups'] = [{'pk': '1', 'name': 'staff'}, {'pk': '2', 'name': 'staff'}]
        with self.assertRaisesRegex(ValueError, 'multiple exact matches'):
            api.ensure('core/groups', 'name', 'staff', {'name': 'staff'})
        self.assertEqual(api.writes, [])

    def test_profile_does_not_duplicate_or_expand_staff_group_claims(self):
        for environment in ['test', 'prod']:
            manifest = identity_manifest(environment)
            user = SimpleNamespace(name='Reader', username='reader', ak_groups=SimpleNamespace(all=lambda: [
                SimpleNamespace(name='pearland-paws-staff-reader'), SimpleNamespace(name='unrelated-admin')]))
            claims = []
            for mapping in manifest['scope_mappings']:
                namespace = {'user': user, 'provider': SimpleNamespace(client_id=manifest['staff']['client_id'])}
                exec('def evaluate():\n    ' + mapping['expression'], namespace)
                claims.append(namespace['evaluate']())
            self.assertEqual([claim['groups'] for claim in claims if 'groups' in claim], [['pearland-paws-staff-reader']])
            self.assertEqual([claim['aud'] for claim in claims if 'aud' in claim], [manifest['staff']['client_id']])

    def test_bootstrap_script_is_valid_and_api_reconciliation_is_idempotent(self):
        py_compile.compile(str(ROLE / 'files/bootstrap.py'), doraise=True)
        script = (ROLE / 'files/bootstrap.py').read_text()
        for required in ['def ensure', 'PATCH', 'core/groups', 'propertymappings/provider/scope', 'providers/oauth2', 'core/applications', 'environment does not match']:
            self.assertIn(required, script)

    def test_manifests_keep_clients_and_audiences_isolated(self):
        test, prod = identity_manifest('test'), identity_manifest('prod')
        for name in ('staff', 'netbird'):
            self.assertNotEqual(test[name]['client_id'], prod[name]['client_id'])
            self.assertNotEqual(test[name]['issuer_host'], prod[name]['issuer_host'])
            self.assertNotEqual(test[name]['redirect_uris'], prod[name]['redirect_uris'])
            self.assertNotEqual(test[name]['logout_uris'], prod[name]['logout_uris'])
        for environment, manifest in [('test', test), ('prod', prod)]:
            bootstrap.validate_manifest(manifest, environment, identity_profile(environment))

    def test_bootstrap_is_disabled_for_base_compose_deployments(self):
        tasks = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn('authentik_bootstrap_enabled | bool', tasks)
        self.assertIn('AUTHENTIK_BOOTSTRAP_TOKEN', tasks)
