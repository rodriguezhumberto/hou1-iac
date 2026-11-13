"""Check domain-scoped branding, reversible deployment and native template inheritance."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

import yaml
from django.template import Context, Engine, Library

from helpers import run_play
from identity_fixtures import identity_manifest, identity_names, identity_profile
from test_authentik_bootstrap import FixtureAPI, bootstrap

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'ansible/playbooks/roles/authentik'
ASSETS = ROLE / 'files/branding'
register = Library()


@register.simple_tag
def versioned_script(path):
    return '/static/' + path.replace('%v', '2025.10.4')


def default_brand():
    return {
        'pk': 'default', 'brand_uuid': 'default', 'domain': 'authentik-default', 'default': True,
        'branding_title': 'Previous title', 'branding_logo': '/old-logo.svg',
        'branding_favicon': '/old-icon.png', 'branding_custom_css': '/* existing CSS */',
        'branding_default_flow_background': '/old-background.png',
        'flow_authentication': 'existing-authentication', 'flow_recovery': 'existing-recovery',
        'attributes': {'custom': 'preserved', 'settings': {'locale': 'en', 'theme': {'base': 'dark'}}},
    }


class BrandingAPI(FixtureAPI):
    def __init__(self):
        super().__init__()
        self.objects['core/brands'] = [default_brand()]
        self.objects['sources/oauth'] = [{'pk': 'google', 'slug': 'google',
                                         'provider_type': 'google', 'enabled': True}]
        self.objects['stages/identification'] = [{
            'pk': 'identify', 'name': 'default-authentication-identification',
            'user_fields': ['username'], 'sources': ['google'],
        }]

    def request(self, method, path, payload=None):
        result = super().request(method, path, payload)
        if method == 'POST' and path == '/core/brands/':
            result['brand_uuid'] = result['pk']
        return result


def branding_input(enabled=True):
    return {'enabled': enabled, 'domain': identity_profile()['issuer_host'],
            'css': (ASSETS / 'login-v3.css').read_text()}


def render_flow(branded=True, compatibility=False):
    # The second loader contains the original upstream 2025.10.4 flow template.
    # Django skips the override's origin when resolving same-name inheritance.
    engine = Engine(loaders=[
        ('django.template.loaders.filesystem.Loader', [str(ASSETS.parent)]),
        ('django.template.loaders.locmem.Loader', {
            'if/flow.html': (ASSETS / 'flow.html').read_text(),
        }),
        ('django.template.loaders.filesystem.Loader', [str(ROOT / 'tests/fixtures/authentik-2025.10.4')]),
        ('django.template.loaders.locmem.Loader', {
            'base/skeleton.html': '<!doctype html><html><head>{% block head_before %}{% endblock %}'
                                  '{% block head %}{% endblock %}</head><body>'
                                  '{% block body %}{% endblock %}{% block scripts %}{% endblock %}'
                                  '</body></html>',
            'base/header_js.html': '<script>window.authentik = {};</script>',
        }),
    ], libraries={'authentik_core': __name__, 'static': 'django.templatetags.static'})
    return engine.get_template('if/flow.html').render(Context({
        'brand': {'attributes': {'pearland_paws_login': {'environment': 'test'}} if branded else {}},
        'flow': {'slug': 'default-authentication-flow', 'layout': 'stacked',
                 'compatibility_mode': compatibility},
        'inspector': False, 'flow_background_url': '/background.png',
    }))


class AuthentikBrandingTest(unittest.TestCase):
    def test_domain_brand_inherits_flows_and_preserves_the_catch_all(self):
        api = BrandingAPI()
        original = copy.deepcopy(api.objects['core/brands'][0])
        bootstrap.apply_branding(branding_input(), 'test', api)
        catch_all, brand = api.objects['core/brands']
        self.assertEqual(catch_all, original)
        self.assertFalse(brand['default'])
        self.assertEqual(brand['domain'], identity_profile()['issuer_host'])
        self.assertEqual(brand['flow_authentication'], original['flow_authentication'])
        self.assertEqual(brand['flow_recovery'], original['flow_recovery'])
        self.assertEqual(brand['attributes']['custom'], 'preserved')
        self.assertEqual(brand['attributes']['settings']['locale'], 'en')
        self.assertEqual(brand['attributes']['settings']['theme']['base'], 'light')
        count = len(api.writes)
        bootstrap.apply_branding(branding_input(), 'test', api)
        self.assertEqual(len(api.writes), count)

    def test_existing_brand_restores_previous_style_without_google(self):
        api = BrandingAPI()
        original = {**default_brand(), 'domain': identity_profile()['issuer_host'],
                    'default': False, 'brand_uuid': 'specific', 'pk': 'specific'}
        api.objects['core/brands'].append(copy.deepcopy(original))
        bootstrap.apply_branding(branding_input(), 'test', api)
        # Rollback retains unrelated settings changed since branding was enabled.
        api.objects['core/brands'][1]['attributes']['later'] = 'keep me'
        api.objects['sources/oauth'] = []
        bootstrap.apply_branding(branding_input(False), 'test', api)
        expected = copy.deepcopy(original)
        expected['attributes']['later'] = 'keep me'
        self.assertEqual(api.objects['core/brands'][1], expected)
        count = len(api.writes)
        bootstrap.apply_branding(branding_input(False), 'test', api)
        self.assertEqual(len(api.writes), count)

    def test_missing_google_and_duplicate_domains_leave_brands_unchanged(self):
        for condition in ('missing', 'disabled', 'duplicate', 'no-local-fields', 'source-not-bound'):
            api = BrandingAPI()
            if condition == 'missing':
                api.objects['sources/oauth'] = []
            elif condition == 'disabled':
                api.objects['sources/oauth'][0]['enabled'] = False
            elif condition == 'duplicate':
                api.objects['core/brands'] += [
                    {**default_brand(), 'domain': identity_profile()['issuer_host']} for _ in range(2)]
            elif condition == 'no-local-fields':
                api.objects['stages/identification'][0]['user_fields'] = []
            else:
                api.objects['stages/identification'][0]['sources'] = []
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                bootstrap.apply_branding(branding_input(), 'test', api)
            self.assertEqual(api.writes, [])

    def test_branding_validation_precedes_api_writes(self):
        for change in ({'domain': 'other.invalid'}, {'enabled': 'false'}, {'css': ''}):
            manifest = identity_manifest(google=True)
            manifest['branding'] = {**branding_input(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                bootstrap.validate_manifest(manifest, 'test', identity_profile())
        manifest = identity_manifest()
        manifest['branding'] = branding_input()
        with self.assertRaisesRegex(ValueError, 'Google'):
            bootstrap.validate_manifest(manifest, 'test', identity_profile())

    def test_test_and_prod_defaults_render_valid_branded_manifests_and_allow_rollback(self):
        for environment in ('test', 'prod'):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / 'bootstrap.json'
                variables = {
                    'role_path': str(ROLE), 'authentik_environment': environment,
                    'authentik_identity_profiles': {environment: identity_profile(environment, private=False)},
                    'authentik_decrypted': {'IDENTITY_NAMES': identity_names(environment),
                                           'GOOGLE_OAUTH_CLIENT_ID': 'fixture.apps.googleusercontent.com',
                                           'GOOGLE_ALLOWED_EMAILS': ['approved@example.invalid']},
                }
                tasks = [
                    {'ansible.builtin.assert': {'that': 'authentik_bootstrap_enabled | bool'}},
                    {'ansible.builtin.template': {
                        'src': str(ROLE / 'templates/bootstrap.json.j2'), 'dest': str(destination)}},
                ]
                for enabled in (True, False):
                    overrides = {} if enabled else {'authentik_branding_enabled': 'false'}
                    result = run_play({**variables, **overrides}, tasks)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    manifest = json.loads(destination.read_text())
                    bootstrap.validate_manifest(manifest, environment, identity_profile(environment))
                    self.assertEqual(manifest['branding']['enabled'], enabled)
                    self.assertIn('google', manifest)
                    if enabled:
                        self.assertEqual(manifest['branding']['css'], (ASSETS / 'login-v3.css').read_text().rstrip())
                    else:
                        self.assertNotIn('css', manifest['branding'])

    def test_disabling_a_managed_template_requires_brand_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'templates/if/flow.html'
            target.parent.mkdir(parents=True)
            target.write_text((ASSETS / 'flow.html').read_text())
            result = run_play({'authentik_data_root': directory, 'authentik_branding_enabled': False,
                               'authentik_bootstrap_enabled': False}, self.deployment_tasks())
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(target.exists())

    def test_django_extension_preserves_native_executor_scripts_and_compatibility(self):
        for branded in (True, False):
            for compatibility in (True, False):
                html = render_flow(branded, compatibility)
                self.assertEqual(html.count('<ak-flow-executor '), 1)
                self.assertIn('flowSlug="default-authentication-flow"', html)
                self.assertIn('/static/dist/flow/FlowInterface-2025.10.4.js', html)
                self.assertEqual('ShadyDOM = { force: true }' in html, compatibility)
                self.assertEqual('id="pp-login"' in html, branded)
                self.assertEqual('src="/media/branding/login-v2.js"' in html, branded)
                self.assertNotIn('Private workspace', html)
                self.assertNotIn('Team sign-in', html)
                if branded:
                    self.assertIn('id="pp-google" type="button" hidden', html)
                    self.assertIn('Test environment', html)

    def deployment_tasks(self):
        tasks = yaml.safe_load((ROLE / 'tasks/branding.yml').read_text())
        for task in tasks:
            if 'ansible.builtin.copy' in task:
                task['ansible.builtin.copy']['src'] = str(ROLE / 'files') + '/' + task['ansible.builtin.copy']['src']
        return tasks

    def test_assets_install_idempotently_and_disable_restores_native_template(self):
        with tempfile.TemporaryDirectory() as directory:
            variables = {'authentik_data_root': directory, 'authentik_branding_enabled': True,
                         'authentik_container_uid': os.getuid(), 'authentik_container_gid': os.getgid(),
                         'authentik_bootstrap_enabled': True}
            tasks = self.deployment_tasks()
            for repetition in range(2):
                result = run_play(variables, tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if repetition:
                    self.assertIn('changed=0', result.stdout)
            target = Path(directory) / 'templates/if/flow.html'
            self.assertEqual(target.read_text(), (ASSETS / 'flow.html').read_text())
            for asset in ['logo-v1.png', 'login-v3.css', 'login-v2.js']:
                installed = Path(directory) / 'media/branding' / asset
                self.assertEqual(installed.read_bytes(), (ASSETS / asset).read_bytes())
                self.assertEqual(installed.stat().st_mode & 0o777, 0o640)
            variables['authentik_branding_enabled'] = False
            result = run_play(variables, tasks)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(target.exists())

    def test_foreign_templates_are_not_overwritten_or_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'templates/if/flow.html'
            target.parent.mkdir(parents=True)
            target.write_text('Unrelated existing template')
            for enabled in (True, False):
                result = run_play({'authentik_data_root': directory, 'authentik_branding_enabled': enabled},
                                  self.deployment_tasks())
                self.assertEqual(result.returncode == 0, not enabled, result.stdout + result.stderr)
                self.assertEqual(target.read_text(), 'Unrelated existing template')

    def test_branding_guard_requires_google_bootstrap_and_the_tested_version(self):
        guard = next(task for task in yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
                     if task['name'] == 'Validate Authentik environment and isolated roots')
        variables = {'authentik_environment': 'test', 'authentik_project_root': '/opt/hou1-authentik/test',
                     'authentik_data_root': '/var/lib/hou1-authentik/test',
                     'authentik_compose_project': 'hou1-authentik-test',
                     'authentik_age_key_file': '/private/key', 'authentik_secret_source': '/private/test.enc.yaml',
                     'authentik_branding_enabled': True, 'authentik_google_enabled': True,
                     'authentik_bootstrap_enabled': True}
        for override in ({}, {'authentik_google_enabled': False}, {'authentik_bootstrap_enabled': False},
                         {'authentik_image': 'ghcr.io/goauthentik/server:unverified'}):
            result = run_play({**variables, **override}, [guard])
            self.assertEqual(result.returncode == 0, not override, result.stdout + result.stderr)
