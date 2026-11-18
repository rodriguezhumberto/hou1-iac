"""Exercise inventory/environment boundaries with a transport that cannot reach hosts."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
NODE = 'hou1'
PLAYBOOKS = ROOT / 'ansible/playbooks'
GUARD = PLAYBOOKS / 'tasks/validate-target.yml'
CONTROLLER_PLAYS = {'tls-renewal-peer.yml', 'tls-renewal-schedule.yml', 'certificate-renewal.yml'}


class EnvironmentTargetingTest(unittest.TestCase):
    def run_fixture(self, plays, inventory, extra_args=()):
        with tempfile.TemporaryDirectory(prefix=NODE + '-targeting-') as folder:
            root = Path(folder)
            marker = root / 'transport-used'
            # Every remote operation fails locally, including accidental fact gathering.
            (root / 'target_trap.py').write_text(
                'import os\nfrom pathlib import Path\n'
                'from ansible.plugins.connection import ConnectionBase\n'
                'from ansible.errors import AnsibleConnectionFailure\n'
                'class Connection(ConnectionBase):\n'
                '    transport = "target_trap"\n    has_pipelining = True\n'
                '    def trap(self, *args, **kwargs):\n'
                '        Path(os.environ["TARGET_TRAP_MARKER"]).touch()\n'
                '        raise AnsibleConnectionFailure("OFFLINE_TRANSPORT_TRAP")\n'
                '    _connect = exec_command = put_file = fetch_file = trap\n'
                '    def close(self): pass\n')
            config = root / 'ansible.cfg'
            config.write_text('[defaults]\nvars_plugins_enabled = host_group_vars\n'
                              'retry_files_enabled = False\n')
            inv = root / 'inventory.yml'
            inv.write_text(yaml.safe_dump(inventory))
            play = root / 'play.yml'
            play.write_text(yaml.safe_dump(plays))
            result = subprocess.run(
                ['ansible-playbook', '-i', str(inv), str(play), *extra_args],
                cwd=root, text=True, capture_output=True, timeout=90,
                env={**os.environ, 'ANSIBLE_CONFIG': str(config),
                     'ANSIBLE_VARS_ENABLED': 'host_group_vars',
                     'ANSIBLE_ROLES_PATH': str(PLAYBOOKS / 'roles'),
                     'ANSIBLE_CONNECTION_PLUGINS': str(root),
                     'ANSIBLE_FILTER_PLUGINS': str(PLAYBOOKS / 'filter_plugins'),
                     'ANSIBLE_ACTION_PLUGINS': str(PLAYBOOKS / 'action_plugins'),
                     'TARGET_TRAP_MARKER': str(marker), 'ANSIBLE_NOCOLOR': '1'})
            return result, marker.exists()

    @staticmethod
    def inventory(groups):
        return {'all': {'vars': {'ansible_connection': 'target_trap'}, 'children': {
            group: {'hosts': {'fixture': {}}} for group in groups}}}

    @staticmethod
    def variables(production=False, **changes):
        return {
            'cms_environment' if NODE == 'hou1' else 'hou4_environment': 'production' if production else 'test',
            'netbird_server_group': 'prod-servers' if production else 'test-servers',
            'node_bootstrap': False, 'ansible_host': '100.91.0.10',
            'netbird_ip': '100.91.0.10', 'test_vps_public_ip': '192.0.2.11',
            'prod_vps_public_ip': '192.0.2.10', 'prod_netbird_ip': '100.90.0.10',
            'ansible_python_interpreter': sys.executable, **changes,
        }

    def test_environment_and_address_mismatches_fail_before_transport(self):
        cases = [
            (['test_env'], self.variables(), True),
            (['prod_env'], self.variables(True), True),
            (['test_env', NODE + '_test_init'], self.variables(node_bootstrap=True, ansible_host='192.0.2.11'), True),
            (['prod_env', NODE + '_prod_init'], self.variables(True, node_bootstrap=True, ansible_host='192.0.2.10'), True),
            (['test_env', 'prod_env'], self.variables(), False),
            ([], self.variables(), False),
            (['prod_env'], self.variables(), False),
            (['test_env'], self.variables(True), False),
            (['test_env'], self.variables(netbird_server_group='prod-servers'), False),
            (['prod_env'], self.variables(True, netbird_server_group='test-servers'), False),
            (['test_env', NODE + '_prod'], self.variables(), False),
            (['prod_env', NODE + '_test_init'], self.variables(True), False),
            (['test_env'], self.variables(node_bootstrap=True, ansible_host='192.0.2.11'), False),
            (['test_env'], self.variables(ansible_host='192.0.2.11'), False),
            (['test_env'], self.variables(ansible_host='100.90.0.10', netbird_ip='100.90.0.10'), False),
            (['test_env', NODE + '_test_init'], self.variables(node_bootstrap=True, ansible_host='192.0.2.10', test_vps_public_ip='192.0.2.10'), False),
        ]
        if NODE == 'hou1':
            cases += [
                (['isolated_test_vps'], self.variables(), True),
                (['isolated_test_vps', 'prod_env'], self.variables(), False),
                (['prod_env'], self.variables(True, authentik_environment='test'), False),
                (['test_env'], self.variables(deploy_environment='prod'), False),
                (['prod_env'], self.variables(True, deploy_environment='production'), False),
            ]
        # A failed fixture host is removed from later plays; use separate aliases
        # so both accepted and rejected cases execute in one Ansible invocation.
        inventory = {'all': {'vars': {'ansible_connection': 'target_trap'}, 'children': {}}}
        plays = []
        for i, (groups, variables, valid) in enumerate(cases):
            host = f'fixture_{i}'
            for group in groups or ['unclassified']:
                inventory['all']['children'].setdefault(group, {'hosts': {}})['hosts'][host] = {}
            plays.append({'hosts': host, 'gather_facts': False, 'vars': variables, 'tasks': [{
                'block': [{'ansible.builtin.import_tasks': str(GUARD)},
                          {'ansible.builtin.assert': {'that': valid}}],
                'rescue': [{'ansible.builtin.assert': {'that': [
                    not valid, "ansible_failed_task.action == 'ansible.builtin.assert'",
                    'ansible_failed_result.evaluated_to is defined',
                    'ansible_failed_result.evaluated_to == false',
                    "ansible_failed_task.name in ['Require one unambiguous inventory environment before connecting', "
                    "'Bind deployment inputs to the inventory environment', "
                    "'Validate the connection address before gathering facts or changing controller state']",
                ]}}],
            }]})
        result, connected = self.run_fixture(plays, inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(connected)

    def test_tagged_bootstrap_fails_before_transport(self):
        play = {'hosts': 'fixture', 'gather_facts': False,
                'vars': self.variables(True, node_bootstrap=True, ansible_host='192.0.2.10'),
                'pre_tasks': [
                    {'ansible.builtin.import_tasks': str(GUARD), 'tags': ['always']},
                    {'ansible.builtin.setup': {}, 'tags': ['always']}], 'tasks': []}
        for tags in (['--tags', 'component'], ['--skip-tags', 'component']):
            result, connected = self.run_fixture([play], self.inventory(['prod_env', NODE + '_prod_init']), tags)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(connected, result.stdout + result.stderr)
            self.assertIn('Bootstrap requires a complete untagged run', result.stdout)

    def test_valid_targets_reach_fact_gathering_after_the_guard(self):
        for production in (False, True):
            play = {'hosts': 'fixture', 'gather_facts': False,
                    'vars': self.variables(production), 'pre_tasks': [
                        {'ansible.builtin.import_tasks': str(GUARD), 'tags': ['always']},
                        {'ansible.builtin.setup': {}, 'tags': ['always']}], 'tasks': []}
            result, connected = self.run_fixture([play], self.inventory(['prod_env' if production else 'test_env']), ['--tags', 'component'])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(connected, result.stdout + result.stderr)
            self.assertIn('OFFLINE_TRANSPORT_TRAP', result.stdout)

    def test_every_host_entrypoint_rejects_mismatched_inputs_under_component_tags(self):
        plays = []
        for path in sorted(PLAYBOOKS.glob('*.yml')):
            if path.name in CONTROLLER_PLAYS:
                continue
            for original in yaml.safe_load(path.read_text()):
                if 'hosts' not in original:
                    continue
                self.assertIs(original['gather_facts'], False, path.name)
                pre = original['pre_tasks']
                self.assertEqual(pre[0]['ansible.builtin.import_tasks'], 'tasks/validate-target.yml', path.name)
                self.assertIn('always', pre[0]['tags'], path.name)
                # Keep the real public guard and play variables. Replace downstream
                # work with an unconditional transport trap: no secret loading or
                # host/controller mutation is available to this fixture.
                first = copy.deepcopy(pre[0])
                first['ansible.builtin.import_tasks'] = str(GUARD)
                plays.append({
                    'name': path.name + ': ' + original['name'], 'hosts': 'fixture',
                    'gather_facts': original['gather_facts'],
                    'vars': {**original.get('vars', {}), **self.variables()},
                    'pre_tasks': [first, {'ansible.builtin.setup': {}, 'tags': ['always']}],
                })
        # Recover each expected assertion so every public entrypoint executes.
        for play in plays:
            first, trap = play['pre_tasks']
            play['pre_tasks'] = [{
                'block': [first, trap], 'rescue': [{'ansible.builtin.assert': {'that': [
                    "ansible_failed_task.action == 'ansible.builtin.assert'",
                    "ansible_failed_task.name == 'Bind deployment inputs to the inventory environment'",
                ]}}], 'tags': ['always'],
            }]
        result, connected = self.run_fixture(plays, self.inventory(['prod_env']), ['--tags', 'component'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(connected)
        self.assertEqual(result.stdout.count('Bind deployment inputs to the inventory environment]'), len(plays))

    def test_production_identity_requires_matching_profile_selection(self):
        for name in ('authentik.yml', 'authentik-bootstrap.yml'):
            original = yaml.safe_load((PLAYBOOKS / name).read_text())[0]
            for selected in (None, 'test', 'prod'):
                values = {**original['vars'], **self.variables(True)}
                if selected is not None:
                    values['deploy_environment'] = selected
                play = {'hosts': 'fixture', 'gather_facts': False, 'vars': values,
                        'pre_tasks': [
                            {'ansible.builtin.import_tasks': str(GUARD), 'tags': ['always']},
                            {'ansible.builtin.setup': {}, 'tags': ['always']}], 'tasks': []}
                result, connected = self.run_fixture([play], self.inventory(['prod_env']), ['--tags', 'authentik'])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(connected, selected == 'prod', result.stdout + result.stderr)
                expected = 'OFFLINE_TRANSPORT_TRAP' if selected == 'prod' else 'Environment and identity inputs must match inventory membership'
                self.assertIn(expected, result.stdout)

    def test_default_test_groups_are_empty_and_ssh_keys_are_checked(self):
        data = yaml.safe_load((ROOT / 'ansible/inventory/inventory.yml').read_text())
        children = data['all']['children']
        for group in (NODE + '_test', NODE + '_test_init'):
            self.assertEqual(children['test_env']['children'][group]['hosts'], {})
        for env in ('test', 'prod'):
            for group in children[env + '_env']['children'].values():
                self.assertIn('StrictHostKeyChecking=yes', group['vars']['ansible_ssh_common_args'])
        self.assertIn('host_key_checking = True', (ROOT / 'ansible/ansible.cfg').read_text())


if __name__ == '__main__':
    unittest.main()
