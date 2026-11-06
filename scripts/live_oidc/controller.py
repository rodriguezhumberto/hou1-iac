"""Controller adapter for the isolated VPS fixture; run from this directory.

Private paths and hostnames deliberately bind this drill to the approved VPS
fixture. Secrets are decrypted only into captured process input. Reports contain
no credentials. See docs/live-security-tests.md before invoking mutations.
"""

import json, subprocess, os, sys, uuid
from pathlib import Path

ROOT = Path(os.environ.get("HOU1_SECURITY_WORKDIR", "/var/tmp/hou1-vps-test/security"))
ROOT.mkdir(mode=0o700, exist_ok=True)
assert ROOT.stat().st_mode & 0o077 == 0
SSH = ["ssh", "-F", "/var/tmp/hou1-vps-test/ssh_config", "hou1-vps-test"]
AUTH = "https://auth-test.vps-test.pearlandpaws.com"
STAFF = "https://staff-test.vps-test.pearlandpaws.com"


def shell(command, data=None):
    r = subprocess.run(SSH + [command], input=data, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("remote command failed: " + r.stderr[-1200:])
    return r.stdout


def api(path, method="GET", body=None):
    a = {"path": path, "method": method}
    if body is not None:
        a["body"] = body
    raw = shell("sudo -n python3 /root/hou1-test-auth-api.py", json.dumps(a))
    return json.loads(raw)


def secrets():
    r = subprocess.run(
        [
            "sops",
            "--decrypt",
            "--output-type",
            "json",
            "/var/tmp/hou1-vps-test/secrets.enc.yaml",
        ],
        env={**os.environ, "SOPS_AGE_KEY_FILE": "/var/tmp/hou1-vps-test/test-age.key"},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout)


def probe(a):
    a = {"auth_url": AUTH, "staff_url": STAFF, **a}
    r = subprocess.run(
        [
            "ssh",
            "dev@192.168.0.151",
            "docker exec -i hou1-vps-admin-test python3 /tmp/live-oidc-probe.py",
        ],
        input=json.dumps(a),
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError("peer probe failed: " + r.stderr[-1600:])
    return json.loads(r.stdout)


def run(name, expect="accepted", mode="normal", fault=None, reader=False):
    key = "TEST_READER_PASSWORD" if reader else "TEST_ADMIN_PASSWORD"
    a = {
        "run_id": name + "-" + uuid.uuid4().hex[:8],
        "username": "vps-staff-reader" if reader else "vps-staff-admin",
        "password": secrets()[key],
        "mode": mode,
    }
    start = probe({**a, "action": "start"})
    if mode == "redirect_uri":
        return start
    codehash = start["code_sha256"]
    if fault:
        shell(
            "sudo -n tee /root/hou1-security-proxy/control.json >/dev/null",
            json.dumps({"code_sha256": codehash, "mode": fault}),
        )
    finish = probe({**a, "action": "finish", "expect": expect, "reader_write": reader})
    receipt = (
        json.loads(shell("sudo -n cat /root/hou1-security-proxy/" + codehash + ".json"))
        if mode not in ("state", "missing_cookie", "swapped_cookie")
        else None
    )
    if mode in ("state", "missing_cookie", "swapped_cookie"):
        assert (
            shell("sudo -n test ! -e /root/hou1-security-proxy/" + codehash + ".json")
            == ""
        )
    result = {"case": name, "assertion": "passed", "probe": finish, "receipt": receipt}
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    run("baseline")
