# Fresh-host rehearsal for issues #27 and staff #39

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
are repeated after reboot. Operator decryption and personal SSH-key access must
still be confirmed before retiring the temporary deployment grant.

A brief post-boot disk-I/O headroom failure recovered without changing the 70%
threshold (follow-up utilization 9.6%). The final synthetic monitor reports only
the deliberately outstanding backup-freshness rule; no alert deliveries are
pending. Human Google sign-in was confirmed by the operator.
