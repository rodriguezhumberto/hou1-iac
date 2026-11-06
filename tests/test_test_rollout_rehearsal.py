import argparse
import json
from pathlib import Path
import runpy
import tempfile
import unittest


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
