#!/usr/bin/env python3
"""Live isolated test component outages, pre-armed recovery and alert/recovery assertions."""

import argparse
import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import time
import urllib.request

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--config", required=True)
p.add_argument(
    "--components",
    nargs="+",
    default=["authentik", "postgresql", "staff", "nginx", "netbird"],
)
a = p.parse_args()
config_path = Path(a.config)
config = json.loads(config_path.read_text())
assert config["environment"] == "test"
root = config_path.parent
spec = importlib.util.spec_from_file_location(
    "monitor", Path(__file__).with_name("rehearsal-monitor.py")
)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
components = {
    "authentik": ("hou1-authentik-test-authentik-server-1", "authentik_server"),
    "postgresql": ("hou1-authentik-test-postgresql-1", "postgresql"),
    "staff": ("pearlandpaws-test-astro-app-1", "staff_app"),
    "nginx": ("pearlandpaws-test-nginx-1", "public_nginx"),
    "netbird": ("hou1-netbird-test-netbird-server-1", "public_netbird_api"),
}
report = (
    json.loads((root / "alert-drill-report.json").read_text())
    if (root / "alert-drill-report.json").exists()
    else []
)


def remote(command, timeout=25):
    return subprocess.run(
        config["ssh"] + [command], capture_output=True, text=True, timeout=timeout
    )


def tick():
    state = monitor.update(root, config, monitor.probe(config))
    assert not state["pending_events"], "test alert transport failed"
    return state


def events_since(start, rule, status):
    lines = (
        (root / "alerts.jsonl").read_text().splitlines()
        if (root / "alerts.jsonl").exists()
        else []
    )
    return [
        json.loads(l)
        for l in lines[start:]
        if json.loads(l)["rule"] == rule and json.loads(l)["status"] == status
    ]


for label in a.components:
    baseline = tick()
    assert all(v["ok"] for v in baseline["rules"].values()), (
        "restore a healthy baseline before the next outage"
    )
    assert label in components
    container, rule = components[label]
    unit = "hou1-test-drill-recover-" + label
    stop = "hou1-test-drill-stop-" + label
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    offset = (
        len((root / "alerts.jsonl").read_text().splitlines())
        if (root / "alerts.jsonl").exists()
        else 0
    )
    # The recovery job is local to the VPS and survives loss of the combined relay/ingress.
    r = remote(
        "sudo -n systemd-run --collect --unit="
        + unit
        + " --on-active=150s /usr/bin/docker start "
        + container
    )
    assert r.returncode == 0
    try:
        r = remote(
            "sudo -n systemd-run --collect --unit="
            + stop
            + " --on-active=2s /usr/bin/docker stop --time=5 "
            + container
        )
        assert r.returncode == 0
        time.sleep(8)
        for sample_count in range(1, 5):
            state = tick()
            if state["rules"].get(rule, {}).get("firing"):
                break
        assert state["rules"][rule]["firing"], label + " failed to fire expected alert"
        assert events_since(offset, rule, "firing"), (
            label + " firing event not received off-host"
        )
        print(json.dumps({"component": label, "firing_received": True}), flush=True)
    finally:
        # Immediate recovery when management survives; the armed timer is the fallback.
        try:
            remote("sudo -n docker start " + container, timeout=15)
        except subprocess.TimeoutExpired:
            pass
        recovered = False
        recovery_deadline = time.monotonic() + 240
        while time.monotonic() < recovery_deadline:
            try:
                r = remote(
                    "sudo -n docker inspect "
                    + container
                    + ' --format "{{.State.Status}}"',
                    timeout=12,
                )
                if r.returncode == 0 and r.stdout.strip() == "running":
                    recovered = True
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(2)
        assert recovered, (
            "local recovery timer did not restore management; use provider console"
        )
        # Container health/startup can lag behind its running state.
        for _ in range(12):
            state = tick()
            if state["rules"].get(rule, {}).get("ok") and state["rules"].get(
                "staff_oidc_login", {}
            ).get("ok"):
                break
            time.sleep(2)
        assert events_since(offset, rule, "resolved"), (
            label + " recovery event not received off-host"
        )
        remote("sudo -n systemctl stop " + unit + ".timer")
        print(json.dumps({"component": label, "resolved_received": True}), flush=True)
    report.append(
        {
            "component": label,
            "rule": rule,
            "started_at": started,
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "firing_received": True,
            "resolved_received": True,
            "recovery_prearmed_seconds": 150,
            "failure_probe_samples": sample_count,
            "cadence": "consecutive real probes after shutdown; timer cadence tested separately",
        }
    )
    (root / "alert-drill-report.json").write_text(json.dumps(report, indent=2))
