"""Private root-side Authentik RPC for this isolated fixture. Controller captures secret-bearing API responses; do not run directly in a recorded terminal."""

import json, os, subprocess, sys, urllib.request, urllib.error

s = json.loads(
    subprocess.run(
        [
            "sops",
            "--decrypt",
            "--output-type",
            "json",
            "/opt/hou1-authentik/test/secrets.enc.yaml",
        ],
        env={**os.environ, "SOPS_AGE_KEY_FILE": "/root/.config/sops/age/vps-test.key"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
)
x = json.loads(
    subprocess.run(
        ["docker", "inspect", "hou1-authentik-test-authentik-server-1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
)[0]
base = (
    "http://"
    + x["NetworkSettings"]["Networks"]["hou1-authentik-test"]["IPAddress"]
    + ":9000/api/v3/"
)
a = json.load(sys.stdin)
path = a["path"]
assert path.startswith(
    (
        "providers/oauth2/",
        "propertymappings/provider/scope/",
        "crypto/certificatekeypairs/",
        "core/users/",
        "core/groups/",
        "schema/",
    )
)
request = urllib.request.Request(
    base + path,
    data=None if "body" not in a else json.dumps(a["body"]).encode(),
    method=a.get("method", "GET"),
    headers={
        "Authorization": "Bearer " + s["AUTHENTIK_BOOTSTRAP_TOKEN"],
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        b = response.read()
        print(b.decode() if b else "{}")
except urllib.error.HTTPError as e:
    print(json.dumps({"http_error": e.code, "details": e.read().decode()[:2000]}))
    sys.exit(1)
