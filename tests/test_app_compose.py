"""Check declared integration, migration safety and backend-independent ingress."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from compose_fixtures import local_tasks
from helpers import ROOT, ROLES, run_play

spec = importlib.util.spec_from_file_location(
    'app_compose', ROOT / 'ansible/playbooks/filter_plugins/app_compose.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def application(root):
    app = root / 'app'
    app.mkdir()
    subprocess.run(['git', 'init', str(app)], check=True, capture_output=True)
    (app / 'docker-compose.yml').write_text(yaml.safe_dump({
        'services': {'astro-app': {'image': 'node:24'},
                     'nginx': {'image': 'nginx:alpine', 'networks': ['app-network']}},
        'networks': {'app-network': {}}}))
    return dict(app_directory=str(app), cms_media_directory=str(root / 'media'),
                netbird_control_plane_directory=str(root / 'ingress'),
                authentik_enable_ingress_network=True,
                authentik_active_network='fixture-authentik',
                authentik_ingress_network='fixture-authentik')


class DeclaredComposeTest(unittest.TestCase):
    def test_source_ownership_guard_uses_separate_checkout_and_runtime_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            variables = application(root)
            source = root / 'selected-source'
            source.mkdir()
            subprocess.run(['git', 'init', str(source)], check=True, capture_output=True)
            (source / 'docker-compose.override.yml').write_text('services: {}\n')
            subprocess.run(['git', '-C', str(source), 'add', 'docker-compose.override.yml'], check=True)
            runtime = root / 'app'
            tasks = local_tasks('app_compose')
            for use_receipt in (False, True):
                if use_receipt:
                    (runtime / 'application-source.json').write_text(json.dumps(
                        dict(schema=1, source_root=str(source), revision='a' * 40, files={})))
                result = run_play({**variables, 'app_source_directory': str(source)}, tasks)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('app owns docker-compose.override.yml', result.stdout)
                self.assertFalse((runtime / 'docker-compose.override.yml').exists())

    def test_unknown_legacy_additions_refuse_before_write_then_migrate_and_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            variables = application(root)
            override = root / 'app/docker-compose.override.yml'
            extension = {'services': {'nginx': {'environment': {'CUSTOM': 'yes'}}},
                         'volumes': {'custom-cache': {}}}
            original = '# Managed by hou1-iac.\n' + yaml.safe_dump(extension)
            override.write_text(original)
            tasks = local_tasks('app_compose')
            refused = run_play(variables, tasks)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('app_compose_extensions', refused.stdout)
            self.assertIn('volumes.custom-cache', refused.stdout)
            self.assertEqual(override.read_text(), original)
            migrated = run_play({**variables, 'app_compose_extensions': extension}, tasks)
            self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
            self.assertIn('CUSTOM', override.read_text())
            # A later declared removal must not become sticky previous output.
            removed = run_play(variables, tasks)
            self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
            clean = override.read_text()
            self.assertNotIn('CUSTOM', clean)
            self.assertNotIn('custom-cache', clean)
            repeated = run_play(variables, tasks + [{'ansible.builtin.assert': {
                'that': 'not app_compose_render.changed'}}])
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            self.assertEqual(override.read_text(), clean)
            # An operator edit invalidates the fingerprint and needs declaration.
            override.write_text(clean + 'volumes:\n  manually-added: {}\n')
            edited = override.read_text()
            refused = run_play(variables, tasks)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('manually-added', refused.stdout)
            self.assertEqual(override.read_text(), edited)

    def test_generated_output_depends_only_on_declared_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            variables = application(Path(directory))
            override = Path(variables['app_directory']) / 'docker-compose.override.yml'
            tasks = local_tasks('app_compose')
            observed = {}
            for enabled in (True, False, False, True):
                result = run_play({**variables, 'authentik_enable_ingress_network': enabled}, tasks)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if enabled in observed:
                    self.assertEqual(override.read_text(), observed[enabled])
                observed[enabled] = override.read_text()
            override.unlink()
            result = run_play({**variables, 'authentik_enable_ingress_network': False}, tasks)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(override.read_text(), observed[False])

    def test_extensions_cannot_replace_managed_mounts_or_network_fields(self):
        for extensions in [
                {'services': {'astro-app': {'volumes': ['/custom:/var/data/media']}}},
                {'services': {'nginx': {'networks': {'netbird-ingress': {'ipv4_address': '1.2.3.4'}}}}},
                {'networks': {'authentik-ingress': {'name': 'other', 'external': True}}}]:
            with self.subTest(extensions=extensions), self.assertRaisesRegex(
                    Exception, 'cannot declare managed'):
                generator.compose_override('/media', '/ingress', 'netbird', '172.30.80.2',
                                           extensions=extensions)


class IndependentIngressTest(unittest.TestCase):
    def fake_modules(self, root):
        modules = root / 'modules'
        modules.mkdir()
        log = root / 'events.jsonl'
        common = f'''
from ansible.module_utils.basic import AnsibleModule
from pathlib import Path
import json
ROOT = Path({str(root)!r})
def event(value):
    with Path({str(log)!r}).open('a') as stream:
        stream.write(json.dumps(value) + '\\n')
'''
        (modules / 'fixture_network.py').write_text(common + '''
m = AnsibleModule(argument_spec=dict(name=dict(type='str', required=True),
    state=dict(type='str', default='present'), ipam_config=dict(type='list', elements='dict')))
assert m.params['state'] == 'present'
(ROOT / 'networks').mkdir(exist_ok=True)
(ROOT / 'networks' / m.params['name']).touch()
event(dict(action='network', **m.params))
m.exit_json(changed=True)
''')
        (modules / 'fixture_compose.py').write_text(common + '''
import yaml
m = AnsibleModule(argument_spec=dict(project_src=dict(type='str', required=True),
    services=dict(type='list', elements='str'), dependencies=dict(type='bool'),
    state=dict(type='str'), recreate=dict(type='str')))
assert m.params['services'] == ['nginx']
assert m.params['dependencies'] is False
assert (ROOT / 'ingress/nginx-include.conf').is_file()
assert (ROOT / 'ingress/ingress').is_dir()
model = yaml.safe_load((Path(m.params['project_src']) / 'docker-compose.override.yml').read_text())
assert 'authentik-ingress' in model['services']['nginx']['networks']
for network in model['networks'].values():
    assert (ROOT / 'networks' / network['name']).exists()
assert not (ROOT / 'ingress/ingress/authentik-prod.conf').exists()
event(dict(action='compose', **m.params))
m.exit_json(changed=True)
''')
        return modules, log

    def test_authentik_publishes_without_prior_netbird_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            variables = application(root)
            modules, log = self.fake_modules(root)
            certificate = root / 'app/certbot/conf/live/auth.example.org/fullchain.pem'
            certificate.parent.mkdir(parents=True)
            certificate.touch()
            binary = root / 'bin'
            binary.mkdir()
            docker = binary / 'docker'
            docker.write_text('#!/usr/bin/env python3\n' +
                'from pathlib import Path\nimport json, sys\n' +
                f"assert Path({str(root / 'ingress/ingress/authentik-prod.conf')!r}).exists()\n" +
                f"with open({str(log)!r}, 'a') as stream:\n" +
                "    stream.write(json.dumps(dict(action='docker', argv=sys.argv[1:])) + '\\n')\n")
            docker.chmod(0o700)
            tasks = local_tasks('authentik', 'ingress.yml')
            for task in tasks:
                for original, replacement in [('community.docker.docker_network', 'fixture_network'),
                        ('community.docker.docker_compose_v2', 'fixture_compose')]:
                    if original in task:
                        task[replacement] = task.pop(original)
            variables.update(authentik_environment='prod', primary_domain='example.org',
                             authentik_ingress_directory=str(root / 'ingress/ingress'),
                             hou1_controller_managed_tls=True,
                             netbird_subnet='100.90.0.0/16')
            with patch.dict(os.environ, {'ANSIBLE_LIBRARY': str(modules),
                    'PATH': str(binary) + ':' + os.environ['PATH']}):
                result = run_play(variables, tasks)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([event['action'] for event in events],
                             ['network', 'network', 'compose', 'docker', 'docker'])
            self.assertEqual(events[2]['services'], ['nginx'])
            self.assertFalse((root / 'ingress/compose.yaml').exists())
            self.assertFalse((root / 'ingress/data').exists())
            self.assertIn('resolver 127.0.0.11',
                          (root / 'ingress/ingress/authentik-prod.conf').read_text())

    def test_authentik_undeclared_or_mismatched_attachment_refuses_before_resources(self):
        guard = local_tasks('authentik', 'ingress-inputs.yml')
        for variables in [dict(authentik_enable_ingress_network=False),
                          dict(authentik_enable_ingress_network=True,
                               authentik_active_network='wrong')]:
            result = run_play(dict(authentik_ingress_network='expected', **variables), guard)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('select this environment', result.stdout)

    def test_webstack_prepares_shared_resources_before_starting_application(self):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/deploy.yml').read_text())
        prepare = next(index for index, task in enumerate(tasks)
                       if task.get('ansible.builtin.import_role', {}).get('name') == 'shared_ingress')
        startup = next(index for index, task in enumerate(tasks)
                       if task['name'] == 'Deploy the verified application without building on the host')
        self.assertLess(prepare, startup)
        shared = yaml.safe_dump(local_tasks('shared_ingress'))
        self.assertNotIn('docker_compose_v2', shared)
        self.assertNotIn('docker compose up', shared)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            variables = application(root)
            modules, log = self.fake_modules(root)
            binary = root / 'bin'
            binary.mkdir()
            activation = binary / 'hou1-compose-apply'
            activation.write_text('#!/usr/bin/env python3\n' +
                'from pathlib import Path\nimport json, sys\n' +
                f"root = Path({str(root)!r})\n" +
                "assert (root / 'ingress/nginx-include.conf').is_file()\n" +
                "assert (root / 'networks/fixture-authentik').is_file()\n" +
                "assert (root / 'networks/hou1-netbird-ingress').is_file()\n" +
                "assert not list((root / 'ingress/ingress').iterdir())\n" +
                "assert sys.argv[1:] == [str(root / 'app'), '--validate-nginx', '--services', 'nginx']\n" +
                f"with open({str(log)!r}, 'a') as stream:\n" +
                "    stream.write(json.dumps(dict(action='startup')) + '\\n')\n" +
                "print(json.dumps(dict(changed=True)))\n")
            activation.chmod(0o700)
            preparation = local_tasks('app_compose') + local_tasks('shared_ingress')
            for task in preparation:
                if 'community.docker.docker_network' in task:
                    task['fixture_network'] = task.pop('community.docker.docker_network')
            with patch.dict(os.environ, {'ANSIBLE_LIBRARY': str(modules),
                    'PATH': str(binary) + ':' + os.environ['PATH']}):
                startup_task = tasks[startup]
                startup_task['ansible.builtin.command'] = startup_task['ansible.builtin.command'].replace(
                    '/usr/local/libexec/hou1-compose-apply', str(activation))
                result = run_play({**variables, 'webstack_bootstrap_ingress_only': True},
                                  preparation + [startup_task])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([json.loads(line)['action'] for line in log.read_text().splitlines()],
                             ['network', 'network', 'startup'])


if __name__ == '__main__':
    unittest.main()
