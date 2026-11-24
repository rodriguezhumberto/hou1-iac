# Controller responsibilities and future separation

The controller is an operations host and its installed automation. The coding
agent develops, tests and invokes that automation within the owner's authorized
scope. Scheduled operations must work without an agent, conversation or editor.
The current coding-agent host temporarily serves both roles. It remains the
operational controller until a separate, recorded handoff completes.

## Responsibilities and boundaries

| Responsibility | Controller owner and inputs |
| --- | --- |
| Deployment coordination | Pinned HOU1/HOU4 checkouts, toolchain, private workflow/inventories, release receipt and source bundle; existing playbooks own each resource |
| Input validation and evidence | Managed input contracts, immutable generated snapshots, host-key pins, run receipts and retained failures |
| Administrative transport | Dedicated enrolled VPN peer, isolated network/resolver view, operator SSH authentication and independent bootstrap access |
| TLS issuance and renewal | Scoped DNS credential, encrypted ACME/certificate state, renewal identity/key, dedicated restricted VPN peer, fixed root launcher and user timers |
| Identity admission | Encrypted approved-account/role declarations, controller profile and scheduled reconciliation of explicit application/VPN grants |
| Recovery coordination | Selected encrypted point, source/image bindings, access review, new host/receiver pins, restore/publication/finalization and offline verification |
| Operational coordination | Controller workflow lock, host capture admission, environment-specific timers, bounded waits and cleanup |

HOU1 runs the application, identity/VPN services, source capture and exporters.
HOU4 runs monitoring, synthetic login checks and scheduled encrypted collection.
The controller verifies recovery points offline; it is not their only storage
location. The hypervisor/provisioning project owns VM power, disks and provider
firewall policy. Console recovery and owner-held recovery material must remain
independent of the managed VPN and the controller.

If the controller is unavailable, host services and their installed capture,
collection and monitoring schedules can continue. Controller identity admission,
certificate renewal/status delivery, deployments and coordinated restore cannot.
Existing certificates keep serving until expiration; missed controller jobs and
status freshness therefore need monitoring. A controller outage is not itself an
application outage, but it can become one if unattended obligations are missed.

The coding agent owns code changes, tests, documentation and authorized execution.
The controller owns persistent execution and protected operational state. Human
access decisions, independent recovery custody and deployment authorization stay
with the owner. Moving automation does not transfer those decisions to an agent.

## Current coupling to remove later

The recorded controller inventory includes test and production renewal/status
jobs, test identity admission, a test alert receiver and inactive legacy units.
Inventory installed units and their actual enabled/running state before
selecting a migration scope; filenames alone do not establish current ownership.
Production duties need their own continuity plan even when the recovery drill is
test-only.

Current dependencies include private absolute paths, installed program copies,
root-owned fixed launchers/sudoers, the persistent user manager, Docker namespaces,
SSH-agent availability and clean pinned repositories. Recheck each job's actual
tooling and import paths: temporary installations and jobs reading a development
checkout are migration debts. Later foreground recovery runs used tooling under
private persistent state, which does not prove every scheduled job was migrated.
Prepare durable tool installations and dedicated operational checkouts so editing
code or ending an agent session cannot invalidate recurring jobs. Keep the
existing public commands and recovery formats when changing installation paths.

The private starting inventory is under
`/home/dev/.ssh/hou-lab/controller-separation/`. Its metadata-only capture helper
owns dated inventory files; they contain path references, unit state, executor
profiles and selected container mounts, without decrypted credentials. The
private README identifies the accepted recovery receipts and current selectors.
This inventory is not an encrypted controller backup or proof of portability.
Recheck indirect imports, private helpers, root files and SSH-key custody.
Actual domains, account names and deployment repository names stay in private
inputs.

A historical encrypted renewal copy is not proof of a complete current controller
backup. Before controller-loss acceptance, verify current dependency coverage,
owner decryption and retention outside the controller and managed hosts. Keep the
owner recovery identity in owner custody; preserve the scoped recurring-job keys.
The [client recovery and artifact audit](evidence/client-reconnection-2026-11-24.md)
records the latest boundary.

## Handoff for the later migration agent

1. Inventory each environment's jobs, peer identities, paths, key scopes, encrypted
   state, release artifacts and receipts. Retain owner-decryptable recovery copies
   outside both controller and managed hosts. Separate recurring-job capabilities
   from deployment/admin capabilities and the offline backup decryption identity;
   do not install the owner's long-lived recovery key in recurring jobs.
2. Prepare a separate controller account/host with durable pinned tooling and
   operational checkouts. Reinstall root launchers and sudoers through their
   owning playbooks; recreate lingering, Docker startup and user units. Establish
   unattended SSH authentication without depending on this agent's session.
   Render new private path bindings instead of editing historical snapshots.
3. Establish independent access, verified SSH pins and narrowly scoped VPN
   transport. Never run two peers from the same saved identity. Either quiesce
   the old peer before restoring it or enroll a distinct peer and reconcile its
   exact policy and receiver source restrictions through the existing owners.
4. Quiesce and drain only the jobs being transferred. Preserve rollback material
   and unrelated jobs. Controller locks are local files: copying a lock path to
   another machine does not serialize writers across controllers. Maintain one
   active writer for each responsibility throughout cutover.
5. Validate input contracts and fresh managed handoff, then prove scoped TLS
   configuration/status/delivery, scheduled identity reconciliation, off-host
   collection/offline verification and monitoring. Record issuance/rotation and
   reload-failure rollback separately from unchanged delivery. Reboot the new
   controller and prove jobs, transport, keys and timers recover without an agent.
6. Select the new controller, resume its scoped jobs, and verify fresh external
   access. Retire old jobs, obsolete peers and excess credentials only after the
   new path passes. Keep a single-writer rollback procedure and preserve failed
   attempts separately from successful acceptance.

The accepted VM225 restore/reboot does not establish controller replacement.
Existing controller duties remain on the current host until the separate
migration is accepted. Reusing the accepted host point does not require
another VM225 reinstall merely to document or migrate controller responsibilities.

Use [managed workflow](deployment-workflow.md), [input contracts](lab-inputs.md),
[inventory handoff](lab-inventory.md), [controller TLS](controller-tls.md),
[renewal transport](controller-renewal-peer.md) and
[identity admission](../ansible/playbooks/roles/identity_admission/README.md) as the
existing implementation owners. Replacement-controller execution remains tracked
in [#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77).
