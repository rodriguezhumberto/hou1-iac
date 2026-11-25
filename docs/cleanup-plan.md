# Recovery implementation status

This is the documentation checkpoint for 2026-11-24. It summarizes recorded
results, not a fresh inspection of running hosts or a deployment instruction.
HOU1 changes are in PR #95; HOU4 has no remote and its changes stay committed
locally. The [development journal](archive/deployment-cleanup-journal.md) retains
earlier implementation decisions, failed attempts and superseded next steps.

## Implemented and accepted in the isolated lab

| Area | Recorded result |
| --- | --- |
| Managed deployment and interruption checks | [Identity-aware failure matrix](evidence/identity-failure-matrix-2026-11-23.md); capture admission serializes managed deployment, identity reconciliation and TLS delivery |
| Fresh replacement with final identity inputs | [Uninterrupted restore, publication, unchanged repeat and reboot](evidence/uninterrupted-identity-replay-2026-11-23.md), including owner-confirmed Google/staff/VPN/SSH access |
| Standalone backup finalization | [Empty-source first capture, collection and verification](evidence/standalone-first-capture-2026-11-24.md), followed by restore of that resulting point |
| Collected-point service restore | [Constrained component drill](evidence/collected-point-restore-2026-11-24.md): 187 PostgreSQL tables and 29 application/access checks |
| TLS activation and publication cleanup | [Operational failure paths](evidence/operational-failure-paths-2026-11-24.md): three TLS process-death cases and eight native UFW boundaries; Compose service calls were substitutes in the UFW test |
| Offboarding after digest-locked recovery | [Public plan/apply/retry and persistent denial](evidence/operational-failure-paths-2026-11-24.md) for Google-source-shaped users and exact reviewed image digests |
| Scheduled identity, TLS and client recovery | [Timer retry](evidence/scheduled-identity-outage-2026-11-24.md), [certificate rotation/rollback](evidence/tls-lifecycle-2026-11-24.md) and [automatic client reconnection](evidence/client-reconnection-2026-11-24.md) |

The final pre-PR checks passed source/playbook syntax, lint and 668 HOU1 tests
with no skips. HOU4's final clean check, including its matching Nginx security
pin, passed 102 tests with 14 optional native monitoring checks skipped.
GitHub validation passed for HOU1's tested code head. Later documentation edits
do not extend the recorded live-test scope.

## Remaining boundaries and follow-up

- [Controller separation](controller-responsibilities.md): assemble a current,
  complete owner-decryptable controller package, retain it off-controller, and
  prove reconstruction, scheduled operation, reboot and rollback on an independent
  controller. Existing host recovery does not establish this gate.
- [Production replacement acceptance](production-host-recovery.md): the dated
  production run required corrections. Later uninterrupted lab runs do not
  establish uninterrupted production recovery.
- Restore drills validate their selected points. Recurring service-restore
  drills, independent HOU4-loss monitoring and an additional storage copy remain
  separate work; transfer and offline integrity alone are not service restore.
- The latest targeted offboarding check had no existing IdP/staff sessions or
  owned VPN peers. Its pass does not replace those broader revocation checks.
- [Independent ingress](ingress-separation.md) remains opt-in; the combined
  application project is the default. Its migration acceptance must be recorded
  for the selected topology.
- [Release selection](releases.md) still binds source, infrastructure and image
  receipt. A source tag or merged PR does not change private inputs, old recovery
  points or a running node.

## Design constraints retained

Existing playbooks own resources and generated outputs; controller workflows
coordinate those owners. Public entry points, variables, runtime paths and
recovery formats stay compatible. Scoped encrypted inputs, environment guards,
capture ordering, independent recovery access and failure journals remain part
of the contract. Use the [intervention audit](deployment-interventions.md) for
ownership details and [evidence index](evidence/README.md) for historical results.
