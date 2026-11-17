"""Ingress consumers share a bounded target without moving services or recovery."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import ROLES, guard_play, run_play, run_plays
from test_controller_tls import receiver
import test_monitoring_exporter as monitoring

collector = monitoring.collector


SELECTOR_TASKS = yaml.safe_load((ROLES / 'shared_ingress/tasks/selector.yml').read_text())
ALTERNATE = {'directory': '/opt/hou1-ingress', 'project': 'hou1-ingress-test', 'file': 'compose.yaml'}


def variables(selector):
    return {'app_directory': '/opt/application',
            **{'shared_ingress_compose_' + key: value for key, value in selector.items()}}


def command(selector):
    result = ['docker', 'compose', '--project-directory', selector['directory']]
    if selector['project']:
        result += ['--project-name', selector['project']]
    if selector['file']:
        result += ['--file', selector['directory'] + '/' + selector['file']]
    return result


class SharedIngressSelectorTest(unittest.TestCase):
    def test_existing_inventory_needs_no_new_selector_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'play.yml'
            play.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
                'vars': {'app_directory': '/opt/application'}, 'tasks': [
                    {'ansible.builtin.import_role': {'name': 'shared_ingress', 'tasks_from': 'selector'}},
                    {'ansible.builtin.assert': {'that': [
                        "shared_ingress_compose_argv == ['docker', 'compose', '--project-directory', '/opt/application']",
                        "shared_ingress_compose_selector == {'directory': '/opt/application', 'project': '', 'file': ''}"]}}]}]))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)],
                                    env={**os.environ, 'ANSIBLE_ROLES_PATH': str(ROLES)},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_explicit_and_removed_selectors_match_the_receiver(self):
        defaults = {'directory': '/opt/application', 'project': '', 'file': ''}
        selectors = [defaults, ALTERNATE, defaults]
        plays = []
        for selector in selectors:
            expected = command(selector)
            plays.append({'variables': variables(selector), 'tasks': SELECTOR_TASKS + [
                {'ansible.builtin.assert': {'that': [
                    'shared_ingress_compose_selector == ' + repr(selector),
                    'shared_ingress_compose_argv == ' + repr(expected)]}}]})
            config = {'app_directory': '/opt/application', 'ingress_compose': selector}
            self.assertEqual(receiver.ingress_command(config), expected)
        result = run_plays(plays)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Older fixed receiver files contain no ingress_compose field.
        with patch.object(receiver, 'run') as run:
            receiver.nginx({'app_directory': '/opt/application'}, '-t')
        run.assert_called_once_with(command(defaults) + ['exec', '-T', 'nginx', 'nginx', '-t'])

    def test_invalid_selectors_fail_before_any_compose_command(self):
        invalid = []
        for key, values in {
                'directory': ['relative', '/', '/opt/../other', '/opt/./other', '/opt//other', '/opt/app\n', 1],
                'project': ['--project', 'Project', 'name; command', 'project\n', [], None],
                'file': ['../compose.yaml', '/tmp/compose.yaml', '--file.yml', 'compose.txt', 'compose.yaml\n', 1]}.items():
            invalid.extend({**ALTERNATE, key: value} for value in values)
        result = run_plays([guard_play(variables(selector), SELECTOR_TASKS,
                                      SELECTOR_TASKS[0]['name']) for selector in invalid])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for selector in [*invalid, None, {}, {**ALTERNATE, 'command': 'other'}]:
            with self.subTest(selector=selector), patch.object(receiver, 'run') as run:
                with self.assertRaises(ValueError):
                    receiver.nginx({'app_directory': '/opt/application', 'ingress_compose': selector}, '-t')
                run.assert_not_called()

    def docker_fixture(self, root):
        binary = root / 'bin'; binary.mkdir()
        log = root / 'commands.jsonl'
        docker = binary / 'docker'
        docker.write_text('#!/usr/bin/env python3\nimport json, sys\n' +
                          f'with open({str(log)!r}, "a") as stream:\n' +
                          ' stream.write(json.dumps(sys.argv[1:]) + "\\n")\n')
        docker.chmod(0o700)
        return binary, log

    def test_identity_and_vpn_apply_validate_and_reload_only_selected_ingress(self):
        cases = [
            ('authentik', 'ingress.yml', 'Attach application Nginx before publishing the Authentik virtual host',
             ['Validate Nginx before reload', 'Reload Nginx after publishing the Authentik virtual host']),
            ('netbird_control_plane', 'main.yml', 'Apply the Nginx network and mounts without rebuilding application services',
             ['Validate the active Nginx configuration before reload',
              'Reload Nginx to pick up directory-mounted ingress changes'])]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, log = self.docker_fixture(root)
            modules = root / 'modules'; modules.mkdir()
            (modules / 'fixture_compose.py').write_text('''
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(project_src=dict(type='str'), project_name=dict(type='str'),
 files=dict(type='list', elements='str'), services=dict(type='list', elements='str'),
 dependencies=dict(type='bool'), state=dict(type='str'), recreate=dict(type='str')))
assert m.params == dict(project_src='/opt/hou1-ingress', project_name='hou1-ingress-test',
 files=['compose.yaml'], services=['nginx'], dependencies=False, state='present', recreate='auto')
m.exit_json(changed=False)
''')
            plays = []
            for role, filename, apply, commands in cases:
                tasks = yaml.safe_load((ROLES / role / 'tasks' / filename).read_text())
                selected = [copy.deepcopy(next(task for task in tasks if task['name'] == name))
                            for name in [apply, *commands]]
                selected[0]['fixture_compose'] = selected[0].pop('community.docker.docker_compose_v2')
                plays.append({'variables': {**variables(ALTERNATE), 'authentik_nginx_config': {'changed': True}},
                              'tasks': SELECTOR_TASKS + selected})
            with patch.dict(os.environ, {'PATH': str(binary) + ':' + os.environ['PATH'],
                                        'ANSIBLE_LIBRARY': str(modules)}):
                result = run_plays(plays)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = [json.loads(line) for line in log.read_text().splitlines()]
            expected = [command(ALTERNATE)[1:] + ['exec', '-T', 'nginx', 'nginx', *args]
                        for args in (['-t'], ['-s', 'reload'])]
            self.assertEqual(actual, expected * 2)

    def test_receiver_config_and_legacy_renewal_keep_certificate_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, log = self.docker_fixture(root)
            receiver_tasks = yaml.safe_load((ROLES / 'controller_tls/tasks/receiver.yml').read_text())
            receiver_config = next(t for t in receiver_tasks if t['name'] == 'Configure the fixed environment certificate allowlist')
            receiver_config['ansible.builtin.copy']['dest'] = str(root / 'receiver.json')
            receiver_config['ansible.builtin.copy'].pop('owner')
            receiver_config['ansible.builtin.copy'].pop('group')
            renewal_tasks = yaml.safe_load((ROLES / 'webstack/tasks/tls.yml').read_text())
            renewal = next(t for t in renewal_tasks if t['name'] == 'Create automated SSL renewal script')
            renewal['copy']['dest'] = str(root / 'renew.sh')
            result = run_play({**variables(ALTERNATE), 'cms_environment': 'test',
                               'hou1_tls_lineages': {}, 'hou1_controller_managed_tls': False},
                              SELECTOR_TASKS + [receiver_config, renewal])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            configured = json.loads((root / 'receiver.json').read_text())
            self.assertEqual(configured['app_directory'], '/opt/application')
            self.assertEqual(configured['certificate_directory'], '/opt/application/certbot/conf')
            self.assertEqual(configured['ingress_compose'], ALTERNATE)
            with patch.dict(os.environ, {'PATH': str(binary) + ':' + os.environ['PATH']}):
                subprocess.run(['bash', str(root / 'renew.sh')], check=True, capture_output=True)
            actual = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(actual[1:], [command(ALTERNATE)[1:] + ['exec', '-T', 'nginx', 'nginx', *args]
                                         for args in (['-t'], ['-s', 'reload'])])
            self.assertIn('/opt/application/certbot/conf:/etc/letsencrypt', actual[0])

    def test_monitoring_pins_project_and_file_and_refuses_wrong_labels(self):
        source = {**ALTERNATE, 'service': 'nginx'}
        state = monitoring.MonitoringExporterTest.container_state(source['directory'], source['service'])
        labels = state['labels']
        labels['com.docker.compose.project'] = source['project']
        labels['com.docker.compose.project.config_files'] = source['directory'] + '/' + source['file']
        with patch.object(collector, 'command', side_effect=['a' * 64, json.dumps(state)]) as run:
            self.assertEqual(collector.source_container(source)[0], 'a' * 64)
        self.assertIn('label=com.docker.compose.project=hou1-ingress-test', run.call_args_list[0].args[0])
        self.assertIn('label=com.docker.compose.project.config_files=/opt/hou1-ingress/compose.yaml',
                      run.call_args_list[0].args[0])
        for field in ('com.docker.compose.project', 'com.docker.compose.project.config_files'):
            wrong = copy.deepcopy(state); wrong['labels'][field] = 'other'
            with patch.object(collector, 'command', side_effect=['a' * 64, json.dumps(wrong)]):
                with self.assertRaises(ValueError): collector.source_container(source)
        config = {'environment': 'test', 'containers': {'postgresql': source},
                  'authentik_backup_directory': '/var/backups/idp', 'request_metrics_enabled': False,
                  'request_metrics_log': '/opt/application/nginx-logs/metrics.jsonl'}
        collector.validate_config(config)
        for changes in ({'project': ''}, {'project': '--other'}, {'file': '../compose.yaml'}, {'file': None}):
            with self.assertRaises(ValueError):
                collector.validate_config({**config, 'containers': {'postgresql': {**source, **changes}}})

    def test_monitoring_guard_uses_the_shared_selector_without_moving_logs(self):
        tasks = yaml.safe_load((ROLES / 'monitoring_exporter/tasks/main.yml').read_text())[:2]
        base = {'app_directory': '/opt/application', 'hou1_monitoring_enabled': True,
                'cms_environment': 'test', 'hou1_monitoring_identity_profile': 'test',
                'hou1_monitoring_inventory_group': 'metrics-target', 'netbird_subnet': '100.91.0.0/16',
                'netbird_server_group': 'test-servers', 'netbird_ip': '100.91.0.10',
                'ansible_host': '100.91.0.10', 'node_bootstrap': False, 'hou1_monitoring_peer_ip': '100.91.0.22'}
        selected = {**base, **variables(ALTERNATE)}
        stale = {**selected, 'hou1_monitoring_containers': {
            'nginx': {'directory': '/opt/application', 'service': 'nginx'}}}
        result = run_plays([guard_play(selected, tasks), guard_play(stale, tasks, tasks[1]['name']),
                           guard_play(base, tasks)], inventory_source={
                               'all': {'children': {'metrics-target': {'hosts': {'localhost': {}}}}}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compose_discovery_and_explicit_selection_keep_their_project_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('SELECTOR_FIXTURE=from-project-root\n')
            (root / 'docker-compose.yml').write_text('services:\n nginx:\n  image: nginx:alpine\n'
                                                      '  environment:\n   MARKER: ${SELECTOR_FIXTURE}\n')
            (root / 'alternate.yaml').write_text('services:\n nginx:\n  image: nginx:alpine\n'
                                                 '  environment:\n   MARKER: explicit-file\n')
            for selector, marker in [({'directory': str(root), 'project': '', 'file': ''}, 'from-project-root'),
                                     ({'directory': str(root), 'project': 'selected-test',
                                       'file': 'alternate.yaml'}, 'explicit-file')]:
                result = subprocess.run(command(selector) + ['config', '--format', 'json'],
                                        cwd='/', capture_output=True, text=True, check=True)
                config = json.loads(result.stdout)
                self.assertEqual(config['services']['nginx']['environment']['MARKER'], marker)
                if selector['project']:
                    self.assertEqual(config['name'], selector['project'])


if __name__ == '__main__':
    unittest.main()
