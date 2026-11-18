"""Independent ingress rendering and recovery contracts remain explicit and reversible."""
import importlib.util
import json
import unittest
import yaml
from jinja2 import Environment

from helpers import ROOT


FILTER_SPEC = importlib.util.spec_from_file_location(
    'app_compose', ROOT / 'ansible/playbooks/filter_plugins/app_compose.py')
app_compose = importlib.util.module_from_spec(FILTER_SPEC)
FILTER_SPEC.loader.exec_module(app_compose)
RECOVERY_SPEC = importlib.util.spec_from_file_location(
    'ingress_recovery', ROOT / 'ansible/playbooks/roles/shared_ingress/files/validate-recovery.py')
recovery = importlib.util.module_from_spec(RECOVERY_SPEC)
RECOVERY_SPEC.loader.exec_module(recovery)


class IndependentIngressTest(unittest.TestCase):
    def test_default_compose_remains_combined(self):
        result = app_compose.compose_override('/data', '/ingress', 'netbird', '172.20.0.5')
        self.assertNotIn('profiles', result['services']['nginx'])
        self.assertEqual(result['networks']['netbird-ingress']['name'], 'netbird')
        self.assertNotIn('app-network', result['networks'])

    def test_independent_compose_keeps_backend_and_ingress_networks(self):
        result = app_compose.compose_override('/data', '/ingress', 'netbird', '172.20.0.5',
                                              independent=True, app_network='hou1-application')
        self.assertEqual(result['services']['nginx']['profiles'], ['integrated-ingress-disabled'])
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

    def test_disabled_secondary_site_omits_invalid_template_mount(self):
        source = (ROOT / 'ansible/playbooks/roles/shared_ingress/templates/independent-compose.yml.j2').read_text()
        environment = Environment()
        environment.filters['bool'] = bool
        template = environment.from_string(source)
        context = {
            'application_image_compose': {'services': {'nginx': {'image': 'nginx:stable'}}},
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

    def test_recovery_export_uses_a_real_final_newline(self):
        tasks = yaml.safe_load((ROOT / 'ansible/playbooks/roles/shared_ingress/tasks/independent.yml').read_text())
        copy_task = next(task for task in tasks if task['name'] == 'Record the compatible independent recovery export')
        content = copy_task['ansible.builtin.copy']['content']
        self.assertTrue(content.endswith('\n'))
        self.assertFalse(content.endswith('\\n'))


if __name__ == '__main__':
    unittest.main()
