# Identity and failure-matrix acceptance — 2026-11-23

The owner confirmed Google sign-in, staff sign-in and SSH from the enrolled Mac.
Declared lab permissions are application admin, NetBird admin and administrator
VPN access. The production-only base account is excluded from lab admission.
Production was unchanged.

## Results

The maintained matrix passed at HOU1 `a7a95cb` and HOU4 `3d0ae0f`:

| Interruption | Verified recovery |
| --- | --- |
| Application backend | Scoped reconciliation returned it to readiness. |
| HOU4 Prometheus | Monitoring reconciliation restored readiness. |
| Authentik | HOU4 remained locally ready and reported the failed remote probe; its normal monitoring playbook completed with zero changes. |
| Backup transfer | The collector retried after its SSH child was killed with 1 MiB received; prior verified bytes survived, partial data stayed unpublished and staging was removed. |

New point `20261123T154423Z-e3eae83bc7c9` was collected on HOU4 and passed offline
decryption and integrity checks. All eight HOU1/six HOU4 container IDs and both
boot identities were preserved. Google admission policies, branding and the
Mac's inherited administrator group passed readback. The local emergency owner
remained available. Ten offline matrix tests passed.

## Boundaries and follow-up

The first invocation stopped before outages because the original HOU4 checkout
had an uncommitted Nginx image edit. That edit was preserved; a clean checkout at
the accepted revision and newly rendered private inventories supplied the run.
The failed admission and successful matrix have separate receipts.

Lab identity and TLS controller timers were temporarily paused to exclude
concurrent writers, then restored. After an initial stale container metric,
normal scheduled collection recovered without repair: eight scrape targets were
healthy and all twenty alert rules inactive. This run does not prove scheduled
identity reconciliation during an outage.

The Mac was offline afterward, with its last contact before the successful run;
automatic client reconnection is not established. A fresh empty-host replay with
the final identity inputs, controller reboot/replacement, certificate rotation
and reload-failure rollback remain separate acceptance work. No reboot, reinstall,
provider change or production deployment ran here.

Private inputs and receipts:
`/home/dev/.ssh/hou-lab/external-acceptance/20261123T153207Z-identity-failure-matrix/`.
