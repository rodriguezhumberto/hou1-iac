import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from helpers import ROOT, run_play


class SopsAgeTest(unittest.TestCase):
    def test_encrypted_files_have_no_plaintext_secret_values(self):
        for name in ['authentik-test.enc.yaml', 'authentik-prod.enc.yaml']:
            content = (ROOT / 'ansible/secrets' / name).read_text()
            self.assertIn('ENC[AES256_GCM', content)
            self.assertNotRegex(content, r'(?m)^\s*(?:AUTHENTIK_SECRET_KEY|AUTHENTIK_POSTGRES_PASSWORD|STAFF_OIDC_CLIENT_SECRET|NETBIRD_OIDC_CLIENT_SECRET):\s+[^E\n]')

    @unittest.skipUnless(shutil.which('sops') and shutil.which('age-keygen'), 'SOPS/age binaries required')
    def test_deployment_loads_on_controller_without_remote_access_or_logged_secrets(self):
        deployment = next(task for task in yaml.safe_load(
            (ROOT / 'ansible/playbooks/roles/authentik/tasks/main.yml').read_text()) if 'block' in task)
        load = deployment['block'][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, wrong_key, encrypted = root / 'key', root / 'wrong-key', root / 'secrets.enc.yaml'
            for identity in (key, wrong_key):
                subprocess.run(['age-keygen', '-o', str(identity)], check=True, capture_output=True)
            recipient = subprocess.check_output(['age-keygen', '-y', str(key)], text=True).strip()
            secret = 'literal-secret-{{ undefined_variable }}'
            encrypted.write_text(subprocess.run(
                ['sops', '--encrypt', '--config', '/dev/null', '--age', recipient,
                 '--input-type', 'json', '--output-type', 'yaml', '/dev/stdin'],
                input=json.dumps({'AUTHENTIK_SECRET_KEY': secret}), text=True,
                check=True, capture_output=True).stdout)
            for identity in (key, wrong_key):
                result = run_play({
                    'ansible_connection': 'ssh', 'ansible_host': '192.0.2.1',
                    'authentik_secret_source': str(encrypted),
                    'authentik_controller_age_key_file': str(identity),
                    'fixture_secret_sha256': hashlib.sha256(secret.encode()).hexdigest(),
                }, [load, {'ansible.builtin.assert': {
                    'that': "authentik_decrypted.AUTHENTIK_SECRET_KEY | hash('sha256') == fixture_secret_sha256"},
                    'no_log': True}])
                self.assertEqual(result.returncode == 0, identity == key, result.stdout + result.stderr)
                self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertEqual({path.name for path in root.iterdir()}, {'key', 'wrong-key', 'secrets.enc.yaml'})

    @unittest.skipUnless(shutil.which('sops') and shutil.which('age-keygen'), 'SOPS/age binaries required')
    def test_inventory_loads_only_the_target_environment_and_preserves_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group_vars, host_vars = root / 'group_vars', root / 'host_vars'
            group_vars.mkdir()
            host_vars.mkdir()
            inventory = root / 'inventory.yml'
            inventory.write_text(yaml.safe_dump({'all': {'children': {
                environment + '_env': {'hosts': {environment + '_target': {}}}
                for environment in ('test', 'prod')}}}))
            (group_vars / 'all.yml').write_text('public_value: shared\nprecedence: all\n')
            for environment in ('test', 'prod'):
                key = root / (environment + '.key')
                subprocess.run(['age-keygen', '-o', str(key)], check=True, capture_output=True)
                recipient = subprocess.check_output(['age-keygen', '-y', str(key)], text=True).strip()
                (group_vars / (environment + '_env.yml')).write_text(
                    'precedence: public-group\nsecret_alias: "{{ session_secret }}"\n')
                (host_vars / (environment + '_target.yml')).write_text('precedence: host\n')
                (group_vars / (environment + '_env.sops.yml')).write_text(subprocess.run(
                    ['sops', '--encrypt', '--config', '/dev/null', '--age', recipient,
                     '--input-type', 'json', '--output-type', 'yaml', '/dev/stdin'],
                    input=json.dumps({'session_secret': environment + '-fixture-secret', 'precedence': 'encrypted-group'}),
                    text=True, check=True, capture_output=True).stdout)
            playbook = root / 'play.yml'
            playbook.write_text(yaml.safe_dump([{
                'hosts': 'all', 'gather_facts': False,
                'tasks': [{'ansible.builtin.assert': {'that': [
                    "public_value == 'shared'", "precedence == 'host'",
                    "secret_alias == inventory_hostname.split('_')[0] ~ '-fixture-secret'",
                ]}, 'no_log': True, 'tags': ['deploy']}],
            }]))
            for environment, identity in [('test', 'test'), ('prod', 'prod'), ('prod', 'test')]:
                result = subprocess.run([
                    'ansible-playbook', '-i', str(inventory), str(playbook),
                    '--limit', environment + '_target', '--tags', 'deploy',
                ], env={**os.environ, 'ANSIBLE_CONFIG': str(ROOT / 'ansible/ansible.cfg'),
                        'SOPS_AGE_KEY_FILE': str(root / (identity + '.key'))},
                    text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode == 0, environment == identity, result.stdout + result.stderr)
                for secret in ('test-fixture-secret', 'prod-fixture-secret'):
                    self.assertNotIn(secret, result.stdout + result.stderr)

    def test_rotation_and_overlap_tools_are_executable_and_sops_config_separates_envs(self):
        for name in ['sops-age-render.sh', 'sops-age-rotate.sh', 'sops-age-overlap-check.sh']:
            path = ROOT / 'scripts' / name
            self.assertTrue(path.stat().st_mode & 0o111)
        config = (ROOT / '.sops.yaml').read_text()
        self.assertIn('authentik-test\\.enc\\.yaml', config)
        self.assertIn('authentik-prod\\.enc\\.yaml', config)
