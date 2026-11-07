"""Security boundaries and durable alert transition/delivery behavior."""

import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import runpy
import tempfile
import threading
import unittest
from unittest.mock import patch
import yaml
from helpers import ROOT, guard_play, run_plays

ISOLATION = runpy.run_path(
    str(ROOT / "ansible/playbooks/roles/rehearsal_gates/files/isolation.py")
)
MONITOR = runpy.run_path(str(ROOT / "scripts/rehearsal-monitor.py"))


class RehearsalGateTest(unittest.TestCase):
    def test_cached_geolocation_requires_both_nonempty_databases(self):
        tasks = [
            {
                "ansible.builtin.import_tasks": str(
                    ROOT
                    / "ansible/playbooks/roles/netbird_control_plane/tasks/rehearsal-cache.yml"
                )
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            plays = []
            for label, entries, valid in [
                ("missing", {}, False),
                ("partial", {"GeoLite2-City_20261013.mmdb": b"db"}, False),
                (
                    "empty",
                    {"GeoLite2-City_20261013.mmdb": b"db", "geonames_20261013.db": b""},
                    False,
                ),
                (
                    "complete",
                    {
                        "GeoLite2-City_20261013.mmdb": b"db",
                        "geonames_20261013.db": b"db",
                    },
                    True,
                ),
            ]:
                root = Path(directory) / label
                (root / "data").mkdir(parents=True)
                for name, content in entries.items():
                    (root / "data" / name).write_bytes(content)
                plays.append(
                    guard_play(
                        {
                            "cms_environment": "test",
                            "rehearsal_controller_managed_tls": True,
                            "netbird_control_plane_directory": str(root),
                        },
                        tasks,
                        None
                        if valid
                        else "Require both nonempty geolocation databases before denying update traffic",
                    )
                )
            result = run_plays(plays)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_isolated_rehearsal_renders_cached_geolocation(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = [
                {
                    "ansible.builtin.copy": {
                        "dest": directory + "/{{ fixture_name }}-{{ item }}.yaml",
                        "content": "{{ lookup('template', '"
                        + str(
                            ROOT
                            / "ansible/playbooks/roles/netbird_control_plane/templates"
                        )
                        + "/' + item + '.yaml.j2') }}",
                    },
                    "loop": ["config", "config-authentik"],
                }
            ]
            plays = []
            for name, environment, flag in [
                ("test", "test", True),
                ("ordinary-test", "test", False),
                ("production", "prod", False),
            ]:
                plays.append(
                    {
                        "variables": {
                            "fixture_name": name,
                            "cms_environment": environment,
                            "rehearsal_controller_managed_tls": flag,
                            "netbird_domain": "netbird.example.org",
                            "netbird_authentik_issuer": "https://auth.example.org/application/o/netbird/",
                            "netbird_control_plane_secrets": {
                                k: "fixture-only"
                                for k in [
                                    "relay_auth_secret",
                                    "cookie_encryption_key",
                                    "store_encryption_key",
                                ]
                            },
                        },
                        "tasks": tasks,
                    }
                )
            result = run_plays(plays)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in ["test", "ordinary-test", "production"]:
                for template in ["config", "config-authentik"]:
                    config = yaml.safe_load(
                        (Path(directory) / f"{name}-{template}.yaml").read_text()
                    )
                    self.assertEqual(
                        config["server"].get("disableGeoliteUpdate"),
                        True if name == "test" else None,
                    )
                    self.assertNotIn("NB_DISABLE_GEOLOCATION", str(config))

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

    def google_config(self):
        return {
            **self.config(),
            "google_oauth": {
                "enabled": True,
                "container": "authentik-server-1",
                "project": "hou1-authentik-test",
                "network": "hou1-authentik-test",
            },
        }

    def fake_google_inspect(self):
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "check_returncode": lambda self: None,
                "stdout": json.dumps(
                    [
                        {
                            "Id": "test-container-id",
                            "State": {"Running": True},
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": "hou1-authentik-test",
                                    "com.docker.compose.service": "authentik-server",
                                }
                            },
                            "NetworkSettings": {
                                "Networks": {
                                    "hou1-authentik-test": {
                                        "IPAddress": "172.18.0.3",
                                        "GlobalIPv6Address": "",
                                    }
                                }
                            },
                        }
                    ]
                )
            },
        )()

    def test_google_destinations_are_bound_to_running_authentik(self):
        with patch.object(
            ISOLATION["subprocess"], "run", return_value=self.fake_google_inspect()
        ), patch.object(
            ISOLATION["socket"],
            "getaddrinfo",
            return_value=[
                (ISOLATION["socket"].AF_INET, 0, 0, "", ("8.8.8.8", 443)),
                (
                    ISOLATION["socket"].AF_INET6,
                    0,
                    0,
                    "",
                    ("2001:4860:4860::8888", 443, 0, 0),
                ),
            ],
        ):
            entries, hosts, container = ISOLATION["google_targets"](self.google_config())
        self.assertEqual(entries[4], ["172.18.0.3 . 8.8.8.8"])
        self.assertEqual(entries[6], [])
        self.assertEqual(hosts, {host: ["8.8.8.8"] for host in ISOLATION["GOOGLE_HOSTS"]})
        self.assertEqual(container, "test-container-id")
        batch = ISOLATION["google_batch"](entries)
        self.assertIn("172.18.0.3 . 8.8.8.8 timeout 300s", batch)

    def test_google_pins_refresh_and_disable_preserve_other_hostnames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosts"
            original = "127.0.0.1 localhost\n172.18.0.3 existing-alias\n"
            path.write_text(original)
            for addresses in [["8.8.8.8"], ["8.8.4.4"], []]:
                hosts = {"accounts.google.com": addresses} if addresses else {}
                with patch("pathlib.Path", return_value=path), patch(
                    "sys.stdin", io.StringIO(json.dumps(hosts))
                ):
                    exec(ISOLATION["GOOGLE_HOSTS_UPDATE"], {})
                self.assertTrue(path.read_text().endswith(original))
                self.assertEqual(path.read_text().count("accounts.google.com"), len(addresses))
                if addresses:
                    self.assertIn(addresses[0] + " accounts.google.com", path.read_text())
            self.assertEqual(path.read_text(), original)

    def test_google_refresh_rejects_wrong_workloads_and_unsafe_dns(self):
        for label, overrides, address, error in [
            ("wrong-project", {"project": "production"}, "8.8.8.8", AssertionError),
            ("private-dns", {}, "10.0.0.1", AssertionError),
            ("denied-dns", {}, "8.8.8.8", AssertionError),
            ("stopped", {}, "8.8.8.8", AssertionError),
            ("wrong-service", {}, "8.8.8.8", AssertionError),
        ]:
            config = self.google_config()
            config["google_oauth"].update(overrides)
            inspect = self.fake_google_inspect()
            data = json.loads(inspect.stdout)
            if label == "denied-dns":
                config["deny_cidrs"].append("8.8.8.8/32")
            elif label == "stopped":
                data[0]["State"]["Running"] = False
            elif label == "wrong-service":
                data[0]["Config"]["Labels"]["com.docker.compose.service"] = "authentik-worker"
            inspect.stdout = json.dumps(data)
            with self.subTest(label=label), patch.object(
                ISOLATION["subprocess"], "run", return_value=inspect
            ), patch.object(
                ISOLATION["socket"], "getaddrinfo",
                return_value=[(2, 0, 0, "", (address, 443))],
            ), self.assertRaises(error):
                ISOLATION["google_targets"](config)

    def test_google_disable_skips_dns_and_returns_only_pin_cleanup(self):
        config = self.google_config()
        config["google_oauth"]["enabled"] = False
        with patch.object(
            ISOLATION["subprocess"], "run", return_value=self.fake_google_inspect()
        ), patch.object(ISOLATION["socket"], "getaddrinfo") as dns:
            entries, hosts, container = ISOLATION["google_targets"](config)
        dns.assert_not_called()
        self.assertEqual(entries, {4: [], 6: []})
        self.assertEqual(hosts, {})
        self.assertEqual(container, "test-container-id")

    def test_google_refresh_fails_closed_on_dns_error(self):
        with patch.object(
            ISOLATION["subprocess"], "run", return_value=self.fake_google_inspect()
        ), patch.object(
            ISOLATION["socket"], "getaddrinfo", side_effect=OSError("DNS unavailable")
        ), self.assertRaises(OSError):
            ISOLATION["google_targets"](self.google_config())

    def test_google_rules_are_https_only_and_separate_from_host_updates(self):
        rules = ISOLATION["rules"](self.config())
        self.assertEqual(rules.count("ip saddr . ip daddr @google4 tcp dport 443 counter accept"), 2)
        self.assertEqual(rules.count("ip6 saddr . ip6 daddr @google6 tcp dport 443 counter accept"), 2)
        self.assertIn("set google4", rules)
        self.assertIn("set google6", rules)

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
