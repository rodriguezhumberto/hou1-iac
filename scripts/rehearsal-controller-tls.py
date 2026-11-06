#!/usr/bin/env python3
"""Renew only isolated test certificates on the trusted controller, never ship DNS credentials."""

import argparse
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


def run(argv, data=None):
    return subprocess.run(argv, input=data, capture_output=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--renew", action="store_true")
    a = p.parse_args()
    config_path = Path(a.config)
    c = json.loads(config_path.read_text())
    root = config_path.parent
    assert c["environment"] == "test" and root.stat().st_mode & 0o077 == 0
    ssh = c["ssh"]
    remote = c["certificate_directory"]
    assert remote == "/opt/pearlandpaws-test/certbot/conf"
    original = run(ssh + ["sudo -n tar -czf - -C " + remote + " ."])
    assert original.returncode == 0
    token = json.loads(
        subprocess.run(
            c["secrets_command"],
            env={**os.environ, **c["secrets_env"]},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )["CLOUDFLARE_DNS_API_TOKEN"]
    with tempfile.TemporaryDirectory(prefix="tls-", dir=root) as directory:
        work = Path(directory)
        certs = work / "conf"
        certs.mkdir()
        with tarfile.open(fileobj=io.BytesIO(original.stdout), mode="r:gz") as tar:
            tar.extractall(certs, filter="data")
        renewals = list((certs / "renewal").glob("*.conf"))
        assert len(renewals) == 4
        for renewal in renewals:
            assert renewal.stem.endswith(".vps-test.pearlandpaws.com")
            for line in renewal.read_text().splitlines():
                if line.startswith("domains ="):
                    assert all(
                        name.strip().endswith(".vps-test.pearlandpaws.com")
                        for name in line.split("=", 1)[1].split(",")
                    )
        credential = work / "cloudflare.ini"
        credential.write_text("dns_cloudflare_api_token = " + token + "\n")
        credential.chmod(0o600)
        for name in ("work", "logs"):
            (work / name).mkdir()
        cmd = [
            "docker",
            "run",
            "--rm",
            "--user",
            str(os.getuid()) + ":" + str(os.getgid()),
            "-v",
            str(certs) + ":/etc/letsencrypt",
            "-v",
            str(credential) + ":/cloudflare.ini:ro",
            "-v",
            str(work / "work") + ":/work",
            "-v",
            str(work / "logs") + ":/logs",
            "certbot/dns-cloudflare:v5.1.0",
            "renew",
            "--non-interactive",
            "--no-random-sleep-on-renew",
            "--work-dir",
            "/work",
            "--logs-dir",
            "/logs",
            "--dns-cloudflare-credentials",
            "/cloudflare.ini",
            "--dns-cloudflare-propagation-seconds",
            "120",
        ]
        if not a.renew:
            cmd += ["--dry-run"]
        r = run(cmd)
        (root / ("tls-renew.log" if a.renew else "tls-dry-run.log")).write_bytes(
            (r.stdout + r.stderr).replace(token.encode(), b"[redacted]")
        )
        assert r.returncode == 0, (
            "controller Certbot failed; inspect private redacted log"
        )
        if a.renew:
            # Backup is the previously fetched issued test state; dry-run staging state is never installed.
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w:gz") as tar:
                tar.add(certs, arcname=".")
            r = run(
                ssh + ["sudo -n tar -xzf - --no-same-owner -C " + remote],
                payload.getvalue(),
            )
            try:
                assert r.returncode == 0
                r = run(
                    ssh + ["sudo -n docker exec pearlandpaws-test-nginx-1 nginx -t"]
                )
                assert r.returncode == 0
                r = run(
                    ssh
                    + ["sudo -n docker exec pearlandpaws-test-nginx-1 nginx -s reload"]
                )
                assert r.returncode == 0
            except BaseException:
                run(
                    ssh + ["sudo -n tar -xzf - --no-same-owner -C " + remote],
                    original.stdout,
                )
                run(
                    ssh
                    + ["sudo -n docker exec pearlandpaws-test-nginx-1 nginx -s reload"]
                )
                raise
            receipt = {
                "environment": "test",
                "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "certificate_lineages": len(renewals),
                "dns_credentials_shipped": False,
            }
            target = root / "tls-renew-success.json"
            target.write_text(json.dumps(receipt))
            target.chmod(0o600)
        print(
            json.dumps(
                {
                    "environment": "test",
                    "mode": "renew" if a.renew else "dry-run",
                    "certificate_lineages": len(renewals),
                    "passed": True,
                    "dns_credentials_shipped": False,
                }
            )
        )


if __name__ == "__main__":
    main()
