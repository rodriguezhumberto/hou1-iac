#!/usr/bin/env python3
"""Separate only the approved VPS fixture's age identity and application secrets.

The controller retains the shared DNS capability. The test host receives only
filtered ciphertext and a different identity. Original controller recovery keys
remain private. This command is specific to the approved private test inventory.
"""

import argparse, json, os, subprocess, yaml, shlex
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--controller-dir", required=True)
args = parser.parse_args()
root = Path(args.controller_dir).resolve()
assert root.stat().st_mode & 0o077 == 0
inventory = yaml.safe_load((root / "management.yml").read_text())
target = inventory["all"]["children"]["isolated_test_vps"]["hosts"]["rehearsal_vps"]
assert (
    target["cms_environment"] == "test"
    and target["ansible_host"] == "100.91.0.10"
    and target["primary_domain"] == "vps-test.pearlandpaws.com"
)
gate = root / "gates"
gate.mkdir(mode=0o700, exist_ok=True)
key = gate / "host-age.key"
if not key.exists():
    subprocess.run(["age-keygen", "-o", str(key)], capture_output=True, check=True)
key.chmod(0o600)
recipient = subprocess.run(
    ["age-keygen", "-y", str(key)], capture_output=True, text=True, check=True
).stdout.strip()
secret = json.loads(
    subprocess.run(
        ["sops", "--decrypt", "--output-type", "json", str(root / "secrets.enc.yaml")],
        env={**os.environ, "SOPS_AGE_KEY_FILE": str(root / "test-age.key")},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
)
assert all(
    k in secret
    for k in ("TEST_ADMIN_PASSWORD", "TEST_READER_PASSWORD", "CLOUDFLARE_DNS_API_TOKEN")
)
controller_recipient = subprocess.run(
    ["age-keygen", "-y", str(root / "test-age.key")],
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
assert recipient != controller_recipient, (
    "host must not receive the controller identity"
)
filtered = {k: v for k, v in secret.items() if k != "CLOUDFLARE_DNS_API_TOKEN"}
r = subprocess.run(
    [
        "sops",
        "--encrypt",
        "--config",
        "/dev/null",
        "--age",
        recipient,
        "--input-type",
        "json",
        "--output-type",
        "yaml",
        "/dev/stdin",
    ],
    input=json.dumps(filtered),
    capture_output=True,
    text=True,
    check=True,
)
cipher = gate / "authentik-host.enc.yaml"
cipher.write_text(r.stdout)
cipher.chmod(0o600)
ssh = ["ssh", "-F", str(root / "ssh_config"), "hou1-vps-test"]
projects = subprocess.run(
    ssh
    + [
        "sudo -n docker ps --format "
        + __import__("shlex").quote('{{.Label "com.docker.compose.project"}}')
    ],
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines()
assert set(filter(None, projects)) == {
    "pearlandpaws-test",
    "hou1-authentik-test",
    "hou1-netbird-test",
}, "live VPS is not the approved isolated stack"
# Exact-value audit prints paths only; never sends the shared token back to logs.
audit = """sudo -n python3 -c 'import sys,pathlib,json; value=sys.stdin.buffer.read(); found=[]
for prefix in ["/opt","/root","/var/lib/hou1-authentik","/var/backups","/mnt/offhost-backups"]:
 for p in pathlib.Path(prefix).rglob("*"):
  try:
   if p.is_file() and p.stat().st_size<10000000 and value in p.read_bytes():found.append(str(p))
  except (OSError,PermissionError):pass
print(json.dumps(found))' """
res = subprocess.run(
    ssh + [audit],
    input=secret["CLOUDFLARE_DNS_API_TOKEN"],
    capture_output=True,
    text=True,
    check=True,
)
locations = json.loads(res.stdout)
(gate / "dns-token-locations-before.json").write_text(json.dumps(locations, indent=2))
assert all(p.endswith("/cloudflare.ini") for p in locations), (
    "unexpected plaintext credential location: inspect private paths before rotation"
)
# New host identity cannot decrypt the controller bundle or old credential-bearing archives.
for path, data in [
    ("/opt/hou1-authentik/test/secrets.enc.yaml", cipher.read_text()),
    ("/root/.config/sops/age/vps-test.key", key.read_text()),
]:
    subprocess.run(
        ssh + ["sudo -n tee " + path + " >/dev/null"],
        input=data,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(ssh + ["sudo -n chmod 600 " + path], capture_output=True, check=True)
# Remove plaintext DNS capability; retain issued test certificates.
for path in locations:
    subprocess.run(
        ssh
        + [
            "sudo -n python3 -c 'from pathlib import Path; Path(\""
            + path
            + "\").unlink()'"
        ],
        capture_output=True,
        check=True,
    )
# Replace the obsolete host renewal job with controller-only renewal. No broken cron remains.
cleanup_script = """
from pathlib import Path
import subprocess
p=subprocess.run(['crontab','-l'],capture_output=True,text=True,check=True)
lines=[line for line in p.stdout.splitlines() if 'renew_ssl.sh' not in line and not line.startswith('#Ansible: Renew')]
subprocess.run(['crontab','-'],input='\\n'.join(lines)+'\\n',text=True,check=True)
p=Path('/opt/pearlandpaws-test/certbot/renew_ssl.sh')
p.write_text('#!/bin/sh\\necho DNS renewal is managed by the trusted controller >&2\\nexit 2\\n')
"""
subprocess.run(
    ssh + ["sudo -n python3 -"],
    input=cleanup_script,
    capture_output=True,
    text=True,
    check=True,
)
# Verify new SOPS works and shared Cloudflare capability is absent, with no secret output.
verify = """sudo -n python3 -c 'import json,subprocess,os; s=json.loads(subprocess.run(["sops","--decrypt","--output-type","json","/opt/hou1-authentik/test/secrets.enc.yaml"],env={**os.environ,"SOPS_AGE_KEY_FILE":"/root/.config/sops/age/vps-test.key"},capture_output=True,text=True,check=True).stdout); assert "CLOUDFLARE_DNS_API_TOKEN" not in s; print("host SOPS contains only isolated application secrets")' """
print(
    subprocess.run(
        ssh + [verify], capture_output=True, text=True, check=True
    ).stdout.strip()
)
p = root / "management.yml"
inventory = yaml.safe_load(p.read_text())
h = inventory["all"]["children"]["isolated_test_vps"]["hosts"]["rehearsal_vps"]
h.update(
    authentik_secret_source=str(cipher),
    rehearsal_host_age_key_source=str(key),
    authentik_backup_age_recipient=recipient,
    netbird_backup_age_recipient=recipient,
)
p.write_text(yaml.safe_dump(inventory, sort_keys=False))
p.chmod(0o600)
(gate / "key-rotation-state.json").write_text(
    json.dumps(
        {
            "new_recipient": recipient,
            "removed_plaintext_dns_files": len(locations),
            "host_identity_changed": True,
            "shared_dns_credential_absent": True,
        },
        indent=2,
    )
)
print(
    "test host age identity rotated; DNS credentials remain only on trusted controller"
)
