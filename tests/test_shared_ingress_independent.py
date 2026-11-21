"""Independent ingress rendering and recovery contracts remain explicit and reversible."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
from jinja2 import Environment

from helpers import ROOT, run_play


FILTER_SPEC = importlib.util.spec_from_file_location(
    'app_compose', ROOT / 'ansible/playbooks/filter_plugins/app_compose.py')
app_compose = importlib.util.module_from_spec(FILTER_SPEC)
FILTER_SPEC.loader.exec_module(app_compose)
RECOVERY_SPEC = importlib.util.spec_from_file_location(
    'ingress_recovery', ROOT / 'ansible/playbooks/roles/shared_ingress/files/validate-recovery.py')
recovery = importlib.util.module_from_spec(RECOVERY_SPEC)
RECOVERY_SPEC.loader.exec_module(recovery)
CUTOVER_SPEC = importlib.util.spec_from_file_location(
    'ingress_cutover', ROOT / 'ansible/playbooks/roles/shared_ingress/files/cutover.py')
cutover = importlib.util.module_from_spec(CUTOVER_SPEC)
CUTOVER_SPEC.loader.exec_module(cutover)


class IndependentIngressTest(unittest.TestCase):
    def ingress_model(self, application, installed=None, source=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, ingress, binary = (root / name for name in ('application', 'ingress', 'bin'))
            for path in (app, ingress, binary):
                path.mkdir()
            (app / 'docker-compose.yml').write_text(json.dumps(application))
            if installed is not None:
                (ingress / 'docker-compose.yml').write_text(json.dumps(installed))
            docker = binary / 'docker'
            docker.write_text(f'#!{sys.executable}\n' + '''import json,pathlib,sys
args=sys.argv[1:]
directory=pathlib.Path(args[args.index('--project-directory')+1])
model=directory/'docker-compose.yml'
if '--file' in args:model=pathlib.Path(args[args.index('--file')+1])
print(json.dumps(json.loads(model.read_text())))
''')
            docker.chmod(0o755)
            variables = {'app_directory': str(app), 'shared_ingress_directory': str(ingress),
                         'shared_ingress_project': 'fixture-ingress'}
            if source is not None:
                variables['application_image_compose'] = source
            tasks = yaml.safe_load((ROOT / 'ansible/playbooks/roles/shared_ingress/tasks/nginx-model.yml').read_text())
            expected = (source if source is not None else installed if installed is not None else application)
            if 'nginx' in expected.get('services', {}):
                tasks += [{'ansible.builtin.assert': {'that': [
                    "shared_ingress_nginx_service.image == 'nginx:fixture'",
                    ('application_image_compose is not defined' if source is None else
                     'application_image_compose == ' + repr(source)),
                ]}}]
            return run_play(variables, [{'block': tasks,
                'environment': {'PATH': str(binary) + ':' + os.environ['PATH']}}])

    def test_identity_only_reconcile_reads_the_existing_independent_listener(self):
        result = self.ingress_model({'services': {'application': {'image': 'app:fixture'}}},
                                    {'services': {'nginx': {'image': 'nginx:fixture', 'ports': ['443:443']}}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_migration_uses_the_deployed_legacy_listener(self):
        result = self.ingress_model({'services': {'nginx': {'image': 'nginx:fixture', 'ports': ['443:443']}}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_application_release_remains_the_explicit_listener_image_source(self):
        result = self.ingress_model({}, {'services': {'nginx': {'image': 'nginx:previous'}}},
                                    {'services': {'nginx': {'image': 'nginx:fixture', 'ports': ['443:443']}}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_existing_ingress_does_not_fall_back_to_legacy(self):
        result = self.ingress_model({'services': {'nginx': {'image': 'nginx:fixture', 'ports': ['443:443']}}},
                                    {'services': {'application': {'image': 'app:fixture'}}})
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Require a supported Nginx service', result.stdout)

    def test_default_compose_remains_combined(self):
        result = app_compose.compose_override('/data', '/ingress', 'netbird', '172.20.0.5')
        self.assertNotIn('profiles', result['services']['nginx'])
        self.assertNotIn('stop_signal', result['services']['nginx'])
        self.assertNotIn('stop_grace_period', result['services']['nginx'])
        self.assertEqual(result['networks']['netbird-ingress']['name'], 'netbird')
        self.assertNotIn('app-network', result['networks'])

    def test_independent_compose_keeps_backend_and_ingress_networks(self):
        result = app_compose.compose_override('/data', '/ingress', 'netbird', '172.20.0.5',
                                              independent=True, app_network='hou1-application')
        self.assertEqual(result['services']['nginx']['profiles'], ['integrated-ingress-disabled'])
        self.assertEqual(result['services']['nginx']['stop_signal'], 'SIGQUIT')
        self.assertEqual(result['services']['nginx']['stop_grace_period'], '30s')
        self.assertEqual(result['networks']['app-network'],
                         {'external': True, 'name': 'hou1-application'})
        self.assertEqual(result['networks']['netbird-ingress']['name'], 'netbird')

    def test_recovery_manifest_accepts_empty_legacy_selector_and_rejects_mutation(self):
        ingress = '/opt/application-ingress'
        manifest = {
            'schema': 1, 'mode': 'independent', 'application_directory': '/opt/application',
            'application_project': {'directory': '/opt/application', 'project': '', 'file': ''},
            'ingress_project': {'directory': ingress, 'project': 'hou1-ingress', 'file': 'docker-compose.yml'},
            'backend_network': 'hou1-application', 'listener_service': 'nginx',
            'rollback': {
                'stop_independent': ['docker', 'compose', '--project-directory', ingress,
                                     '--project-name', 'hou1-ingress', '--file', ingress + '/docker-compose.yml',
                                     'stop', 'nginx'],
                'start_legacy': ['docker', 'compose', '--project-directory', '/opt/application', '--profile',
                                 'integrated-ingress-disabled', 'up', '-d', '--no-deps', 'nginx']}}
        self.assertEqual(recovery.validate(manifest), manifest)
        for mutation in ({'backend_network': 'bad/name'}, {'listener_service': 'app'},
                         {'rollback': {}}):
            changed = json.loads(json.dumps(manifest))
            changed.update(mutation)
            with self.assertRaises(ValueError):
                recovery.validate(changed)

    def test_boot_unit_waits_for_the_selected_vpn_address(self):
        service = (ROOT / 'ansible/playbooks/roles/shared_ingress/templates/independent.service.j2').read_text()
        self.assertIn('--require-address {{ netbird_ip', service)
        self.assertIn('--interface {{ netbird_interface', service)

    def test_independent_listener_has_graceful_stop_and_compose_readiness_gate(self):
        source = (ROOT / 'ansible/playbooks/roles/shared_ingress/templates/independent-compose.yml.j2').read_text()
        environment = Environment()
        environment.filters['bool'] = bool
        rendered = environment.from_string(source).render(
            shared_ingress_nginx_service={'image': 'nginx:stable'},
            app_directory='/opt/application', netbird_control_plane_directory='/opt/netbird',
            netbird_ingress_proxy_ip='172.30.80.2', netbird_ingress_network='netbird-ingress',
            shared_ingress_app_network='application', authentik_enable_ingress_network=False,
            webstack_secondary_site_enabled=False)
        service = yaml.safe_load(rendered)['services']['nginx']
        self.assertEqual(service['stop_signal'], 'SIGQUIT')
        self.assertEqual(service['stop_grace_period'], '30s')
        self.assertEqual(service['healthcheck'], {
            'test': ['CMD', 'nginx', '-t'], 'interval': '5s', 'timeout': '3s',
            'retries': 12, 'start_period': '5s'})

    def test_cutover_runs_as_one_host_local_systemd_operation(self):
        tasks = yaml.safe_load((ROOT / 'ansible/playbooks/roles/webstack/tasks/deploy.yml').read_text())
        cutover_task = next(task for task in tasks if 'block' in task and task.get('when'))
        command = cutover_task['block'][0]['ansible.builtin.command']['argv']
        self.assertEqual(command[:6], ['systemd-run', '--quiet', '--wait', '--collect',
                                       '--service-type=oneshot',
                                       '/usr/local/libexec/hou1-ingress-cutover'])
        self.assertIn('host-local helper restored', cutover_task['rescue'][0]['ansible.builtin.fail']['msg'])

    def test_host_local_cutover_resolves_relative_compose_files_from_project(self):
        command = cutover.compose('/opt/ingress', 'ingress', 'docker-compose.yml')
        self.assertEqual(command[-2:], ['--file', '/opt/ingress/docker-compose.yml'])

    def test_host_local_cutover_stops_independent_before_legacy_restore(self):
        args = type('Args', (), {
            'legacy_directory': '/opt/application', 'legacy_project': '', 'legacy_file': '',
            'independent_directory': '/opt/application-ingress',
            'independent_project': 'hou1-ingress', 'independent_file': 'docker-compose.yml'})()
        calls = []
        def record(command):
            calls.append(command)
            if command[0] == '/usr/local/libexec/hou1-compose-apply':
                raise RuntimeError('activation failed')
        with patch.object(cutover, 'run', side_effect=record), self.assertRaises(RuntimeError):
            cutover.cutover(args)
        self.assertEqual([call[-2:] for call in calls], [
            ['stop', 'nginx'], ['/opt/application-ingress/docker-compose.yml', '--validate-nginx'],
            ['stop', 'nginx'], ['--no-deps', 'nginx']])
        self.assertLess(calls.index(calls[2]), calls.index(calls[3]))

    def test_disabled_secondary_site_omits_invalid_template_mount(self):
        source = (ROOT / 'ansible/playbooks/roles/shared_ingress/templates/independent-compose.yml.j2').read_text()
        environment = Environment()
        environment.filters['bool'] = bool
        template = environment.from_string(source)
        context = {
            'shared_ingress_nginx_service': {'image': 'nginx:stable'},
            'app_directory': '/opt/application',
            'netbird_control_plane_directory': '/opt/netbird',
            'netbird_ingress_proxy_ip': '172.30.80.2',
            'netbird_ingress_network': 'netbird-ingress',
            'shared_ingress_app_network': 'application',
            'authentik_enable_ingress_network': False,
        }
        disabled = template.render(**context, webstack_secondary_site_enabled=False)
        self.assertNotIn('secondary.conf.template:/etc/nginx/templates/secondary.conf.template', disabled)
        enabled = template.render(**context, webstack_secondary_site_enabled=True)
        self.assertIn('secondary.conf.template:/etc/nginx/templates/secondary.conf.template', enabled)

    def test_independent_role_installs_host_local_cutover_helper(self):
        tasks = yaml.safe_load((ROOT / 'ansible/playbooks/roles/shared_ingress/tasks/independent.yml').read_text())
        task = next(task for task in tasks if task['name'] == 'Install the host-local ingress cutover helper')
        self.assertEqual(task['ansible.builtin.copy']['dest'], '/usr/local/libexec/hou1-ingress-cutover')

    def test_recovery_export_uses_a_real_final_newline(self):
        tasks = yaml.safe_load((ROOT / 'ansible/playbooks/roles/shared_ingress/tasks/independent.yml').read_text())
        copy_task = next(task for task in tasks if task['name'] == 'Record the compatible independent recovery export')
        content = copy_task['ansible.builtin.copy']['content']
        self.assertTrue(content.endswith('\n'))
        self.assertFalse(content.endswith('\\n'))


if __name__ == '__main__':
    unittest.main()
