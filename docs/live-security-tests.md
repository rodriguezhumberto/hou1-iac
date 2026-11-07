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

## JWKS lifetime and IdP revocation follow-up

Use a fresh mode-0700 directory via `HOU1_SECURITY_WORKDIR` for the follow-up.
The updated `auth_api.py` obtains API authorization inside the test Authentik
container; no host SOPS path or controller identity is needed by that helper.
Install the updated staff probe before running. Pause the test monitor timer
and service during deliberate authentication failures, and restore them after
all cleanup and healthy-login checks.

```bash
export HOU1_SECURITY_WORKDIR=<private-followup-directory>
python3 scripts/live_oidc/jwks_overlap.py --phase start
python3 scripts/live_oidc/revocation_boundary.py
# Wait until the start report's natural_expiry_not_before timestamp.
python3 scripts/live_oidc/jwks_overlap.py --phase finish --wait
```

Run these commands sequentially. `start` publishes a temporary union of two
real Authentik public keys only at the test staff JWKS path. The installed
Authentik publishes one selected signing key natively; this fixture measures
the application's overlap/cache behavior, not native Authentik overlap support.
The new `kid` must trigger a refresh, both cached keys must work, and withdrawing
the old public key must leave the warm cache usable until its real 900-second
lifetime expires. `finish` requires at least 905 elapsed seconds, then proves a
JWKS outage fails closed, a withdrawn old key is rejected, and the current key
works again. No app restart, clock change, or token fabrication is involved.
The separate JS regression test accelerates time locally; it is labeled
separately from the real-time deployed test.

Do not run another key-rotation drill during this window. Other probes must
finish before the cache deadline, since a successful post-expiry login can
refresh the cache. `finish` always cleans up. If interrupted, run
`jwks_overlap.py --phase cleanup`; an independent 40-minute host timer also
restores the signing key and exact saved ingress without controller access.
Cleanup verifies original native JWKS publication and deletes only the generated
key and the dedicated fixture container/directory.

The overlap helper now uses `controller.container_api` directly; it does not
require installing `/root/hou1-test-auth-api.py`. Its root recovery script is
self-contained, uses the container-local bootstrap token, and attempts ingress
restoration independently if Authentik is unavailable. The timer is armed before
switching signing keys. `--wait` finishes after natural expiry in a foreground
process retained by the execution tool; avoid detached shell sleep jobs. Start
refuses an active rehearsal-monitor timer to prevent incidental cache refresh.
Stop both `hou1-test-monitor.timer` and `hou1-test-monitor.service` before start,
then restore monitoring after cleanup and require all 33 rules healthy.

`revocation_boundary.py` changes only the internal `vps-staff-admin` fixture,
with an independent five-minute recovery timer. It retains two admin sessions
and a reader control session, disables the fixture and tests fresh-login denial,
then explicitly revokes the admin subject and tests both cookies against the
real protected HTTP API. It repeats the distinction for removal of staff groups.
The account's active state and groups are restored in `finally`; private browser
artifacts are removed and both positive logins are repeated.

An existing session remaining valid after an IdP disable/group change is a
recorded offboarding limitation, not automatic revocation passing. Explicit
subject invalidation must return 401 while the reader control remains 200.
No human Google accounts, NetBird membership, or production state are changed.

## Current-release outage, fresh restore and application rollback

Run the current signed-claim/callback/session matrix first, always restoring
the provider and removing the token proxy before taking a backup. The
`skew_exp_30s` fixture sets `iat` 330 seconds in the past so its recently
expired token still has a valid issuance-to-expiry interval. Empty/false
authorized-party claims and reversed token times are separate rejection cases.

`recovery_gate.py` uses only the fixed isolated VPS adapter. Install the
existing private Auth API helper and updated staff HTTP probe as described
above. Keep the controller monitor paused through intentional failures.
With a new mode-0700 `HOU1_SECURITY_WORKDIR`, run these phases sequentially:

```bash
python3 scripts/live_oidc/recovery_gate.py --phase prepare
python3 scripts/live_oidc/recovery_gate.py --phase outage
# Confirm the independently started Authentik recovery service is healthy.
python3 scripts/live_oidc/recovery_gate.py --phase restore
# Confirm hou1-oidc-fresh-restore.service completed successfully.
python3 scripts/live_oidc/recovery_gate.py --phase finish
```

`prepare` takes a fresh logical backup with the existing host age recipient,
verifies checksum and decryption off-VPS using the controller's separate copy
of the host recovery identity, and uploads only the encrypted archive back for
restoration. It records issuer, public JWKS and a digest of stable UUID/active/
group bindings. It arms a 20-minute host timer before stopping anything.
No controller age identity is uploaded.

`outage` retains an unexchanged code, stops only Authentik server/worker, and
asserts both that callback and a fresh login fail without a staff session.
Fresh pinned VPN SSH, independent NetBird local-owner PKCE/API login and the
current test PAT must keep working. A `finally` starts Authentik independently.

`restore` runs the self-contained `recovery_worker.py` through systemd. It
quarantines the consistent cold original database/configuration and initializes
fresh state from the off-VPS archive. The host guardian can restore the original
without controller SSH or the Authentik API. `finish` verifies the exact issuer,
signing keys and identity bindings, then repeats admin/reader, replay, logout
and reader-write denial before disarming recovery. On failure invoke
`recovery_gate.py --phase recover`; do not remove the original quarantine until
the failure is understood. This is application-state recovery on the existing
host, not a replacement-host/provider-console exercise.

`release_rollback.py` separately tests the staff release rollback. It requires
the current rehearsal's `issue37-security.override.yml` and retained prior test
image `pearlandpaws-test-offboarding:2f133f32aae5656a4dea1b2649b9af166ff16aa9`.
Both images must include persistent subject blocking. An independent five-minute
timer restores the candidate; the script asserts admin/reader, replay, logout
and reader write denial on the prior image, then restores the exact candidate
and repeats those assertions. It does not restore data or restart NetBird.

Afterward remove the temporary RPC/probe files and pending browser artifacts,
verify public staff login/API denial, and resume the controller monitor. Record
the actual immutable app/image refs, backup checksum, restore duration and
healthy final state. Keep earlier NetBird state-rollback and real 905-second
JWKS-cache evidence under their original refs; neither is silently relabeled
as a new drill or as native Authentik dual-key publication.
