# Fresh-host rehearsal for issues #27 and staff #39

> Historical rehearsal journal. For current deployment and recovery, use [production operations](production-oidc-runbook.md) and [production recovery](production-host-recovery.md). Pending gates below describe the recorded attempt, not current rollout status.

This is a new test installation, not production promotion or a database restore.
The operator confirmed these inputs on 2026-11-08:

| Input | Confirmed value |
| --- | --- |
| HOU1 | 139.177.151.12, fresh Debian 12, root SSH port 22 |
| Capacity | 4 vCPU, 16 GiB RAM, approximately 80 GiB disk |
| HOU4 | 136.0.42.245; preserve its installation |
| Domains | test, landing-test, staff-test, auth-test, netbird-test under vps-test.pearlandpaws.com |
| Private access | Staff, administration, SSH and metrics through VPN |
| VPN | 100.91.0.0/16 |
| Identity | Existing encrypted Google allowlist and role assignments; existing Google OAuth test client; separate local emergency administrator |
| Controller | Temporary rehearsal age identity; fresh application secrets also encrypted to the operator |
| TLS contact | cheep.bikers-3c@icloud.com |
| Monitoring | Existing HOU4 and Discord destination; alerts remain enabled |
| Content | Staff/CMS content requires manual seeding |
| Backups | Deferred by the operator; no off-host destination or retention selected |

The operator authorized trusting the rebuilt host's presented key. Its pinned
ED25519 fingerprint is `SHA256:XXORXxOJUlqWb34HvHR1iYYflnndjXQfNzj4OT+NjPE`.
This was trust on first use, not an independent provider-console comparison.
The initial read-only check found Debian 12, 4 CPUs, 16005 MiB RAM, 75 GiB free
disk and over five million free inodes. Temporary root SSH bootstrap access was
verified; the password delivery file was deleted afterward.

## Input and execution checklist

1. Use fresh private inventory and paths. Do not reuse the previous NetBird PAT,
   database archives, deployment identities or prior evidence as new results.
   Pin infrastructure and staff commit SHAs and retain NetBird 0.82.0.
2. Re-encrypt only the existing Google allowlist and staff assignments to the
   temporary deployment recipient and operator. Supply the Google test client
   secret through the secure prompt. Keep the previous operator-only bundles
   restricted. Encrypt fresh application secrets separately for controller and
   host; neither document needs the shared DNS token when TLS is controller-managed.
3. Prepare test TLS certificates and both NetBird geolocation cache files before
   enforcing isolated egress. These are prerequisites, not database restores.
   Record their provenance and hashes. Do not copy previous account databases.
4. Run the fresh-host entry points in order: `rehearsal-prepare.yml`,
   `rehearsal-seeds.yml`, then `rehearse-vps.yml`. Use the same pinned infrastructure
   checkout for each invocation and save redacted output. Enable the Authentik
   bootstrap and `authentik_google_enabled` in the isolated test inventory.
   Preparation detects provider cloud-init and waits up to 15 minutes for it
   to finish before installing packages. A failed or timed-out initialization
   stops deployment. The initial package task also allows up to five minutes
   for other package jobs to release their locks; never kill those jobs or
   remove their lock files to force deployment through.
   Preparation installs Debian's resolver and encryption dependencies before the
   common role or encrypted seeds need them. Create the staff Git bundle from
   `main` (or explicitly set `rehearsal_staff_bundle_ref`); `rehearsal_staff_ref`
   still selects the exact immutable application commit from that bundle.
5. Verify that Authentik initializes groups, claims, clients and its local
   emergency administrator. Google source bootstrap must bind the verified-email
   allowlist to both source flows before enabling Google. The bootstrap manifest
   and command must remain private and excluded from logs.
6. Verify that NetBird initializes its owner, encrypted PAT, groups and policies
   through automation. Re-enroll HOU4 and administrative peers as needed after
   the control-plane reset, keeping console recovery available. Apply and test
   the existing admission policy before disabling manual approval. Reconcile
   explicit staff roles and network groups after those Google users first log in.
   The initial `rehearse-vps.yml` run starts bootstrap ingress only. After verifying
   administrative SSH over the new VPN, run the complete `hou1.yml` with the
   bootstrap inventory and `node_bootstrap: true`. Its existing security role
   proves a fresh private administrative connection, switches Ansible to that
   connection and removes its bounded recovery timer before finishing the app.
   Pass fresh secrets through a mode-0600 file in a private RAM-backed directory
   and remove it in `finally`; Ansible resolves piped `/dev/stdin` to an
   inaccessible pipe path in this controller version. Finish with the
   private `rehearsal-gates.yml` and `monitoring.yml` entry points; never close
   bootstrap SSH before proving its private replacement. The gate removes the
   exact controller-source bootstrap firewall rule declared by the seed playbook.
   Before invoking `monitoring.yml` or `backups.yml`, re-enroll preserved HOU4
   through its provider console, revoke the one-time setup key, and verify
   private SSH. Require exactly one connected HOU4 peer at `100.91.0.22` in
   `test-servers`. If console recovery is pending, wait before starting either
   playbook. Their existing peer-policy guards must remain enforced.
   A first Google web login creates the fresh identity; it does not prove device
   enrollment. Run `scripts/rehearsal-netbird-admission.py` with
   `--identity-secrets` selecting the matching owner-encrypted naming bundle, and `--profile`
   pointing to the [inventory-exported identity profile](authentik-bootstrap.md#google-admission-and-staffvpn-assignments) and the encrypted
   admission inputs and verify its positive/negative policy checks, disabled
   approval requirement, exact pending-user reconciliation, and explicit staff
   assignments before asking the operator to test a NetBird client. Until then,
   the initial approval gate can reject peer creation despite successful Google
   authentication. See the [admission procedure](rehearsal-monitoring-isolation.md).
7. Record the manual CMS seed input, command, image/commit, counts and resulting
   page checks. The app initializes its schema; staff content is the manual step.
   The staff repository's `docs/cms-import.md` describes isolated synthetic seed
   and content import workflows. Do not copy production forms/customer data into
   test or use the isolated seed command against live runtime storage.
8. Validate public denial and VPN access, Google login and role enforcement,
   token/revocation tests, HOU4 metrics and Discord delivery, controller-managed
   certificate renewal and alert recovery. Verify restart/reboot persistence.
   Existing alerts stay enabled throughout; do not apply a maintenance silence.
9. Hand off new ciphertext to the operator and verify local decryption before
   retiring the temporary controller deployment identity and bootstrap SSH grant.
   Replace ongoing job inputs with their scoped identities. A host runtime age
   identity is distinct from the retired controller deployment key.

## Outstanding production gates

No restore evidence exists for this fresh run. Backups and an off-host restore
drill remain outstanding under #27; the final verify phase must continue to fail
without genuine restore evidence. Do not close the rollout issue or claim
production readiness based only on successful deployment or manual seeding.
Controller isolation and any other open gate remain separately applicable.

The obsolete `hou1-test-tls.timer` and service were removed from the controller.
The old HOU1 synthetic monitor is disabled until its inputs can be replaced.
HOU4's TLS renewal and TLS metrics timers remain enabled, and its monitoring and
alert delivery are not silenced. Fresh deployment must reinstall and validate
the intended HOU1 renewal and synthetic-check jobs with new scoped inputs.

## Fresh-run checkpoint (2026-11-08)

The corrected pinned bootstrap completed with 246 successful tasks and no
failures. Full private SSH hardening/application deployment completed with 122,
monitoring with 37, and isolation with 24 successful tasks, all without failures.
HOU4 retained its monitoring installation, rejoined at its existing private IP,
and resumed scraping HOU1. Both nodes' public SSH and metrics ports are closed;
public staff login/API and Authentik administration return 403. Public website,
NetBird API and Authentik discovery return 200 with trusted TLS.

Fresh internal admin/reader login, protected read, reader write denial and logout
checks passed. The admission policy passed four application-user and five Google
verified-email checks before disabling manual approval. One encrypted staff
assignment initially awaited the user's first Google login in the fresh identity
database. The operator has now confirmed that Google sign-in succeeded;
reconciliation verified one staff assignment, zero waiting assignments and ten
live policy checks. The NetBird account retains the regular user role. The old and new databases were not mixed.

The host age identity can decrypt application runtime secrets without the DNS
capability. Controller health inputs now contain only the two fresh fixture
passwords. Existing scoped DNS renewal credentials remain on the controller;
monitoring and renewal timers are enabled. The separate ongoing test-job SSH key
was restored alongside the operator and temporary keys. Its passwordless sudo
access remains a controller-isolation limitation. Backup freshness alerts are
expected to fire because the operator deferred backups; keep them visible.

The fresh live security matrix passed 19 signed-claim cases, 8 callback cases,
3 signing-key rotation logins and 4 session cases, plus its positive baseline.
Invalid state/cookies, nonce, PKCE, signature, issuer, audience, authorized party,
token times and malformed groups were denied; an unknown group received 403.
Real session revocation and timestamp-driven idle/absolute expiry returned 401.
The original provider, signing key and ingress were restored; the unpublished
fault proxy was removed. NetBird's live PKCE, replay and invalid-bearer checks
passed too. These rotation checks do not replace the separate real-time
old/new signing-key overlap and cache-expiry gate.

Google discovery and JWKS returned 200 with certificate validation inside
Authentik; a deliberately invalid token request returned Google's expected 400.
The production/default-deny drill passed on the host and all seven workload
namespaces. Discord's integration counter increased with no reported delivery
failures after a bounded test alert; this is aggregate delivery evidence, not an
independent confirmation of message display at the destination.

The reboot check passed: HOU1 recovered VPN-only SSH and metrics, NetBird,
Authentik discovery and the persistent isolation/collector services without a
public SSH fallback or alert silence. Fresh access and Google endpoint checks
are repeated after reboot. The operator confirmed local decryption and personal SSH-key access. The
temporary deployment grant has been retired; see the handoff checkpoint below.

A brief post-boot disk-I/O headroom failure recovered without changing the 70%
threshold (follow-up utilization 9.6%). The final synthetic monitor reports only
the deliberately outstanding backup-freshness rule; no alert deliveries are
pending. Human Google sign-in was confirmed by the operator.

## Operator handoff checkpoint

The operator's selected connected MacBook peer was added to the existing
administrators group after verifying its approved Google owner. Only that device
was added; other memberships, policies and its regular NetBird user role were
preserved. The operator confirmed both personal SSH access and local decryption
of the delivered operator-only credential archive.

The temporary key was removed from both root and dev authorized keys, and a
fresh connection using only that identity received authentication denial. The
operator and ongoing job keys remain installed. Four active controller SOPS
sources were re-encrypted with fresh data keys to the operator alone; the old
temporary identity could no longer decrypt them before deletion. The temporary
deployment age key, local host-age copy, SSH private key and duplicate plaintext
certificate seed state were deleted. The host's distinct runtime identity and
the unchanged operator-encrypted recovery archive remain available.

Private inventories no longer contain the temporary public key or enable
bootstrap mode. Ongoing health and renewal identities are retained separately.
Their existing SSH access still has passwordless sudo, so this handoff does not
complete the remaining controller-isolation gate. Backups/restore, manual CMS
seeding and the other recorded rehearsal gates remain outstanding.

Post-retirement checks passed for the host runtime secret boundary, denial of
public SSH on both nodes, the HOU1 renewal service and HOU4 renewal-metrics
publication. All four existing monitoring/renewal timers remain active. Health
and DNS jobs still decrypt only their respective two-password and one-token
bundles. The synthetic monitor reports only the known missing-backup alert,
with no pending alert deliveries. No application or infrastructure code changed
for this handoff; the earlier repository test results remain applicable.

## Prepared off-host recovery follow-up

The [HOU4 backup protocol](hou4-backup-protocol.md) now provides the restricted
encrypted source role, receiver contract and offline validation procedure. This
is implementation/test preparation; the earlier live checkpoint still has no
verified off-host backup or clean-node restore. Repeat the live gates after the
fresh HOU4 deployment and key pairing before closing #27.

## Fresh-wipe attempt stopped at NetBird initialization (2026-11-09 UTC)

The next fresh Debian 12 run used merged infrastructure `58520a4` and staff
`c3b51ec`. HOU4 at `209.135.170.2` was preserved with alerting enabled.
Preparation passed 27 tasks and encrypted seeding passed 17, with no failures.
The first cold application build and bootstrap ingress completed, but NetBird's
missing-secrets guard counted the two seeded geolocation cache files as account
state and refused initialization. An immediate-stop controller callback ended
the run at that assertion; no later tasks, live fixes or retries were run.

The corrected guard permits only regular files named `GeoLite2-City_YYYYMMDD.mmdb`
or `geonames_YYYYMMDD.db` without `secrets.json`. Any other entry, including
directories, links, hidden files and cache-like WAL names, still requires the
original secrets. Existing secrets are never replaced. Regression tests exercise
the real Ansible guard and generator against these cases.

See [attempt evidence](evidence/fresh-host-stopped-2026-11-09.json). The fix has
not been deployed: another operator-confirmed wipe is required before starting
again from preparation. This stopped attempt does not satisfy the rollout or
recovery gates in #27 and staff #39.

## Fresh-wipe attempt stopped at Authentik bootstrap (2026-11-09 UTC)

Attempt `2026-11-09-02` used merged infrastructure `5cb8318` and staff
`c3b51ec`. Preparation passed 27 tasks, encrypted seeding passed 17, and the
corrected NetBird cache guard passed live. NetBird initialized its owner, groups
and policies; Authentik's database, server and worker became healthy. The first
failure was the Authentik identity-bootstrap command. The controller stopped
immediately, without retrying or applying the local fix to the failed host.

The original exception was hidden by `no_log`. Read-only API request evidence
places the failure after reading the default provider flows and signing
certificate, before creating OAuth providers. Later exact-match reads found
all three defaults. This suggests asynchronous default-object readiness but
does not prove which object was missing. The local fix waits for required
built-in objects before identity writes, with a 180-second polling deadline.
API/authentication errors and duplicate objects fail immediately; mutations
are never retried.

Focused tests passed 17 cases, the full suite passed 164 with one optional
dnsmasq skip, and eight playbook syntax checks passed. See the
[attempt evidence](evidence/fresh-host-authentik-stopped-2026-11-09.json).
These are local checks; fresh-host validation remains pending.

Immediate termination bypassed Ansible cleanup, so only the generated root-only
bootstrap manifest was removed and verified absent. No deployment resumed.
HOU4 at `209.135.170.2` remained unchanged with alerts enabled. The operator
selected its provider console for later VPN re-enrollment; no console commands
were issued during this attempt. Another confirmed HOU1-only wipe is required
before restarting at preparation. Production approval and both rollout issues
remain open.

## Fresh-wipe attempt stopped before monitoring changes (2026-11-09 UTC)

Attempt `2026-11-09-03` used infrastructure `46aff11` and staff `c3b51ec`.
Preparation passed 27 tasks, encrypted seeding passed 17, bootstrap passed 252,
and the full private-access/application playbook passed 121, all without
failures. The Authentik readiness fix passed live. The administrative peer
re-enrolled and private SSH/sudo were verified before hardening.

The controller invoked monitoring before preserved HOU4 had re-enrolled. Its
existing guard correctly rejected the missing metrics peer before policy writes
or exporter installation. This was a controller sequencing error. Execution
stopped immediately; no live correction or retry followed. HOU4 was unchanged
and alerts were not silenced. Another HOU1-only wipe is required under the
operator's stop-on-error rule.

The operator reported successful Google authentication, followed by a NetBird
client rejection because the user was pending approval. Admission reconciliation
had not run. This records an intermediate state, not a passed client-login gate.
The checklist now makes both enrollment and admission prerequisites explicit.
Local controller prerequisite checks cover 16 missing/invalid/ready HOU4 cases
without remote calls. The infrastructure policy remains unchanged.

See [attempt evidence](evidence/fresh-host-monitoring-stopped-2026-11-09.json).
Production approval and rollout issues remain open.

## Fresh-wipe attempt stopped at the provider package lock (2026-11-09 UTC)

Attempt `2026-11-09-04` used infrastructure `566d812` and staff `c3b51ec`.
The first preparation package task failed after its default 60-second lock
wait. Read-only process ancestry and cgroup checks identified the lock holder
as the provider's `apt-get` under `cloud-final.service`, which was still
activating. Deployment stopped immediately. No retry, lock removal, package-job
termination or live correction followed. Seeding and application deployment
never started; HOU4 remained unchanged and alerts were not silenced.

Preparation now waits for cloud-init when installed, then gives the initial
package task a bounded lock wait. Local Ansible fixtures cover delayed successful
initialization, failed initialization, timeout and hosts without cloud-init.
The corrected order for HOU4 enrollment and allowlist admission remains required.
See [attempt evidence](evidence/fresh-host-package-lock-stopped-2026-11-09.json).
Fresh-host validation requires another confirmed HOU1-only wipe; production
approval and rollout issues remain open.
