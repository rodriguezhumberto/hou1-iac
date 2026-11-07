"""Assert SSO CLI enrollment and live policy grant/removal without changing existing peers."""

from controller import *
import urllib.request, urllib.error, time

NB = "https://netbird-test.vps-test.pearlandpaws.com"
r = subprocess.run(
    [
        "sops",
        "--decrypt",
        "--output-type",
        "json",
        "/var/tmp/hou1-vps-test/netbird-token.enc.yaml",
    ],
    env={**os.environ, "SOPS_AGE_KEY_FILE": "/var/tmp/hou1-vps-test/test-age.key"},
    capture_output=True,
    text=True,
    check=True,
)
secret = json.loads(r.stdout)
assert secret["netbird_management_url"] == NB


def nb(path, method="GET", body=None):
    req = urllib.request.Request(
        NB + "/api" + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Token " + secret["netbird_api_token"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def peer(command):
    r = subprocess.run(
        ["ssh", "dev@192.168.0.151", "docker exec hou1-vps-security-cli " + command],
        capture_output=True,
        text=True,
    )
    return r


peers = nb("/peers")
target = [p for p in peers if p["name"] == "issue26-security-cli"]
assert len(target) == 1
pid = target[0]["id"]
admin = [p for p in peers if p["ip"] == "100.91.175.237"]
assert len(admin) == 1
allgroups = nb("/groups")
groups = [
    g
    for g in allgroups
    if any(p["id"] == admin[0]["id"] for p in (g.get("peers") or []))
    and g["name"] != "All"
]
print(
    "test peer enrolled; candidate admin groups",
    [(g["name"], len(g["peers"])) for g in groups],
    flush=True,
)
# Show only policy structure, never PAT or peer identity keys.
print(
    "test policies",
    [
        (p["name"], [(r["name"], r["protocol"], r.get("ports")) for r in p["rules"]])
        for p in nb("/policies")
    ],
    flush=True,
)
status = json.loads(peer("netbird status --json").stdout)
assert (
    status["management"]["connected"]
    and status["signal"]["connected"]
    and status["peers"]["total"] == 0
)
cmd = "curl --silent --show-error --connect-timeout 3 --max-time 5 --resolve staff-test.vps-test.pearlandpaws.com:443:100.91.0.10 https://staff-test.vps-test.pearlandpaws.com/api/staff/cms/pets -o /dev/null -w %{http_code}"
blocked = peer(cmd)
assert blocked.returncode in (7, 28) and blocked.stdout == "000"
print(
    "PASS unprivileged SSO CLI peer has zero authorized peers; staff TCP connection blocked",
    flush=True,
)
(ROOT / "peer-drill-state.json").write_text(
    json.dumps({"peer_id": pid, "groups": groups})
)
(ROOT / "peer-drill-state.json").chmod(0o600)
assert len(groups) == 1
original = groups[0]
gid = original["id"]
ids = [p["id"] for p in (original.get("peers") or [])]
try:
    nb(
        "/groups/" + gid,
        "PUT",
        {
            "name": original["name"],
            "peers": ids + [pid],
            "resources": original.get("resources") or [],
        },
    )
    for _ in range(15):
        probe_result = peer(cmd)
        st = json.loads(peer("netbird status --json").stdout)
        print(
            "authorized probe",
            st["peers"]["total"],
            st["peers"]["connected"],
            probe_result.returncode,
            probe_result.stdout,
            flush=True,
        )
        if probe_result.returncode == 0 and probe_result.stdout == "401":
            break
        time.sleep(2)
    else:
        raise AssertionError("authorized peer connection did not establish")
    allowed = peer(cmd)
    assert allowed.returncode == 0 and allowed.stdout == "401", (
        "VPN alone should not authenticate staff"
    )
    print(
        "PASS temporary policy membership establishes peer transport; unauthenticated staff API still HTTP 401",
        flush=True,
    )
finally:
    nb(
        "/groups/" + gid,
        "PUT",
        {
            "name": original["name"],
            "peers": ids,
            "resources": original.get("resources") or [],
        },
    )
for _ in range(10):
    st = json.loads(peer("netbird status --json").stdout)
    if st["peers"]["total"] == 0:
        break
    time.sleep(2)
else:
    raise AssertionError("revoked policy membership retained peer")
blocked = peer(cmd)
assert blocked.returncode in (7, 28) and blocked.stdout == "000"
print(
    "PASS group removal revokes peer authorization and blocks staff transport again",
    flush=True,
)
(ROOT / "peer-report.json").write_text(
    json.dumps(
        {
            "cli_version": st["cliVersion"],
            "daemon_version": st["daemonVersion"],
            "sso_device_flow": "passed",
            "unprivileged_transport_denied": "passed",
            "temporary_membership_peer_transport": "passed",
            "vpn_without_staff_auth_401": "passed",
            "removed_membership_transport_denied": "passed",
        },
        indent=2,
    )
)
