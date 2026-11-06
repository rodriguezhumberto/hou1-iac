#!/usr/bin/env python3
"""Off-host isolated test monitor and private rehearsal alert receiver.

Config and credentials remain in a mode-0700 controller directory. Check output
is limited to stable rule IDs, booleans and timestamps. Alerts are sent only to
the explicitly configured loopback test receiver, never to people or production.
"""

import argparse
import datetime
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import ipaddress
import socket
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as f:
        os.chmod(f.name, 0o600)
        json.dump(data, f)
        f.write("\n")
    os.replace(f.name, path)


def receiver(root, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/alerts":
                self.send_error(404)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size < 65536:
                self.send_error(413)
                return
            event = json.loads(self.rfile.read(size))
            if event.get("environment") != "test" or event.get("status") not in (
                "firing",
                "resolved",
            ):
                self.send_error(400)
                return
            event["received_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            fd = os.open(
                root / "alerts.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
            )
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")
            self.send_response(204)
            self.end_headers()

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def run_command(argv, data=None, timeout=90, env=None):
    return subprocess.run(
        argv, input=data, capture_output=True, text=True, timeout=timeout, env=env
    )


def probe(config):
    checks = {}
    if config.get("production_dns_names"):
        covered = True
        networks = [
            ipaddress.ip_network(value) for value in config["isolation_deny_cidrs"]
        ]
        for hostname in config["production_dns_names"]:
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(
                        hostname, 443, type=socket.SOCK_STREAM
                    )
                }
                covered = (
                    covered
                    and bool(addresses)
                    and all(
                        any(
                            address in network
                            for network in networks
                            if address.version == network.version
                        )
                        for address in addresses
                    )
                )
            except socket.gaierror:
                covered = covered and hostname in config.get(
                    "unresolved_reserved_dns_names", []
                )
        checks["isolation_dns_coverage"] = {"ok": covered}
    for name, url in config.get("public_checks", {}).items():
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                checks[name] = {"ok": r.status == 200}
        except Exception:
            checks[name] = {"ok": False}
    try:
        r = run_command(
            config["ssh"]
            + [
                "sudo -n /usr/local/sbin/hou1-test-probe /etc/hou1-test-gates/probe.json"
            ],
            timeout=60,
        )
        assert r.returncode == 0
        remote = json.loads(r.stdout)
        assert remote["environment"] == "test"
        checks.update(remote["checks"])
        checks["host_reachable"] = {"ok": True}
    except Exception:
        checks["host_reachable"] = {"ok": False}
    try:
        r = run_command(
            [
                "ssh",
                "dev@192.168.0.151",
                "docker exec hou1-vps-admin-test netbird status --json",
            ],
            timeout=25,
        )
        st = json.loads(r.stdout)
        checks["vpn_management"] = {"ok": st["management"]["connected"]}
        checks["vpn_signal"] = {"ok": st["signal"]["connected"]}
        checks["vpn_peer"] = {
            "ok": any(
                p["netbirdIp"] == config["server_peer_ip"]
                and p["status"] == "Connected"
                for p in (st["peers"].get("details") or [])
            )
        }
    except Exception:
        for name in ("vpn_management", "vpn_signal", "vpn_peer"):
            checks[name] = {"ok": False}
    try:
        r = run_command(
            config["secrets_command"],
            env={**os.environ, **config["secrets_env"]},
            timeout=15,
        )
        assert r.returncode == 0
        values = json.loads(r.stdout)
        payload = json.dumps(
            {
                key: values[key]
                for key in ("TEST_ADMIN_PASSWORD", "TEST_READER_PASSWORD")
            }
        )
        r = run_command(config["staff_probe_command"], data=payload, timeout=90)
        checks["staff_oidc_login"] = {"ok": r.returncode == 0}
    except Exception:
        checks["staff_oidc_login"] = {"ok": False}
    if config.get("monitor_controller_tls"):
        try:
            stamp = json.loads(
                (Path(config["state_directory"]) / "tls-renew-success.json").read_text()
            )
            age = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.fromisoformat(stamp["recorded_at"])
            ).total_seconds()
            unit = run_command(
                [
                    "systemctl",
                    "--user",
                    "show",
                    "hou1-test-tls.service",
                    "-p",
                    "Result",
                    "--value",
                ],
                timeout=5,
            )
            checks["controller_tls_renewal"] = {
                "ok": stamp["environment"] == "test"
                and age < 26 * 3600
                and unit.returncode == 0
                and unit.stdout.strip() == "success"
            }
        except Exception:
            checks["controller_tls_renewal"] = {"ok": False}
    return checks


def update(root, config, checks):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state_path = root / "state.json"
    previous = (
        json.loads(state_path.read_text()) if state_path.exists() else {"rules": {}}
    )
    # A lost SSH path omits host checks. Keep their last sampled state so an
    # active alert survives the gap and can resolve when the host returns.
    # host_reachable independently reports the missing management path.
    state = {
        "environment": "test",
        "recorded_at": now,
        "rules": dict(previous["rules"]),
    }
    events = []
    for name, check in checks.items():
        old = previous["rules"].get(name, {"failures": 0, "firing": False})
        failures = 0 if check["ok"] else old["failures"] + 1
        # Staff login uses the runbook's three-failure threshold; other rules two.
        firing = failures >= config.get("thresholds", {}).get(name, 2)
        entry = {"ok": check["ok"], "failures": failures, "firing": firing}
        if firing != old["firing"]:
            events.append(
                {
                    "environment": "test",
                    "rule": name,
                    "status": "firing" if firing else "resolved",
                    "observed_at": now,
                }
            )
        state["rules"][name] = entry
    # Preserve a pending outbox across delivery failures; do not silently lose transitions.
    pending = previous.get("pending_events", []) + events
    delivered = []
    for event in pending:
        try:
            req = urllib.request.Request(
                config["alert_url"],
                data=json.dumps(event).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                assert r.status == 204
            delivered.append(event)
        except Exception:
            break
    state["pending_events"] = pending[len(delivered) :]
    atomic(state_path, state)
    print(
        json.dumps(
            {
                "environment": "test",
                "recorded_at": now,
                "failed_rules": [k for k, v in checks.items() if not v["ok"]],
                "delivered_events": len(delivered),
                "pending_events": len(state["pending_events"]),
            },
            sort_keys=True,
        )
    )
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--receive", action="store_true")
    a = p.parse_args()
    config_path = Path(a.config)
    config = json.loads(config_path.read_text())
    root = config_path.parent
    assert root.stat().st_mode & 0o077 == 0 and config["environment"] == "test"
    url = urllib.parse.urlsplit(config["alert_url"])
    assert (
        url.scheme == "http" and url.hostname == "127.0.0.1" and url.path == "/alerts"
    ), "this rehearsal receiver is loopback only"
    if a.receive:
        receiver(root, url.port)
        return
    with (root / "monitor.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        update(root, config, probe(config))


if __name__ == "__main__":
    main()
