#!/usr/bin/env python3
"""Read-only test-host health probe. Emits checks, never Docker environment/logs."""

import datetime
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.request


def command(argv, timeout=20):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def collect(c):
    result = {}

    def check(name, fn):
        try:
            value = fn()
            result[name] = {"ok": bool(value)}
        except Exception:
            result[name] = {"ok": False}

    def state(name):
        r = command(["docker", "inspect", name])
        assert r.returncode == 0
        data = json.loads(r.stdout)[0]
        return data

    for label, container in c["containers"].items():
        check(
            label,
            lambda name=container: (
                lambda s: (
                    s["Status"] == "running"
                    and s.get("Health", {}).get("Status", "healthy") == "healthy"
                )
            )(state(name)["State"]),
        )

    def pg():
        return (
            command(
                [
                    "docker",
                    "exec",
                    c["containers"]["postgresql"],
                    "pg_isready",
                    "-U",
                    "authentik",
                    "-d",
                    "authentik",
                ]
            ).returncode
            == 0
        )

    check("postgresql_query", pg)

    def ready():
        info = state(c["containers"]["authentik_server"])
        address = info["NetworkSettings"]["Networks"][c["authentik_network"]][
            "IPAddress"
        ]
        with urllib.request.urlopen(
            "http://" + address + ":9000/-/health/ready/", timeout=8
        ) as r:
            return r.status == 200

    check("authentik_ready", ready)

    def https(host, path, expected):
        r = command(
            [
                "curl",
                "--interface",
                c["server_peer_ip"],
                "--silent",
                "--show-error",
                "--noproxy",
                "*",
                "--max-time",
                "10",
                "--resolve",
                host + ":443:" + c["server_peer_ip"],
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "https://" + host + path,
            ]
        )
        return r.returncode == 0 and r.stdout == str(expected)

    for label, host, path, expected in c["http_checks"]:
        check(label, lambda h=host, p=path, e=expected: https(h, p, e))

    def certificate(host):
        ctx = ssl.create_default_context()
        with socket.create_connection(("127.0.0.1", 443), timeout=8) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                expires = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
        return (
            expires - datetime.datetime.now(datetime.timezone.utc).timestamp()
            > c["certificate_warning_days"] * 86400
        )

    for host in c["tls_hosts"]:
        check("tls_" + host.split(".")[0], lambda h=host: certificate(h))
    mem = {
        k: int(v.split()[0])
        for k, v in (
            line.split(":", 1)
            for line in Path("/proc/meminfo").read_text().splitlines()
        )
    }
    check("memory_headroom", lambda: 1 - mem["MemAvailable"] / mem["MemTotal"] < 0.80)
    disk = shutil.disk_usage("/var/lib")
    check("disk_headroom", lambda: disk.used / disk.total < 0.75)
    import os

    fs = os.statvfs("/var/lib")
    check("inode_headroom", lambda: 1 - fs.f_favail / fs.f_files < 0.70)

    def io():
        r = command(["iostat", "-dx", "-o", "JSON", "1", "2"])
        stats = json.loads(r.stdout)["sysstat"]["hosts"][0]["statistics"][-1]["disk"]
        return bool(stats) and all(d.get("util", d.get("%util", 0)) < 70 for d in stats)

    check("io_headroom", io)

    def backup():
        markers = list(Path(c["backup_directory"]).glob("authentik-*.success"))
        return (
            bool(markers)
            and datetime.datetime.now(datetime.timezone.utc).timestamp()
            - max(p.stat().st_mtime for p in markers)
            < c["backup_max_age_hours"] * 3600
        )

    check("authentik_backup_fresh", backup)

    def isolation():
        r = command(["nft", "--json", "list", "table", "inet", "hou1_test_isolation"])
        assert r.returncode == 0
        items = json.loads(r.stdout)["nftables"]
        chains = {i["chain"]["name"] for i in items if "chain" in i}
        expected = {"host_output", "container_forward", "egress_guard"}
        policies = {
            i["chain"]["name"]: i["chain"].get("policy") for i in items if "chain" in i
        }
        guard = [
            i["rule"]
            for i in items
            if "rule" in i and i["rule"]["chain"] == "egress_guard"
        ]
        return (
            expected <= chains
            and all(policies.get(k) == "drop" for k in expected)
            and len(guard) >= 2
            and "denied4" in json.dumps(guard[0])
            and "denied6" in json.dumps(guard[1])
        )

    def secret_boundary():
        import os

        if any(Path(p).exists() for p in c["forbidden_dns_files"]):
            return False
        public = command(["age-keygen", "-y", c["age_identity"]])
        if public.returncode or public.stdout.strip() != c["host_age_recipient"]:
            return False
        r = subprocess.run(
            ["sops", "--decrypt", "--output-type", "json", c["host_ciphertext"]],
            env={**os.environ, "SOPS_AGE_KEY_FILE": c["age_identity"]},
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0 and "CLOUDFLARE_DNS_API_TOKEN" not in json.loads(
            r.stdout
        )

    check("secret_boundary", secret_boundary)
    check("isolation_loaded", isolation)
    return {
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": "test",
        "checks": result,
    }


if __name__ == "__main__":
    print(
        json.dumps(collect(json.loads(Path(sys.argv[1]).read_text())), sort_keys=True)
    )
