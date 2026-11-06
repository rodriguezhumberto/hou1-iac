from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SopsAgeTest(unittest.TestCase):
    def test_encrypted_files_have_no_plaintext_secret_values(self):
        for name in ['authentik-test.enc.yaml', 'authentik-prod.enc.yaml']:
            content = (ROOT / 'ansible/secrets' / name).read_text()
            self.assertIn('ENC[AES256_GCM', content)
            self.assertNotRegex(content, r'(?m)^\s*(?:AUTHENTIK_SECRET_KEY|AUTHENTIK_POSTGRES_PASSWORD|STAFF_OIDC_CLIENT_SECRET|NETBIRD_OIDC_CLIENT_SECRET):\s+[^E\n]')

    def test_deployment_decrypts_with_age_and_cleans_root_only_files(self):
        tasks = (ROOT / 'ansible/playbooks/roles/authentik/tasks/main.yml').read_text()
        for required in ['SOPS_AGE_KEY_FILE', 'sops', "mode: '0600'", '.secrets.decrypted.yml', 'state: absent', 'oidc-clients.env']:
            self.assertIn(required, tasks)

    def test_rotation_and_overlap_tools_are_executable_and_sops_config_separates_envs(self):
        for name in ['sops-age-render.sh', 'sops-age-rotate.sh', 'sops-age-overlap-check.sh']:
            path = ROOT / 'scripts' / name
            self.assertTrue(path.stat().st_mode & 0o111)
        config = (ROOT / '.sops.yaml').read_text()
        self.assertIn('authentik-test\\.enc\\.yaml', config)
        self.assertIn('authentik-prod\\.enc\\.yaml', config)
