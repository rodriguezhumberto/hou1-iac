from pathlib import Path
import json
import importlib.util
import py_compile
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import tempfile
import yaml

from jinja2 import Environment
from helpers import run_play

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
            {'pk': 'source-auth', 'slug': 'default-source-authentication', 'policy_engine_mode': 'any'},
            {'pk': 'source-enroll', 'slug': 'default-source-enrollment', 'policy_engine_mode': 'any'},
        ]
        api.objects['stages/identification'] = [
            {'pk': 'identify', 'name': 'default-authentication-identification', 'sources': ['other-source']}]
        api.objects['stages/user_write'] = [
            {'pk': 'write', 'name': 'default-source-enrollment-write', 'user_type': 'external'}]
        return api

    def google_manifest(self):
        env = Environment()
        env.filters['to_json'] = json.dumps
        return json.loads(env.from_string((ROLE / 'templates/bootstrap-test.json.j2').read_text()).render(
            primary_domain='example.invalid', authentik_staff_users=[], authentik_google_enabled=True,
            authentik_decrypted={'GOOGLE_OAUTH_CLIENT_ID': 'fixture.apps.googleusercontent.com',
                                 'GOOGLE_ALLOWED_EMAILS': ['allowed@example.invalid']}))

    def test_google_fresh_bootstrap_gates_flows_before_enabling_source_and_preserves_other_sources(self):
        manifest = self.google_manifest()
        bootstrap.validate_manifest(manifest, 'test')
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

    def test_google_manifest_refuses_production_or_broad_allowlists(self):
        for value in ([], ['*@example.invalid'], ['UPPER@example.invalid'], ['bad email@example.invalid']):
            manifest = self.google_manifest()
            manifest['google']['allowed_emails'] = value
            with self.assertRaises(ValueError):
                bootstrap.validate_manifest(manifest, 'test')
        env = Environment()
        env.filters['to_json'] = json.dumps
        manifest = json.loads(env.from_string((ROLE / 'templates/bootstrap-prod.json.j2').read_text()).render(
            primary_domain='example.invalid', authentik_staff_users=[]))
        manifest['google'] = self.google_manifest()['google']
        with self.assertRaisesRegex(ValueError, 'restricted to the isolated rehearsal'):
            bootstrap.validate_manifest(manifest, 'prod')

    def test_pinned_schema_reconciles_twice_without_duplicate_objects(self):
        env = Environment()
        env.filters['to_json'] = json.dumps
        manifest = json.loads(env.from_string((ROLE / 'templates/bootstrap-test.json.j2').read_text()).render(
            primary_domain='example.invalid', authentik_staff_users=[]))
        bootstrap.validate_manifest(manifest, 'test')
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
        env = Environment()
        env.filters['to_json'] = json.dumps
        for environment in ['test', 'prod']:
            manifest = json.loads(env.from_string((ROLE / 'templates' / f'bootstrap-{environment}.json.j2').read_text()).render(
                primary_domain='example.invalid', authentik_staff_users=[]))
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
        for env in ['test', 'prod']:
            content = (ROLE / 'templates' / f'bootstrap-{env}.json.j2').read_text()
            self.assertIn(f'"environment": "{env}"', content)
            self.assertIn('client_id', content)
            self.assertIn('audience', content)
            self.assertIn('redirect_uris', content)
            self.assertIn('logout_uris', content)
        test = (ROLE / 'templates/bootstrap-test.json.j2').read_text()
        prod = (ROLE / 'templates/bootstrap-prod.json.j2').read_text()
        self.assertIn('pearland-paws-staff-test', test)
        self.assertIn('pearland-paws-staff', prod)
        self.assertIn('netbird-test', test)
        self.assertIn('netbird-prod', prod)

    def test_bootstrap_is_disabled_for_base_compose_deployments(self):
        tasks = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn('authentik_bootstrap_enabled | bool', tasks)
        self.assertIn('AUTHENTIK_BOOTSTRAP_TOKEN', tasks)
