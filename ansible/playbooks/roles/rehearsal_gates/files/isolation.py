#!/usr/bin/env python3
"""Atomically maintain only the isolated test host's own nftables table."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile

TABLE = "hou1_test_isolation"


# Google source discovery, token exchange, userinfo and signing keys. These
# names are deliberately fixed; this is not a general workload egress input.
GOOGLE_HOSTS = ("accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com")
GOOGLE_TTL_SECONDS = 300

# Fixed host package repositories only; never arbitrary workload destinations.
PACKAGE_HOSTS = ("deb.debian.org", "security.debian.org", "download.docker.com", "pkgs.netbird.io")
PACKAGE_TTL_SECONDS = 1800
PACKAGE_RECEIPT = Path('/var/lib/hou1-test-package-access.json')
PACKAGE_HOSTS_FILE = Path('/etc/hosts')
PACKAGE_APT_FILE = Path('/etc/apt/apt.conf.d/99hou1-test-package-access')
PACKAGE_APT_CONFIG = 'Acquire::EnableSrvRecords "false";\n'
PACKAGE_BEGIN = '# BEGIN HOU1 TEST PACKAGE ACCESS'
PACKAGE_END = '# END HOU1 TEST PACKAGE ACCESS'


def package_targets(config):
    rules(config)  # Includes test-only, explicit-deny and bounded-egress guards.
    denied = [ipaddress.ip_network(v, strict=True) for v in config['deny_cidrs']]
    hosts = {}
    for host in PACKAGE_HOSTS:
        addresses = {ipaddress.ip_address(info[4][0]) for info in
                     socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        assert addresses, f'No addresses for package endpoint {host}'
        assert all(a.is_global and not any(a in n for n in denied
                                          if a.version == n.version)
                   for a in addresses), f'Unsafe package endpoint {host}'
        hosts[host] = sorted(map(str, addresses))
    return hosts


def package_hosts(hosts):
    """Replace only our block, preserving the inode and all unmanaged bytes."""
    original = PACKAGE_HOSTS_FILE.read_text()
    lines, managed = [], False
    for line in original.splitlines(keepends=True):
        if line.rstrip('\r\n') == PACKAGE_BEGIN:
            assert not managed, 'Duplicate package hosts block'
            managed = True
        elif line.rstrip('\r\n') == PACKAGE_END:
            assert managed, 'Unexpected package hosts block end'
            managed = False
        elif not managed:
            lines.append(line)
    assert not managed, 'Unterminated package hosts block'
    prefix = '' if not hosts else '\n'.join([
        PACKAGE_BEGIN,
        *(f'{address} {host}' for host, addresses in sorted(hosts.items()) for address in addresses),
        PACKAGE_END, '',
    ])
    updated = prefix + ''.join(lines)
    if updated != original:
        PACKAGE_HOSTS_FILE.write_text(updated)


def package_batch(hosts, existing_sets):
    lines = []
    for version, kind in [(4, 'ipv4_addr'), (6, 'ipv6_addr')]:
        name = f'packages{version}'
        if name not in existing_sets:
            lines.append(f'add set inet {TABLE} {name} {{ type {kind}; flags timeout; timeout {PACKAGE_TTL_SECONDS}s; }}')
            # Works with a restored older isolation table too. Denies still win
            # even though these rules are inserted at the start of each chain.
            for chain in ['host_output', 'egress_guard']:
                family = 'ip' if version == 4 else 'ip6'
                lines.append(f'insert rule inet {TABLE} {chain} {family} daddr != @denied{version} {family} daddr @{name} tcp dport {{ 80, 443 }} accept')
        lines.append(f'flush set inet {TABLE} {name}')
        addresses = sorted({address for values in hosts.values() for address in values
                            if ipaddress.ip_address(address).version == version})
        if addresses:
            entries = ', '.join(f'{address} timeout {PACKAGE_TTL_SECONDS}s' for address in addresses)
            lines.append(f'add element inet {TABLE} {name} {{ {entries} }}')
    return '\n'.join(lines) + '\n'


def package_sets():
    result = subprocess.run(['nft', '-j', 'list', 'table', 'inet', TABLE],
                            check=True, capture_output=True, text=True, timeout=15)
    return {item['set']['name'] for item in json.loads(result.stdout)['nftables'] if 'set' in item}


def close_packages(token):
    receipt = json.loads(PACKAGE_RECEIPT.read_text())
    assert secrets.compare_digest(receipt['token'], token), 'Package lease token mismatch'
    # Keep the receipt if cleanup fails, so an operator can finish it safely.
    package_hosts({})
    if PACKAGE_APT_FILE.exists():
        assert PACKAGE_APT_FILE.read_text() == PACKAGE_APT_CONFIG, 'Package APT configuration changed'
        PACKAGE_APT_FILE.unlink()
    load_batch(package_batch({}, package_sets()))
    PACKAGE_RECEIPT.unlink()


def open_packages(config):
    hosts = package_targets(config)  # Resolve on this host before changing anything.
    assert PACKAGE_BEGIN not in PACKAGE_HOSTS_FILE.read_text(), 'Stale package hosts block needs cleanup'
    assert not PACKAGE_APT_FILE.exists(), 'Stale package APT configuration needs cleanup'
    sets = package_sets()  # Refuse a missing isolation table.
    receipt = {'token': secrets.token_hex(16)}
    # Exclusive claim prevents concurrent runs from replacing another lease.
    with os.fdopen(os.open(PACKAGE_RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        json.dump(receipt, output)
    try:
        load_batch(package_batch(hosts, sets))
        # APT must use the pinned origin, rather than selecting another CDN via
        # DNS SRV records. This setting exists only for this package operation.
        with PACKAGE_APT_FILE.open('x') as output:
            output.write(PACKAGE_APT_CONFIG)
        package_hosts(hosts)
    except BaseException:
        close_packages(receipt['token'])
        raise
    print(json.dumps(receipt))


def google_targets(config):
    """Pair DNS answers with the actual server address and pin those same answers."""
    assert config["environment"] == "test"
    google = config.get("google_oauth", {})
    enabled = google.get("enabled", False)
    empty = ({4: [], 6: []}, {}, None)
    if not google:
        return empty
    result = subprocess.run(
        ["docker", "inspect", google["container"]],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode and not enabled:
        return empty
    result.check_returncode()
    container = json.loads(result.stdout)[0]
    labels = container["Config"]["Labels"]
    assert labels["com.docker.compose.project"] == google["project"]
    assert labels["com.docker.compose.service"] == "authentik-server"
    if not container["State"]["Running"] and not enabled:
        return empty
    assert container["State"]["Running"], "Authentik server must be running"
    if not enabled:
        return {4: [], 6: []}, {}, container["Id"]
    network = container["NetworkSettings"]["Networks"][google["network"]]
    sources = [ipaddress.ip_address(network[key]) for key in
               ("IPAddress", "GlobalIPv6Address") if network.get(key)]
    assert sources, "Authentik server must have an address on its ingress network"
    destinations = set()
    hosts = {}
    denied = [ipaddress.ip_network(v, strict=True) for v in config["deny_cidrs"]]
    for host in GOOGLE_HOSTS:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
        assert addresses, f"Google endpoint {host} returned no addresses"
        for address in addresses:
            assert address.is_global, f"Google endpoint {host} is not a global address"
            assert not any(address in n for n in denied if n.version == address.version), (
                f"Google endpoint {host} overlaps a production deny destination"
            )
        # Pin only address families available to this container. The same DNS
        # answers must drive both name resolution and the firewall; independent
        # Google DNS queries can legitimately return different addresses.
        hosts[host] = sorted(str(a) for a in addresses
                             if any(a.version == s.version for s in sources))
        assert hosts[host], f"Google endpoint {host} has no usable address family"
        destinations.update(addresses)
    entries = {
        version: sorted(f"{source} . {destination}" for source in sources
                        for destination in destinations
                        if source.version == destination.version == version)
        for version in (4, 6)
    }
    return entries, hosts, container["Id"]


GOOGLE_HOSTS_UPDATE = r"""
import json, sys
from pathlib import Path
hosts = json.load(sys.stdin)
path = Path('/etc/hosts')
begin = '# BEGIN HOU1 TEST GOOGLE OAUTH'
end = '# END HOU1 TEST GOOGLE OAUTH'
original = path.read_text()
lines = []
managed = False
for line in original.splitlines():
    if line == begin:
        assert not managed, 'duplicate managed hosts block'
        managed = True
    elif line == end:
        assert managed, 'unexpected managed hosts end'
        managed = False
    elif not managed:
        lines.append(line)
assert not managed, 'unterminated managed hosts block'
if hosts:
    pins = [str(address) + ' ' + host
            for host, addresses in sorted(hosts.items()) for address in addresses]
    lines = [begin, *pins, end, *lines]
updated = '\n'.join(lines) + '\n'
if updated != original:
    # Docker bind-mounts this file; preserve its inode and all unmanaged lines.
    path.write_text(updated)
"""


def update_google_hosts(container, hosts):
    if container:
        subprocess.run(
            ["docker", "exec", "--user", "0", "-i", container,
             "python", "-c", GOOGLE_HOSTS_UPDATE],
            input=json.dumps(hosts), capture_output=True, text=True,
            check=True, timeout=15,
        )


def google_batch(entries):
    lines = []
    for version in (4, 6):
        name = f"google{version}"
        lines.append(f"flush set inet {TABLE} {name}")
        if entries[version]:
            pairs = ", ".join(f"{pair} timeout {GOOGLE_TTL_SECONDS}s"
                              for pair in entries[version])
            lines.append(f"add element inet {TABLE} {name} {{ {pairs} }}")
    return "\n".join(lines) + "\n"


def load_batch(batch):
    with tempfile.NamedTemporaryFile(mode="w", prefix="hou1-isolation-") as f:
        f.write(batch)
        f.flush()
        subprocess.run(["nft", "--check", "--file", f.name],
                       check=True, capture_output=True, timeout=15)
        subprocess.run(["nft", "--file", f.name],
                       check=True, capture_output=True, timeout=15)


def refresh_google(config):
    # Resolve everything before touching nft. A failed refresh retains only the
    # previous short-lived entries, which expire without requiring the timer.
    entries, hosts, container = google_targets(config)
    load_batch(google_batch(entries))
    update_google_hosts(container, hosts)
    print(json.dumps({"environment": "test", "google_pairs":
                      {str(v): len(entries[v]) for v in (4, 6)}}))


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
    for version, kind in [(4, "ipv4_addr"), (6, "ipv6_addr")]:
        lines.append(
            f" set google{version} {{ type {kind} . {kind}; flags timeout; timeout {GOOGLE_TTL_SECONDS}s; }}"
        )
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
        if hook in ("forward", "postrouting"):
            lines += [
                "  ip saddr . ip daddr @google4 tcp dport 443 counter accept",
                "  ip6 saddr . ip6 daddr @google6 tcp dport 443 counter accept",
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
    load_batch(batch)
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
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--render", action="store_true")
    mode.add_argument("--refresh-google", action="store_true")
    mode.add_argument("--open-packages", action="store_true")
    mode.add_argument("--close-packages", metavar="TOKEN")
    a = p.parse_args()
    config = json.loads(Path(a.config).read_text())
    if a.open_packages:
        open_packages(config)
    elif a.close_packages:
        assert config['environment'] == 'test'
        close_packages(a.close_packages)
    elif a.refresh_google:
        refresh_google(config)
    elif a.render:
        print(rules(config), end="")
    else:
        apply(config)
