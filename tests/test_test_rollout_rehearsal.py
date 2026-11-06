import argparse
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import yaml
from helpers import guard_play, run_plays


ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts/test-rollout-rehearsal.py"))


def args(**overrides):
    values = dict(
        environment="test",
        infra_ref="0123456789abcdef0123456789abcdef01234567",
        staff_ref="89abcdef0123456789abcdef0123456789abcdef",
        netbird_ref="abcdef0123456789abcdef0123456789abcdef",
        test_domain="test.example.com",
        issuer="https://auth-test.example.com/application/o/netbird-test/",
        netbird_url="https://netbird-test.example.com",
        cert_ref="cert-test-2026-11-06",
        secret_ref="sops-age-test",
        inventory="ansible/inventory/inventory.yml",
    )
    values.update(overrides)
    return argparse.Namespace(**values)


class TestRolloutRehearsal(unittest.TestCase):
    def test_fresh_vps_entrypoint_rejects_production_and_mutable_source(self):
        playbook = yaml.safe_load((ROOT / 'ansible/playbooks/rehearse-vps.yml').read_text())[0]
        guard = playbook['pre_tasks'][0]
        inventory = {'all': {'children': {'isolated_test_vps': {'hosts': {'fixture': {}}}}}}
        base = dict(ansible_host='203.0.113.20', test_vps_public_ip='203.0.113.20',
                    cms_environment='test', node_bootstrap=True,
                    primary_domain='vps-test.example.invalid', netbird_domain='netbird-test.vps-test.example.invalid',
                    staff_domain='staff-test.vps-test.example.invalid', netbird_subnet='100.91.0.0/16',
                    rehearsal_staff_ref='a' * 40, netbird_token_age_key_file='/tmp/test-only.key',
                    rehearsal_controller_age_key_file='/tmp/test-only.key')
        scenarios = [{}, {'cms_environment': 'production'}, {'netbird_subnet': '100.90.0.0/16'},
                     {'staff_domain': 'staff.example.invalid'}, {'rehearsal_staff_ref': 'main'}]
        plays = [dict(guard_play({**base, **override}, [guard], guard['name'] if override else None), hosts='fixture')
                 for override in scenarios]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_seed_guard_refuses_production_private_access_and_controller_key(self):
        task = yaml.safe_load((ROOT / 'ansible/playbooks/rehearsal-seeds.yml').read_text())[0]['pre_tasks'][0]
        inventory = {'all': {'children': {'isolated_test_vps': {'hosts': {'fixture': {}}}}}}
        base = dict(cms_environment='test', node_bootstrap=True, ansible_host='203.0.113.20',
                    test_vps_public_ip='203.0.113.20', netbird_domain='netbird-test.example.invalid',
                    app_directory='/opt/staff-test', rehearsal_controller_managed_tls=True,
                    rehearsal_host_age_key_source='/tmp/host.key', rehearsal_controller_age_key_file='/tmp/controller.key',
                    rehearsal_seed_directory='/tmp/seeds', rehearsal_bootstrap_admin_cidr='198.51.100.8/32')
        scenarios = [{}, {'cms_environment': 'prod'}, {'node_bootstrap': False},
                     {'ansible_host': '100.91.0.10'}, {'rehearsal_host_age_key_source': '/tmp/controller.key'},
                     {'app_directory': '/opt/production'}, {'rehearsal_bootstrap_admin_cidr': '0.0.0.0/0'}]
        plays = [dict(guard_play({**base, **override}, [task], task['name'] if override else None), hosts='fixture')
                 for override in scenarios]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_commands_are_limited_to_test_inventory(self):
        commands = MODULE["deployment_commands"](args())
        self.assertEqual(len(commands), 4)
        joined = " ".join(" ".join(command) for command in commands)
        self.assertIn("--limit hou1_test", joined)
        self.assertNotIn("hou1_prod", joined)
        self.assertNotIn("netbird-control-plane", joined)

    def test_production_endpoint_and_mutable_ref_are_rejected(self):
        with self.assertRaises(MODULE["RolloutError"]):
            MODULE["validate_config"](args(issuer="https://auth.example.com/application/o/netbird/"))
        with self.assertRaises(MODULE["RolloutError"]):
            MODULE["validate_config"](args(infra_ref="main"))

    def test_verify_records_hashes_without_copying_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = args()
            values.evidence_dir = root
            (root / "deployment-status.json").write_text('[{"step": 1, "returncode": 0}]\n', encoding="utf-8")
            for name in MODULE["REQUIRED_EVIDENCE"]:
                path = root / f"{name}.txt"
                path.write_text(f"{name}=passed\n", encoding="utf-8")
                setattr(values, f"{name}_evidence", str(path))
            report = MODULE["verify_evidence"](values, root)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(set(report["evidence"]), set(MODULE["REQUIRED_EVIDENCE"]))
            saved = json.loads((root / "rollout-rehearsal.json").read_text())
            self.assertNotIn("dns_tls=passed", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
