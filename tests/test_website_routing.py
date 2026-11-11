"""Verify environment inputs and image-only Compose routing without live secrets."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
from helpers import ROOT, ROLES, guard_play, run_play, run_plays

spec = importlib.util.spec_from_file_location('sites', ROOT / 'ansible/playbooks/filter_plugins/webstack_sites.py')
sites = importlib.util.module_from_spec(spec); spec.loader.exec_module(sites)


def settings():
    return dict(primary_domain='site.example.invalid', public_site_origin='https://site.example.invalid',
                public_site_hostnames=['site.example.invalid', 'www.site.example.invalid'],
                staff_domain='staff.example.invalid', webstack_secondary_site_enabled=False,
                webstack_public_canonical_redirect=True)


class WebsiteRoutingTest(unittest.TestCase):
    def test_explicit_inputs_reject_overlap_invalid_origins_and_nonboolean_switches(self):
        self.assertEqual(sites.site_errors(settings()), [])
        for change in [dict(webstack_secondary_site_enabled='false'), dict(webstack_public_canonical_redirect='true'),
                       dict(public_site_origin='http://site.example.invalid'),
                       dict(public_site_origin='https://site.example.invalid/path'),
                       dict(public_site_origin='https://unlisted.example.invalid'),
                       dict(public_site_hostnames=['site.example.invalid', 'staff.example.invalid']),
                       dict(netbird_domain='site.example.invalid'),
                       dict(authentik_identity_profiles={'prod': {'issuer_host': 'www.site.example.invalid'}}),
                       dict(webstack_secondary_site_enabled=True),
                       dict(webstack_secondary_site_enabled=True, secondary_domain='site.example.invalid')]:
            with self.subTest(change=change):
                self.assertTrue(sites.site_errors({**settings(), **change}))
        legacy = {**settings(), 'webstack_secondary_site_enabled': True, 'webstack_public_canonical_redirect': False,
                  'secondary_domain': 'extra.example.invalid'}
        self.assertEqual(sites.site_errors(legacy), [])

    def test_routing_requires_a_compatible_selected_source_and_rejects_old_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'nginx').mkdir()
            tasks = yaml.safe_load((ROLES / 'webstack/tasks/website.yml').read_text())
            result = run_play({**settings(), 'app_directory': directory}, tasks)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Require a compatible immutable application revision', result.stdout)
            (root / 'nginx/secondary.conf.template').write_text('fixture capability')
            guard = tasks[0]
            result = run_plays([dict(variables={**settings(), 'app_directory': directory}, tasks=tasks),
                               guard_play({**settings(), 'webstack_secondary_site_enabled': 'false'}, [guard], guard['name'])])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_disabled_secondary_mounts_are_absent_from_real_compose_and_other_services_survive(self):
        source = dict(services={
            'astro-app': dict(build={'context': '.'}, volumes=['./database:/var/data']),
            'nginx': dict(image='nginx:1.28.0-alpine', ports=['443:443'], volumes=[
                './nginx/main:/etc/nginx/templates/default.conf.template:ro',
                './nginx/secondary:/etc/nginx/templates/secondary.conf.template:ro',
                dict(type='bind', source='./static', target='/usr/share/nginx/html/landing', read_only=True),
                './certs:/etc/letsencrypt:ro'])})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'docker-compose.yml'
            for enabled in (True, False):
                path.write_text(yaml.safe_dump(source))
                result = run_play(dict(app_directory=directory, webstack_secondary_site_enabled=enabled,
                                       application_image_reference='registry.example.invalid/app@sha256:' + 'a' * 64),
                                  [{'ansible.builtin.import_tasks': str(ROLES / 'webstack/tasks/image-compose.yml')}])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                merged = subprocess.run(['docker', 'compose', '-f', str(path), 'config', '--format', 'json'],
                                        capture_output=True, text=True, check=True)
                services = json.loads(merged.stdout)['services']
                targets = {mount['target'] for mount in services['nginx']['volumes']}
                self.assertEqual('/usr/share/nginx/html/landing' in targets, enabled)
                self.assertEqual('/etc/nginx/templates/secondary.conf.template' in targets, enabled)
                self.assertIn('/etc/letsencrypt', targets)
                self.assertEqual(services['astro-app']['volumes'][0]['target'], '/var/data')
                self.assertNotIn('build', services['astro-app'])

    def test_runtime_rendering_requires_no_secondary_domain_or_certificate_when_disabled(self):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())
        task = next(task for task in tasks if task['name'] == 'Inject production .env file for Astro')
        with tempfile.TemporaryDirectory() as directory:
            values = {**settings(), 'app_directory': directory, 'ssl_domains': [{'name': 'site.example.invalid'}],
                      'netbird_ip': '100.90.0.10', 'staff_cert_dir': 'site.example.invalid',
                      'deploy_environment': 'prod', 'cms_environment': 'production'}
            for name in ('public_hcaptcha_site_key', 'hcaptcha_secret_key', 'VAPID_PUBLIC_KEY', 'VAPID_PRIVATE_KEY',
                         'VAPID_SUBJECT', 'STAFF_PASSWORD', 'STAFF_SESSION_SECRET'):
                values[name] = 'fixture-only-value'
            result = run_play(values, [task])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = (Path(directory) / '.env').read_text()
            self.assertIn('SECONDARY_HOSTNAMES=\n', text)
            self.assertIn('SECONDARY_CERT_DIR=\n', text)
            self.assertIn('PUBLIC_CANONICAL_HOSTNAME=site.example.invalid\n', text)
            self.assertIn('PUBLIC_CANONICAL_REDIRECT=true\n', text)
            self.assertNotIn('fixture-only-value', result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
