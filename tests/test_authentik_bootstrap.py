from pathlib import Path
import json
import importlib.util
import py_compile
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment

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
            key = 'slug' if resource == 'core/applications' else 'pk'
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
