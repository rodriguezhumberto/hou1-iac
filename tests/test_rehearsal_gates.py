"""Security boundaries and durable alert transition/delivery behavior."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import runpy
import tempfile
import threading
import unittest
import yaml
from helpers import ROOT, guard_play, run_plays

ISOLATION = runpy.run_path(
    str(ROOT / "ansible/playbooks/roles/rehearsal_gates/files/isolation.py")
)
MONITOR = runpy.run_path(str(ROOT / "scripts/rehearsal-monitor.py"))


class RehearsalGateTest(unittest.TestCase):
    def config(self):
        return {
            "environment": "test",
            "deny_cidrs": ["198.51.100.7/32", "2001:db8:1::7/128"],
            "test_addresses": ["203.0.113.9", "2001:db8:2::9"],
            "allow_local_cidrs": ["203.0.113.9/32", "2001:db8:2::9/128"],
            "allow_update_cidrs": ["192.0.2.9/32", "2001:db8:3::9/128"],
        }

    def test_firewall_refuses_production_target_overlap_and_open_egress(self):
        for override in [
            {"environment": "production"},
            {"deny_cidrs": ["203.0.113.9/32", "2001:db8:1::7/128"]},
            {"allow_update_cidrs": ["0.0.0.0/0", "::/0"]},
            {"deny_cidrs": []},
        ]:
            with self.subTest(override=override), self.assertRaises(AssertionError):
                ISOLATION["rules"]({**self.config(), **override})

    def test_block_precedes_established_traffic_and_netbird_forward_bypasses(self):
        rules = ISOLATION["rules"](self.config())
        self.assertNotIn("flush ruleset", rules)
        self.assertEqual(rules.count("policy drop;"), 3)
        self.assertIn("hook postrouting", rules)
        for block in rules.split(" chain ")[1:]:
            self.assertLess(
                block.index("daddr @denied4"), block.index("ct state established")
            )
            self.assertLess(
                block.index("daddr @denied6"), block.index("ct state established")
            )

    def test_ansible_guard_refuses_prod_or_public_management(self):
        task = yaml.safe_load(
            (
                ROOT / "ansible/playbooks/roles/rehearsal_gates/tasks/main.yml"
            ).read_text()
        )[0]
        base = {
            "cms_environment": "test",
            "ansible_host": "100.91.0.10",
            "netbird_ip": "100.91.0.10",
            "netbird_subnet": "100.91.0.0/16",
            "rehearsal_deny_cidrs": ["198.51.100.7/32"],
            "rehearsal_test_addresses": ["100.91.0.10"],
            "rehearsal_controller_managed_tls": True,
        }
        inventory = {
            "all": {"children": {"isolated_test_vps": {"hosts": {"fixture": {}}}}}
        }
        plays = [
            dict(
                guard_play(
                    {**base, **override}, [task], None if not override else task["name"]
                ),
                hosts="fixture",
            )
            for override in [
                {},
                {"cms_environment": "production"},
                {"ansible_host": "203.0.113.9"},
                {"rehearsal_controller_managed_tls": False},
                {"netbird_subnet": "100.90.0.0/16"},
            ]
        ]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_controller_tls_mode_never_renders_dns_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = []
            names = {
                "webstack": "Deploy Cloudflare API Credentials File",
                "netbird_control_plane": "Prepare DNS challenge credentials for the NetBird certificate",
            }
            for role, name in names.items():
                tasks.append(
                    next(
                        t
                        for t in yaml.safe_load(
                            (
                                ROOT
                                / "ansible/playbooks/roles"
                                / role
                                / "tasks/main.yml"
                            ).read_text()
                        )
                        if t["name"] == name
                    )
                )
            result = run_plays(
                [
                    {
                        "variables": {
                            "app_directory": directory + "/app",
                            "netbird_control_plane_directory": directory + "/netbird",
                            "cloudflare_dns_api_token": "fixture-only",
                            "rehearsal_controller_managed_tls": True,
                        },
                        "tasks": tasks,
                    }
                ]
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(list(Path(directory).rglob("cloudflare.ini")))


class AlertDeliveryTest(unittest.TestCase):
    def test_threshold_recovery_and_failed_delivery_outbox(self):
        received = []
        respond = {"status": 503}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                event = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if respond["status"] == 204:
                    received.append(event)
                self.send_response(respond["status"])
                self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = {"alert_url": f"http://127.0.0.1:{server.server_port}/alerts"}
                update = MONITOR["update"]
                first = update(root, config, {"staff_app": {"ok": False}})
                self.assertEqual(first["pending_events"], [])
                second = update(root, config, {"staff_app": {"ok": False}})
                self.assertTrue(second["rules"]["staff_app"]["firing"])
                self.assertEqual(len(second["pending_events"]), 1)
                respond["status"] = 204
                third = update(root, config, {"staff_app": {"ok": False}})
                self.assertEqual(third["pending_events"], [])
                self.assertEqual([r["status"] for r in received], ["firing"])
                unreachable = update(root, config, {"host_reachable": {"ok": False}})
                self.assertEqual(
                    unreachable["rules"]["staff_app"], third["rules"]["staff_app"]
                )
                self.assertEqual([r["status"] for r in received], ["firing"])
                update(root, config, {"staff_app": {"ok": True}})
                self.assertEqual(
                    [r["status"] for r in received], ["firing", "resolved"]
                )
                self.assertEqual((root / "state.json").stat().st_mode & 0o777, 0o600)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
