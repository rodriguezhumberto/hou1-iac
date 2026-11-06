#!/usr/bin/env python3
"""Atomically maintain only the isolated test host's own nftables table."""

import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile

TABLE = "hou1_test_isolation"


def rules(config):
    assert config["environment"] == "test"
    networks = [ipaddress.ip_network(v, strict=True) for v in config["deny_cidrs"]]
    assert networks, "an explicit production destination set is required"
    local = [ipaddress.ip_network(v, strict=True) for v in config["allow_local_cidrs"]]
    updates = [
        ipaddress.ip_network(v, strict=True) for v in config["allow_update_cidrs"]
    ]
    assert all(n.prefixlen > 0 for n in local + updates), (
        "unrestricted egress is forbidden"
    )
    protected = [ipaddress.ip_address(v) for v in config["test_addresses"]]
    assert protected and not any(
        a in n for a in protected for n in networks if a.version == n.version
    ), "deny set overlaps test management/service addresses"
    lines = [f"table inet {TABLE} {{"]
    for version, kind in [(4, "ipv4_addr"), (6, "ipv6_addr")]:
        entries = ", ".join(str(n) for n in networks if n.version == version)
        assert entries, "both IPv4 and IPv6 deny sets are required"
        lines += [
            f" set denied{version} {{ type {kind}; flags interval; auto-merge; elements = {{ {entries} }}; }}"
        ]
    for name, members in [("local", local), ("updates", updates)]:
        for version, kind in [(4, "ipv4_addr"), (6, "ipv6_addr")]:
            entries = ", ".join(str(n) for n in members if n.version == version)
            assert entries, "both address families are required"
            lines += [
                f" set {name}{version} {{ type {kind}; flags interval; auto-merge; elements = {{ {entries} }}; }}"
            ]
    for chain, hook in [
        ("host_output", "output"),
        ("container_forward", "forward"),
        ("egress_guard", "postrouting"),
    ]:
        lines += [
            f" chain {chain} {{ type filter hook {hook} priority -20; policy drop;",
            f"  ip daddr @denied4 counter {'drop' if hook == 'postrouting' else 'reject with icmpx type admin-prohibited'}",
            f"  ip6 daddr @denied6 counter {'drop' if hook == 'postrouting' else 'reject with icmpx type admin-prohibited'}",
            "  ct state established,related accept",
            "  ip daddr @local4 accept",
            "  ip6 daddr @local6 accept",
            "  udp dport 53 accept",
            "  tcp dport 53 accept",
        ]
        if hook in ("output", "postrouting"):
            lines += [
                '  oifname "lo" accept',
                "  ip6 daddr { fe80::/10, ff02::/16 } meta l4proto ipv6-icmp accept",
                "  ip daddr @updates4 tcp dport { 80, 443 } accept",
                "  ip6 daddr @updates6 tcp dport { 80, 443 } accept",
                "  udp dport { 123, 3478 } accept",
                "  udp sport { 51820, 51821 } accept",
            ]
        lines += [
            "  counter "
            + (
                "drop"
                if hook == "postrouting"
                else "reject with icmpx type admin-prohibited"
            ),
            " }",
        ]
    return "\n".join(lines + ["}"]) + "\n"


def apply(config):
    exists = (
        subprocess.run(
            ["nft", "list", "table", "inet", TABLE], capture_output=True
        ).returncode
        == 0
    )
    batch = (f"delete table inet {TABLE}\n" if exists else "") + rules(config)
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="hou1-isolation-", delete=True
    ) as f:
        f.write(batch)
        f.flush()
        subprocess.run(
            ["nft", "--check", "--file", f.name], check=True, capture_output=True
        )
        subprocess.run(["nft", "--file", f.name], check=True, capture_output=True)
    print(
        json.dumps(
            {
                "environment": "test",
                "table": TABLE,
                "deny_entries": len(config["deny_cidrs"]),
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("config")
    p.add_argument("--render", action="store_true")
    a = p.parse_args()
    config = json.loads(Path(a.config).read_text())
    print(rules(config), end="") if a.render else apply(config)
