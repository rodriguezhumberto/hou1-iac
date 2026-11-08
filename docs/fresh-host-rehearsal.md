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
