# Live isolated OIDC security tests

These are infrastructure #26 and staff #37 checks for the existing isolated VPS
fixture described in [the deployment evidence](vps-test-evidence-2026-11-06.md).
They mutate only test Authentik provider 1 and a fresh test CLI peer. Run each
suite sequentially; do not run them against production or concurrently with
another operator changing that fixture. They do not deploy or merge anything.

`scripts/oidc-integration-harness.py` tests a **synthetic local model**. Its live
requests cover discovery/JWKS/reachability only. A nonempty drill file proves
neither restore nor rollback success. Its report now labels these limits.

## Fixture and execution

The controller adapter in `scripts/live_oidc/controller.py` deliberately pins
the approved test DNS names, private SSH configuration, isolated homelab Docker
peer and SOPS/age paths. It captures secret-bearing subprocess output and passes
credentials over stdin. Never add `set -x`, print decrypted JSON, or invoke the
root-side Auth API RPC directly in terminal logs. Keep the controller workdir
mode 0700 and all keys/secret files outside Git.

Before running:

1. Confirm private SSH to the expected host fingerprint and both positive login
   probes. Record immutable app/infra refs and running container identities.
2. Install `auth_api.py` as `/root/hou1-test-auth-api.py` and `session_db.py` as
   `/root/hou1-test-session-db.py` on the test VPS, root-owned mode 0700. Their
   fixed SOPS paths and container names are specific to this test fixture.
3. Copy the staff repository's `astro-app/scripts/live-oidc-probe.py` to
   `/tmp/live-oidc-probe.py` inside `hou1-vps-admin-test`. It stores pending flow
   codes/cookies privately in `/tmp/pp-live-oidc`; remove interrupted flows.
4. Copy `scripts/oidc-test-token-proxy.py` to
   `/root/hou1-security-proxy/proxy.py`. The root-only directory is mounted into
   the temporary proxy. Its control file holds a code hash and fault mode, never
   a code or client secret. No proxy port is published.

From the infrastructure checkout, run these **in order**, with an outer
`try/finally` that always executes `proxy_cleanup.py` if setup succeeded:

```text
python3 scripts/live_oidc/proxy_setup.py
python3 scripts/live_oidc/controller.py
python3 scripts/live_oidc/claims.py
python3 scripts/live_oidc/callbacks.py
python3 scripts/live_oidc/rotation.py
python3 scripts/live_oidc/sessions.py
python3 scripts/live_oidc/proxy_cleanup.py
```

Claims and rotation each restore the original provider in `finally`. Setup
saves ingress bytes before changing the exact test token endpoint, tests Nginx
configuration before reload, and recovers automatically if setup fails. If the
controller is interrupted, run cleanup before resuming normal use; recover the
provider from the private `provider-restore.json` if its own `finally` was
interrupted. Confirm the temporary scope/key are gone, original mappings/key
match, ingress is byte-identical, and no proxy container remains.

The proxy forwards real token exchanges. It records only the header, issuer,
audience, groups and relative time claims delivered to the deployed callback.
For one armed authorization-code hash it flips one RS256 signature byte;
claims and `kid` remain unchanged. Normal exchanges are unchanged. The staff
probe verifies failed callbacks create no session and protected API returns
401. Unknown groups are a distinct 403 assertion. Positive flows also assert
replay rejection and logout invalidation.

For session tests, retain real HTTP sessions and invalidate only matched test
rows through the private helper. Subject revocation mirrors the application's
operator database invalidation. Idle/absolute tests advance individual test
session timestamps, not the server clock. They exercise real middleware but do
not prove wall-clock waiting or automatic IdP back-channel logout.

## NetBird and legacy checks

`live-netbird-oidc-probe.py` accepts JSON stdin with `auth_url`, `netbird_url`,
`username`, `password`, and `mode` (`normal` or `pkce`). It asserts the external
Authentik path through NetBird's embedded issuer, real PKCE exchange, API 200,
code replay denial, malformed bearer/signature denial, and wrong-verifier 400.
Do not put its stdin in shell arguments or a committed file.

For CLI device SSO, start a separate `hou1-vps-security-cli` container using
`hou1-vps-test-peer:0.82.0`, its own namespace/state, `NET_ADMIN` and `/dev/net/tun`.
Run its daemon with `netbird service run`; run `netbird up --no-browser
--disable-dns --hostname issue26-security-cli --management-url
https://netbird-test.vps-test.pearlandpaws.com`. Capture its device URL privately
in the controller workdir as `cli-login-private.log`, then run
`cli_device_login.py` and `peer_drill.py`. The latter proves the fresh ordinary
peer has no staff transport, temporarily adds it to the existing test
administrator policy group, proves transport plus unauthenticated staff 401,
and restores membership in `finally`. Generate traffic before checking peer
connection status; an idle peer need not have an established tunnel.

Afterward delete only this fresh peer via the test API and remove its container.
Leave existing management peers connected. Assert original groups and peer IDs
match. The staff repository's `live-legacy-auth-probe.py`, run inside the test
VPN peer with the test session secret over stdin, asserts that a valid old HMAC
cookie gets 401 and password login POST gets 405.

Finally remove root-side RPC helpers, proxy/control files and pending peer
flows; repeat admin/reader and NetBird positive logins without the proxy. Verify
public staff denial and service health. Save redacted receipts/assertions with
UTC timestamps and artifact hashes. Reuse prior restore/rollback evidence only
with its actual assertions, refs, immutable artifact hashes and stated limits.
