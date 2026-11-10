# Clean test-host recovery

Issue #27's replacement-host gate uses `restore-test-host.yml`, followed by
`publish-test-recovery.yml`. These entry points recover the selected test point
on fresh Debian 12 at the same HOU1 address. They are not production restore
commands. Keep HOU4 and its retained backups intact.

The isolated component drill in [PR #59](https://github.com/rodriguezhumberto/hou1-iac/pull/59)
already passed. This clean-host workflow still needs its own live evidence.
Local preparation, syntax checks and fixture tests do not complete that gate.

## Before wiping HOU1

1. Confirm HOU4 provider-console login independently of NetBird. Restoring
   NetBird's database and keys should reconnect existing peers, but that is a
   recovery check, not a substitute for console access. Leave alerts enabled.
2. Retain an operator-readable encrypted recovery point, its manifest/receipt
   and the separate controller deployment/renewal handoff. A temporary recovery
   recipient may be added alongside the operator recipient. Retain its private
   identity through the authorized restore; retire it after the gate handoff.
   Never upload or install the operator's long-lived private age identity.
3. Verify/extract with `scripts/verify-recovery.py`. Preserve original versus
   re-encrypted ciphertext receipts separately. Use a mode-0700 staging root.
4. Provide a Git bundle with `refs/heads/recovery` at the exact saved staff
   commit. An older deployment bundle may not contain that commit. Obtain the
   missing immutable source before wiping; do not depend on HOU1's checkout.
5. Prepare an image-lock JSON with keys `postgresql`, `authentik-server`,
   `authentik-worker`, `netbird-server`, `dashboard`, `nginx`, `node`. Values
   must be the respective official repository plus `@sha256:<digest>`. Capture
   verified registry descriptors; do not transfer old Docker layers or caches.
   NetBird remains 0.82.0. The saved Node 24.22.2 Dockerfile uses the locked
   Node digest in a separate build recipe; the Git checkout stays unchanged.
6. Reconcile account disables, Google admission, group/role grants, peer grants
   and API credentials since the point. Record hashes or reviewed summaries,
   never credentials or allowlist values. Pin the lost HOU1 peer's ID, name and
   declared IP from the selected point. Do not select a peer by desired IP alone.
7. Prepare the fresh inventory, controller source-restricted temporary SSH key,
   operator SSH public key and encrypted controller credentials. Refresh the
   existing bounded package-update address allowlist before the run. Keep DNS
   credentials on the controller. Verify the new SSH server identity after the
   reinstall; establish key authentication before running Ansible.

The controller must finish these checks and explicitly release the wipe
checkpoint. Wipe **HOU1 only**. A failure later requires stopping, preserving
evidence, fixing locally and waiting for a new operator-confirmed clean attempt.

## Inputs

Use the established full test inventory and its group defaults, with
`node_bootstrap: true`, `ansible_host: <HOU1 public IP>`, `cms_environment: test`,
the existing isolated names/subnet and the verified SSH configuration. Add:

```yaml
host_recovery_id: replacement-YYYYMMDD-01   # new for every attempt
host_recovery_console_confirmed: true     # actual independent HOU4 console
host_recovery_archive: /private/point/recovery.age
host_recovery_manifest: /private/point/manifest.json
host_recovery_identity: /private/point/temporary.agekey
host_recovery_source_bundle: /private/point/staff.bundle
host_recovery_image_lock: /private/point/image-lock.json
host_recovery_evidence_directory: /private/point/evidence
host_recovery_access_reviewed: false      # remains false until reconciliation
host_recovery_lost_peer_id: <pinned lost HOU1 peer ID>
host_recovery_lost_peer_name: <pinned lost HOU1 peer name>
```

Retain the existing `rehearsal_*`, `netbird_*`, `authentik_*`, operator key,
bootstrap CIDR and unified-backup inputs. No fresh identity seeds are needed.
The restored runtime key path must match the saved test-only age identity.
This workflow supports the original fixed test paths and amd64 images; it
refuses production, an existing installation, existing attempt/evidence IDs,
and nonempty Docker container/image stores.

## Restore, review, publish

Run once from the immutable infrastructure revision under review:

```bash
ansible-playbook -i /private/point/inventory.yml \
  ansible/playbooks/restore-test-host.yml --limit YOUR_TEST_HOST
```

The playbook reuses the existing cloud-init wait, package, capacity, user and
Docker preparation. It authenticates the recovery point on the controller,
validates source, endpoints, mounts, certificates and images, and restores saved
configuration. It pulls images from registries and builds the app without cache
before placing runtime credentials in the checkout. PostgreSQL imports only
into an empty schema, with TCP query readiness and `ON_ERROR_STOP`.

Backed-up IdP sessions/codes/tokens and staff sessions are cleared while restored
writers are stopped. Authentik policy bootstrap and CMS seeding are never run.
Saved NetBird connection flags are also cleared while its server is stopped:
the backup's `connected` values describe old transport sessions. Peer IDs, keys,
authorization, group membership and last-seen timestamps are preserved. Peers
must reconnect before they can be reported as connected. The later deletion
guard continues to refuse a connected peer.
The saved database/media pair is recovered even when the original CMS was empty.
Nginx and NetBird STUN bind to **loopback only**. UFW alone cannot prevent Docker
from exposing published ports. The initial restore also applies the existing
production egress boundary, with Google exceptions still disabled.

Review the protected `restore-report.json` and any authorization changes before
setting `host_recovery_access_reviewed: true`. A pre-wipe comparison cannot
reconcile changes made after that comparison. Then run once:

```bash
ansible-playbook -i /private/point/inventory.yml \
  ansible/playbooks/publish-test-recovery.yml --limit YOUR_TEST_HOST
```

Publication accepts only the expected loopback port baseline. Enrollment then
requires the pinned lost peer to match its ID/name/IP and be disconnected before
deleting that identity. The existing NetBird client role enrolls the replacement
and restores its declared address/group. Existing DNS and security roles verify
fresh administrative VPN SSH before retiring public bootstrap access. Existing
rehearsal gates restore persistent isolation and bounded Google egress.

After private DNS reconciliation, a bounded SSH readiness check waits for the
restored VPN path before changing listeners. It polls only recognized transport
errors; authentication, host-key and unexpected errors stop immediately. The
final private SSH check after the listener change remains a single attempt.
SSH rollback also disarms its fallback timer after restoring the old settings.

## Complete the recovery gate

Verify HOU4 reconnects with its retained peer; use its console if re-enrollment
is necessary. Confirm the operator's personal SSH key on both nodes. Update
HOU4's backup source SSH pin through its existing Ansible backup role using the
verified **new** HOU1 host public key. Do not disable host-key verification.
Reapply the existing HOU1 `monitoring.yml` and `backups.yml` over VPN, with their
preserved private inputs, to reconcile policies referring to the replacement
peer. Prove a fresh off-host collection and real Discord delivery/recovery.

Run the isolated application/session/media/rollback drill against the recovered
services, external Google sign-in, approved/rejected VPN access, public staff
and admin denials, revocation, renewal and reboot/outage checks. Verify recovered
signing/client/emergency-admin material and reconcile later offboarding before
accepting the restored system. Record time and data recovered separately from
provisioning and operator pauses. Keep issues #27/#39 open until their criteria
are met; this workflow does not authorize production promotion.

## First error and cleanup

Use one controller invocation per phase and latch any nonzero result before
proceeding. The restore driver also writes `first-error.json` and refuses a
second attempt or completed phase. Script failures stop the restored ingress
and application/IdP/NetBird writers, retaining protected diagnostics. Do not
remove failure markers and resume. Keep the failed host until evidence is saved;
after correction, wait for operator approval of the next clean attempt.

The shared preparation role checks resolver attributes before attempting an
immutable-file unlock; an unlock failure is fatal. Existing bounded readiness
polls wait for startup, while SSH rollback and Ansible `always` blocks may run
after a failure to preserve access, diagnostics and plaintext cleanup. Do not
use a callback that exits Ansible immediately and bypasses those cleanup blocks.

For ordinary task failures, Ansible removes controller plaintext staging,
including after evidence-transfer failure. An unreachable host or terminated
controller can bypass Ansible cleanup; remove its private `hou1-host-recovery-*`
staging directory before handoff, retaining encrypted inputs and diagnostics.
Successful restore removes the remote bundle, SQL/payload
staging and source bundle; installed runtime data/secrets remain at their fixed
root-owned application paths. Reports and `private.log` remain under the private
attempt root. Publish only sanitized reports; never publish private logs or
payloads. Retire temporary keys and remove temporary access after successful
operator verification, preserving the encrypted recovery point and receipts.
