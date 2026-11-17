"""Native release/crypto checks and controller-only workflow failure boundaries."""

import copy
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import unittest
from unittest.mock import patch

import yaml

from helpers import ROLES
from identity_fixtures import identity_names, identity_profile
import test_lab_workflow as workflows

contracts = workflows.workflow.lab_contracts
workflow = workflows.workflow


@unittest.skipUnless(all(shutil.which(tool) for tool in ('sops', 'age', 'age-keygen')),
                     'SOPS and age are required for contract checks')
class LabContractsTest(unittest.TestCase):
    def setUp(self):
        fixture = workflows.LabWorkflowTest()
        fixture.setUpClass(); self.addCleanup(fixture.doClassCleanups)
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root, self.config = fixture.root, fixture.config
        self.config.update(version=2)
        for host in self.config['hosts'].values():
            host['capabilities'] = []
        for name in ['deployment', 'host', 'wrong']:
            path = self.root / (name + '.agekey')
            subprocess.run(['age-keygen', '-o', str(path)], check=True, capture_output=True)
        self.key = self.root / 'deployment.agekey'
        self.env = {**os.environ, 'SOPS_AGE_KEY_FILE': str(self.key)}
        self.names = identity_names()
        self.encrypted = self.root / 'names.enc.json'
        self.encrypt_names()
        source = self.root / 'release-source'
        self.source = source
        self.module = 'astro-app/src/lib/staffAuthorization.js'
        path = source / self.module; path.parent.mkdir(parents=True)
        self.original_source = ('const GROUP_ROLES = Object.freeze(' + json.dumps(
            {group: role for role, group in self.names['role_groups'].items()}) + ');\n')
        path.write_text(self.original_source)
        self.git('init', '-q', '--initial-branch=lab', str(source))
        self.git('-C', str(source), 'add', '.')
        self.commit('release')
        self.bundle = self.root / 'source.bundle'
        self.receipt = self.root / 'image-release.json'
        self.refresh_release()
        self.config['release'] = dict(receipt=str(self.receipt), source_bundle=str(self.bundle),
                                     bundle_ref='refs/heads/lab', authorization_file=self.module)
        self.config['age_key_file'] = str(self.key)
        public_profile = json.loads(json.dumps(identity_profile(private=False)).replace(
            '.example.invalid', '.lab-test.example.invalid'))
        self.host_recipient = self.git_tool('age-keygen', '-y', str(self.root / 'host.agekey'))
        files = {key: '' for key in contracts.CAPABILITY_VARIABLES['hou1']}
        files.update(authentik_identity_secret_source=str(self.encrypted),
                     authentik_controller_age_key_file=str(self.key),
                     rehearsal_host_age_key_source=str(self.root / 'host.agekey'))
        self.one = dict(identity_profile=public_profile, files=files,
            staff_client_id=self.names['staff']['client_id'],
            staff_issuer='https://' + public_profile['issuer_host'] + '/application/o/'
                         + self.names['staff']['client_id'] + '/',
            netbird_client_id=public_profile['netbird']['client_id'],
            staff_users=[{'username': 'reader', 'groups': [self.names['role_groups']['reader']]}],
            authentik_backup_recipient=self.host_recipient, backup_enabled=False,
            backup_recipient='', backup_receiver_ip='', source_bundle=str(self.bundle),
            bundle_ref='refs/heads/lab', source_revision=self.config['application']['revision'])
        self.two = dict(files={key: '' for key in contracts.CAPABILITY_VARIABLES['hou4']},
                        backup_enabled=False, backup_source_ip='', backup_source_host_key=fixture.public)
        self.selections = {'hou1': {'contracts': self.one}, 'hou4': {'contracts': self.two}}

    def git_tool(self, *args):
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()

    def git(self, *args):
        return self.git_tool('git', *args)

    def commit(self, message):
        self.git('-C', str(self.source), '-c', 'user.name=Fixture', '-c',
                 'user.email=fixture@example.invalid', 'commit', '-qam', message)

    def encrypt_names(self):
        recipient = self.git_tool('age-keygen', '-y', str(self.key))
        result = subprocess.run(['sops', '--encrypt', '--config', '/dev/null', '--age', recipient,
            '--input-type', 'json', '--output-type', 'json', '/dev/stdin'],
            input=json.dumps({'IDENTITY_NAMES': self.names}), capture_output=True, text=True, check=True)
        self.encrypted.write_text(result.stdout); self.encrypted.chmod(0o600)

    def refresh_release(self):
        if self.bundle.exists(): self.bundle.unlink()
        self.git('-C', str(self.source), 'bundle', 'create', str(self.bundle), 'refs/heads/lab')
        self.bundle.chmod(0o600)
        revision = self.git('-C', str(self.source), 'rev-parse', 'HEAD')
        repository = runpy.run_path(str(ROLES / 'application_image/files/artifact.py'))['REPOSITORY']
        self.config['application'] = dict(revision=revision, image=repository + '@sha256:' + '2' * 64)
        self.receipt.write_text(json.dumps({'schema': 1, **self.config['application'],
            'platform': 'linux/amd64', 'image_id': 'sha256:' + '3' * 64}))
        self.receipt.chmod(0o600)

    def validate(self):
        contracts.validate(self.config, self.selections, self.env)

    def test_release_names_and_backup_identity_pass_without_changing_inputs(self):
        paths = contracts.capability_paths(self.config, self.selections)
        before = contracts.fingerprints(paths)
        for _ in range(2): self.validate()
        self.assertEqual(before, contracts.fingerprints(paths))
        self.assertEqual(set(self.root.glob('hou-lab-release-*')), set())

    def test_wrong_bundle_ref_and_incremental_bundle_cannot_pass(self):
        original = self.config['application']['revision']
        self.config['application']['revision'] = 'f' * 40
        with self.assertRaises(ValueError): self.validate()
        self.config['application']['revision'] = original
        self.one['bundle_ref'] = 'refs/heads/other'
        with self.assertRaises(ValueError): self.validate()
        self.one['bundle_ref'] = 'refs/heads/lab'
        (self.source / 'second').write_text('new release')
        self.git('-C', str(self.source), 'add', '.'); self.commit('next')
        self.refresh_release()
        self.bundle.unlink()
        self.git('-C', str(self.source), 'bundle', 'create', str(self.bundle), 'lab', '^' + original)
        self.bundle.chmod(0o600)
        self.one['source_revision'] = self.config['application']['revision']
        with self.assertRaises(ValueError): self.validate()

    def test_changed_application_groups_and_executable_mapping_are_rejected(self):
        source = self.source / self.module
        for value in [self.original_source.replace('fixture-staff-reader', 'other-reader'),
                      'const GROUP_ROLES = Object.freeze(buildRoles());\n']:
            source.write_text(value); self.commit('changed role contract'); self.refresh_release()
            self.one['source_revision'] = self.config['application']['revision']
            with self.assertRaises(ValueError): self.validate()

    def test_wrong_identity_environment_client_or_unknown_user_group_is_rejected(self):
        for field, value in [('staff_client_id', 'another-client'),
                             ('staff_issuer', 'https://another.example.invalid/'),
                             ('staff_users', [{'username': 'unknown', 'groups': ['unknown']}])]:
            original = self.one[field]; self.one[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate()
            self.one[field] = original
        self.names['environment'] = 'prod'; self.encrypt_names()
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_selfcheck_identity_and_partial_backup_enablement_are_rejected(self):
        self.one['files']['rehearsal_host_age_key_source'] = str(self.root / 'wrong.agekey')
        with self.assertRaises(ValueError): self.validate()
        self.one['files']['rehearsal_host_age_key_source'] = str(self.root / 'host.agekey')
        self.one['backup_enabled'] = True
        with self.assertRaises(ValueError): self.validate()
        self.two['backup_enabled'] = True
        self.one.update(backup_receiver_ip=self.config['hosts']['hou4']['ssh_host_ip'],
                        backup_recipient=self.host_recipient)
        self.two['backup_source_ip'] = self.config['hosts']['hou1']['ssh_host_ip']
        self.validate()
        # Unified backups do not require the legacy host self-check identity.
        self.one['files']['rehearsal_host_age_key_source'] = ''
        self.validate()
        self.config['hosts']['hou1']['ssh_fingerprint'] = 'SHA256:' + 'Z' * 43
        with self.assertRaises(ValueError): self.validate()
        self.config['hosts']['hou1']['ssh_fingerprint'] = self.fixture.fingerprint
        self.two['backup_source_ip'] = '100.90.0.10'
        with self.assertRaises(ValueError): self.validate()

    def inventories(self):
        for service in workflow.SERVICES:
            self.fixture.inventory(service)
            path = Path(self.config['hosts'][service]['inventory'])
            data = yaml.safe_load(path.read_text())
            values = data['all']['children']['test_env']['hosts'][self.config['hosts'][service]['target']]
            values.update(hou1_backup_enabled=False, hou4_backup_enabled=False)
            if service == 'hou1':
                values.update(**self.one['files'], authentik_environment='test',
                    authentik_identity_profiles={'test': self.one['identity_profile']},
                    staff_oidc_client_id=self.one['staff_client_id'], staff_oidc_issuer=self.one['staff_issuer'],
                    netbird_authentik_client_id=self.one['netbird_client_id'],
                    authentik_staff_users=self.one['staff_users'],
                    authentik_backup_age_recipient=self.one['authentik_backup_recipient'],
                    rehearsal_staff_bundle=self.one['source_bundle'],
                    rehearsal_staff_bundle_ref=self.one['bundle_ref'], rehearsal_staff_ref=self.one['source_revision'])
            path.write_text(yaml.safe_dump(data))

    def runner(self, name):
        folder = self.root / name; folder.mkdir(mode=0o700)
        return workflow.Workflow(self.config, folder)

    def test_real_ansible_input_phase_passes_without_managed_host_commands(self):
        self.one['staff_users'][0]['password'] = 'private-password-marker'
        self.inventories()
        runner = self.runner('controller-only')
        with patch.object(runner, 'probe') as probe:
            runner.execute('inputs')
        probe.assert_not_called()
        receipt = json.loads((runner.run / 'receipt.json').read_text())
        self.assertEqual(receipt['status'], 'passed')
        self.assertIn(str(self.encrypted), receipt['capabilities'])
        self.assertEqual([step['step'] for step in receipt['steps']],
                         ['hou1-inputs', 'hou4-inputs', 'input-contracts'])
        for path in runner.run.iterdir():
            if path.is_file(): self.assertNotIn('private-password-marker', path.read_text())
        # Every selected reference remains checked after validation.
        self.encrypted.write_text('changed ciphertext')
        with self.assertRaises(workflow.WorkflowError): runner.check_inputs()

    def test_failed_contract_stops_before_host_access_or_reconciliation(self):
        self.inventories()
        self.names['role_groups']['reader'] = 'unrelated-reader'; self.encrypt_names()
        runner = self.runner('rejected')
        original = runner.playbook
        with patch.object(runner, 'probe') as probe, patch.object(runner, 'playbook', wraps=original) as playbook:
            with self.assertRaises(ValueError): runner.execute('reconcile')
        probe.assert_not_called()
        self.assertTrue(all(call.kwargs.get('local') for call in playbook.call_args_list))
        self.assertEqual(json.loads((runner.run / 'receipt.json').read_text())['status'], 'failed')

    def test_any_host_input_or_capability_drift_stops_before_remote_playbook(self):
        self.inventories()
        other = self.config['hosts']['hou4']
        for index, path in enumerate([self.encrypted, Path(other['inventory']),
                                     Path(other['repository']) / 'ansible/ansible.cfg']):
            with self.subTest(path=path.name):
                before = path.read_text()
                runner = self.runner('drift-' + str(index))
                def probe(service, verify=True):
                    if service == 'hou4': path.write_text(before + '\n# changed after validation\n')
                original = runner.playbook
                try:
                    with patch.object(runner, 'probe', side_effect=probe), \
                            patch.object(runner, 'playbook', wraps=original) as playbook:
                        with self.assertRaises(workflow.WorkflowError): runner.execute('reconcile')
                    remote = [call for call in playbook.call_args_list if not call.kwargs.get('local')]
                    self.assertEqual(len(remote), 1)  # Rejected before spawning that playbook.
                    self.assertFalse((runner.run / 'hou1-reconcile.log').exists())
                finally:
                    path.write_text(before)

    def test_version_two_reconcile_checks_contracts_before_host_commands(self):
        self.inventories()
        runner = self.runner('ordered')
        events = []
        original, validate = runner.playbook, runner.validate_contracts
        def checked():
            validate(); events.append('contracts')
        def playbook(service, play, log_name, local=False):
            if local: return original(service, play, log_name, local=True)
            events.append('deploy-' + service)
        def probe(service, verify=True):
            events.append(('verify-' if verify else 'access-') + service)
        with patch.object(runner, 'validate_contracts', side_effect=checked), \
                patch.object(runner, 'playbook', side_effect=playbook), patch.object(runner, 'probe', side_effect=probe):
            runner.execute('reconcile')
        self.assertEqual(events, ['contracts', 'access-hou1', 'access-hou4',
                                 'deploy-hou1', 'verify-hou1', 'deploy-hou4', 'verify-hou4'])

    def test_version_two_production_membership_fails_before_contract_or_host_access(self):
        self.inventories()
        host = self.config['hosts']['hou1']
        path = Path(host['inventory'])
        data = yaml.safe_load(path.read_text())
        data['all']['children']['prod_env'] = {'hosts': {host['target']: {}}}
        path.write_text(yaml.safe_dump(data))
        runner = self.runner('production-rejected')
        with patch.object(runner, 'probe') as probe, \
                patch.object(runner, 'validate_contracts') as validate:
            with self.assertRaises(workflow.WorkflowError): runner.execute('reconcile')
        probe.assert_not_called(); validate.assert_not_called()

    def test_version_two_derives_release_and_refuses_duplicate_selection(self):
        self.inventories()
        declared = copy.deepcopy(self.config); declared.pop('application')
        self.fixture.input.write_text(json.dumps(declared))
        self.assertEqual(workflow.load_config(self.fixture.input)['application'], self.config['application'])
        declared['application'] = self.config['application']
        self.fixture.input.write_text(json.dumps(declared))
        with self.assertRaises(workflow.WorkflowError): workflow.load_config(self.fixture.input)

    def test_incomplete_or_mutable_release_receipts_are_rejected(self):
        original = json.loads(self.receipt.read_text())
        for changes in [{'platform': 'linux/arm64'}, {'schema': True},
                        {'image': original['image'].split('@')[0] + ':latest'},
                        {'revision': 'main'}, {'image_id': ''}]:
            self.receipt.write_text(json.dumps({**original, **changes}))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                contracts.load_release(self.config['release'])


if __name__ == '__main__':
    unittest.main()
