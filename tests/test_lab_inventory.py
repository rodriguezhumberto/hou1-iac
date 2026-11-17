"""Generated inventories preserve precedence; handoff never trusts an old receipt."""

import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import yaml

from helpers import ROOT
import test_lab_contracts as contracts

SPEC = importlib.util.spec_from_file_location('lab_preparation', ROOT / 'scripts/prepare-lab.py')
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


@unittest.skipUnless(not getattr(contracts.LabContractsTest, '__unittest_skip__', False),
                     'SOPS and age are required for inventory/handoff checks')
class LabInventoryTest(unittest.TestCase):
    def setUp(self):
        fixture = contracts.LabContractsTest()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture, self.root = fixture, fixture.root
        selected_root = Path(fixture.config['hosts']['hou1']['repository'])
        self.patch_root = patch.object(prepare.workflow, 'ROOT', selected_root)
        self.patch_root.start(); self.addCleanup(self.patch_root.stop)
        fixture.inventories()
        self.template = fixture.fixture.input
        self.source = self.root / 'inventory-sources.json'
        self.declaration = dict(version=1, environment='test', workflow=str(self.template), hosts={})
        for index, (service, host) in enumerate(fixture.config['hosts'].items()):
            repository = Path(host['repository'])
            common = repository / 'ansible/inventory/group_vars/all/vars.yml'
            common.parent.mkdir(parents=True)
            common.write_text('shared_marker: repository-value\nansible_python_interpreter: /usr/bin/python3\n')
            fixture.git('-C', str(repository), 'add', '.')
            fixture.git('-C', str(repository), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'shared fixture variables')
            host['revision'] = fixture.git('-C', str(repository), 'rev-parse', 'HEAD')
            inventory = yaml.safe_load(Path(host['inventory']).read_text())
            variables = inventory['all']['children']['test_env']['hosts'][host['target']]
            for key in ('node_bootstrap', 'ansible_host'): variables.pop(key)
            for key in ('application_image_reference', 'application_image_revision', 'deploy_ref',
                        'rehearsal_staff_ref', 'rehearsal_staff_bundle', 'rehearsal_staff_bundle_ref'):
                variables.pop(key, None)
            scoped = self.root / (service + '-scoped.yml'); scoped.write_text('scoped_marker: explicit-source\n'); scoped.chmod(0o600)
            variables['shared_marker'] = 'environment-value'
            overrides = self.root / (service + '-variables.yml')
            overrides.write_text(yaml.safe_dump({'all': {'ansible_python_interpreter': '/usr/bin/python3',
                                                       'shared_marker': 'all-override'},
                                                 'environment': variables}))
            overrides.chmod(0o600)
            self.declaration['hosts'][service] = dict(variables=str(overrides),
                group_vars={'test_env.yml': str(scoped)}, bootstrap_address='192.168.50.' + str(11 + index),
                bootstrap_target=service + '_lab_init', bootstrap_user='fixture')
        declared = copy.deepcopy(fixture.config); declared.pop('application')
        self.template.write_text(json.dumps(declared))
        self.source.write_text(json.dumps(self.declaration)); self.source.chmod(0o600)

    def render(self, name='snapshot'):
        return prepare.render(self.source, self.root / name)

    def hostvars(self, config, service, filename='managed-inventory.yml'):
        host = config['hosts'][service]
        inventory = Path(host['inventory']).with_name(filename)
        argv = [str(Path(config['ansible_bin_directory']) / 'ansible-inventory'), '-i', str(inventory), '--list']
        result = subprocess.run(argv, env=prepare.workflow.environment(config, host),
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)['_meta']['hostvars']

    def test_profiles_keep_repository_values_live_and_preserve_variable_precedence(self):
        path = self.render(); config = prepare.check_snapshot(path)
        second = self.render('repeat')
        for service in ('hou1', 'hou4'):
            host = config['hosts'][service]
            variables = self.hostvars(config, service)[host['target']]
            self.assertEqual(variables['shared_marker'], 'environment-value')
            self.assertEqual(variables['scoped_marker'], 'explicit-source')
            self.assertEqual(variables['ansible_host'], host['ssh_host_ip'])
            self.assertIs(variables['node_bootstrap'], False)
            bootstrap, = self.hostvars(config, service, 'bootstrap-inventory.yml').values()
            self.assertEqual(bootstrap['ansible_host'], self.declaration['hosts'][service]['bootstrap_address'])
            self.assertIs(bootstrap['node_bootstrap'], True)
            snapshot = Path(host['inventory']).parent
            self.assertTrue((snapshot / 'group_vars/all/00-repository.yml').is_symlink())
            self.assertEqual((snapshot / 'managed-inventory.yml').read_bytes(),
                             (second.parent / service / 'managed-inventory.yml').read_bytes())
        self.assertEqual(config['application'], self.fixture.config['application'])
        self.assertEqual(self.template.read_bytes(), Path(self.declaration['workflow']).read_bytes())
        with self.assertRaises(ValueError): self.render()

    def test_invalid_environment_or_duplicate_release_and_topology_cannot_render(self):
        original = copy.deepcopy(self.declaration)
        self.declaration['environment'] = 'prod'; self.source.write_text(json.dumps(self.declaration))
        with self.assertRaises(ValueError): self.render()
        self.declaration = original; self.source.write_text(json.dumps(original))
        values_path = Path(original['hosts']['hou1']['variables'])
        values = yaml.safe_load(values_path.read_text())
        for key, value in [('application_image_revision', 'a' * 40), ('ansible_host', '100.90.0.10'),
                           ('netbird_subnet', '100.90.0.0/16'), ('node_bootstrap', True)]:
            changed = copy.deepcopy(values); changed['environment'][key] = value
            values_path.write_text(yaml.safe_dump(changed))
            with self.subTest(key=key), self.assertRaises(ValueError): self.render()
            self.assertFalse((self.root / 'snapshot').exists())
        self.assertFalse(list(self.root.glob('.lab-render-*')))

    def test_failed_render_leaves_no_partial_snapshot_or_active_changes(self):
        before = self.template.read_bytes()
        with patch.object(prepare, 'write', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): self.render()
        self.assertFalse((self.root / 'snapshot').exists())
        self.assertFalse(list(self.root.glob('.lab-render-*')))
        self.assertEqual(self.template.read_bytes(), before)

    def test_failed_or_stale_handoff_preserves_the_active_manifest(self):
        snapshot = self.render(); before = self.template.read_bytes()
        with patch.object(prepare.workflow.Workflow, 'probe', side_effect=ValueError('VPN unavailable')):
            with self.assertRaises(ValueError): prepare.handoff(snapshot, self.template)
        self.assertEqual(before, self.template.read_bytes())
        # An old success receipt cannot bypass a fresh policy probe.
        with patch.object(prepare.workflow.Workflow, 'probe', return_value=None) as probe:
            prepare.handoff(snapshot, self.template)
        self.assertEqual(probe.call_count, 4)
        self.assertEqual(self.template.read_bytes(), snapshot.read_bytes())
        records = list(Path(self.fixture.config['run_directory']).glob('handoff-*/activation.json'))
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].parent / 'previous-workflow.json').read_bytes(), before)
        with patch.object(prepare.workflow.Workflow, 'probe', side_effect=ValueError('VPN unavailable')) as probe:
            with self.assertRaises(ValueError): prepare.handoff(snapshot, self.template)
        probe.assert_called_once()
        self.assertEqual(self.template.read_bytes(), snapshot.read_bytes())

    def test_changed_shared_values_or_snapshot_and_busy_workflow_stop_handoff(self):
        snapshot = self.render()
        config = prepare.check_snapshot(snapshot)
        lock_path = Path(config['run_directory']) / '.workflow.lock'
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(prepare.workflow.Workflow, 'execute') as execute:
                with self.assertRaises(BlockingIOError): prepare.handoff(snapshot, self.template)
            execute.assert_not_called()
        shared = Path(config['hosts']['hou1']['repository']) / 'ansible/inventory/group_vars/all/vars.yml'
        shared.write_text(shared.read_text() + 'drifted: true\n')
        with self.assertRaises(ValueError): prepare.check_snapshot(snapshot)

    def test_generated_scoped_ciphertext_decrypts_only_on_controller_tasks(self):
        host = self.fixture.config['hosts']['hou1']
        config = Path(host['repository']) / 'ansible/ansible.cfg'
        config.write_text('[defaults]\nvars_plugins_enabled=host_group_vars, community.sops.sops\n'
                          '[community.sops]\nvars_stage=task\n')
        self.fixture.git('-C', host['repository'], 'add', '.')
        self.fixture.git('-C', host['repository'], '-c', 'user.name=Fixture', '-c',
                         'user.email=fixture@example.invalid', 'commit', '-qm', 'scoped vars fixture')
        declared = json.loads(self.template.read_text())
        declared['collections_path'] = os.environ.get('ANSIBLE_COLLECTIONS_PATH', str(ROOT / '.ansible/collections'))
        declared['hosts']['hou1']['revision'] = self.fixture.git('-C', host['repository'], 'rev-parse', 'HEAD')
        self.template.write_text(json.dumps(declared))
        recipient = self.fixture.git_tool('age-keygen', '-y', str(self.fixture.key))
        encrypted = self.root / 'runtime.enc.yaml'
        result = subprocess.run(['sops', '--encrypt', '--config', '/dev/null', '--age', recipient,
            '--input-type', 'json', '--output-type', 'yaml', '/dev/stdin'],
            input=json.dumps({'generated_secret': 'private-runtime-marker'}),
            capture_output=True, text=True, check=True)
        encrypted.write_text(result.stdout); encrypted.chmod(0o600)
        self.declaration['hosts']['hou1']['group_vars'] = {'test_env.sops.yml': str(encrypted)}
        self.source.write_text(json.dumps(self.declaration))
        snapshot = self.render(); selected = prepare.check_snapshot(snapshot)
        inventory_values = self.hostvars(selected, 'hou1')[host['target']]
        self.assertNotIn('generated_secret', inventory_values)
        self.assertNotIn('sops', inventory_values)
        play = self.root / 'check-secret.yml'
        play.write_text(yaml.safe_dump([{'hosts': host['target'], 'gather_facts': False,
            'tasks': [{'ansible.builtin.assert': {'that': "generated_secret == 'private-runtime-marker'"},
                       'no_log': True}]}]))
        process = subprocess.run([str(Path(selected['ansible_bin_directory']) / 'ansible-playbook'),
            '-i', selected['hosts']['hou1']['inventory'], '-c', 'local', str(play)],
            env=prepare.workflow.environment(selected, selected['hosts']['hou1']), capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertNotIn('private-runtime-marker', process.stdout + process.stderr)
        for path in snapshot.parent.rglob('*'):
            if path.is_file(): self.assertNotIn('private-runtime-marker', path.read_text())


if __name__ == '__main__':
    unittest.main()
