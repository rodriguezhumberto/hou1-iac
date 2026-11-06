#!/usr/bin/env python3
"""Prove a broken test OIDC client alerts despite healthy app and IdP containers."""

import argparse
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--config", required=True)
a = p.parse_args()
path = Path(a.config)
c = json.loads(path.read_text())
assert c["environment"] == "test"
root = path.parent
spec = importlib.util.spec_from_file_location(
    "monitor", Path(__file__).with_name("rehearsal-monitor.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def remote(command, data=None):
    return subprocess.run(
        c["ssh"] + [command], input=data, capture_output=True, text=True, check=True
    )


def api(method, body=None):
    request = {"path": "providers/oauth2/1/", "method": method}
    if body is not None:
        request["body"] = body
    return json.loads(
        remote(
            "sudo -n python3 /root/hou1-test-auth-api.py", json.dumps(request)
        ).stdout
    )


original = api("GET")["client_id"]
assert original == "pearland-paws-staff-test"
restore = """import json,subprocess
request={'path':'providers/oauth2/1/','method':'PATCH','body':{'client_id':'pearland-paws-staff-test'}}
r=subprocess.run(['python3','/root/hou1-test-auth-api.py'],input=json.dumps(request),capture_output=True,text=True)
assert r.returncode==0,'test client recovery failed'
"""
remote("sudo -n tee /root/hou1-test-restore-client.py >/dev/null", restore)
unit = "hou1-test-drill-client-recovery"
remote(
    "sudo -n systemd-run --collect --unit="
    + unit
    + " --on-active=150s /usr/bin/python3 /root/hou1-test-restore-client.py"
)
offset = (
    len((root / "alerts.jsonl").read_text().splitlines())
    if (root / "alerts.jsonl").exists()
    else 0
)
try:
    api("PATCH", {"client_id": "pearland-paws-staff-test-disabled"})
    for _ in range(3):
        checks = m.probe(c)
        assert (
            checks["authentik_ready"]["ok"]
            and checks["staff_app"]["ok"]
            and checks["nginx"]["ok"]
        )
        assert not checks["staff_oidc_login"]["ok"]
        state = m.update(root, c, checks)
    assert state["rules"]["staff_oidc_login"]["firing"]
finally:
    api("PATCH", {"client_id": original})
    remote("sudo -n systemctl stop " + unit + ".timer")
    remote(
        "sudo -n python3 -c "
        + shlex.quote(
            "from pathlib import Path; Path('/root/hou1-test-restore-client.py').unlink()"
        )
    )
state = m.update(root, c, m.probe(c))
assert state["rules"]["staff_oidc_login"]["ok"] and not state["pending_events"]
events = [
    json.loads(l) for l in (root / "alerts.jsonl").read_text().splitlines()[offset:]
]
assert all(
    any(e["rule"] == "staff_oidc_login" and e["status"] == status for e in events)
    for status in ("firing", "resolved")
)
report = {
    "environment": "test",
    "staff_login_failure_with_healthy_services": True,
    "three_failure_threshold": True,
    "firing_received_offhost": True,
    "resolved_received_offhost": True,
    "client_restored": api("GET")["client_id"] == original,
}
(root / "staff-login-alert-report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
