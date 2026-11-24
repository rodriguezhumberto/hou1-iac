# Scheduled identity reconciliation through an outage — 2026-11-24

Lab acceptance passed at HOU1 `00b6348` and HOU4 `3d0ae0f`, continuing from the
[accepted fresh-host recovery](uninterrupted-identity-replay-2026-11-23.md).
The controller remained on the coding-agent host; no relocation or reboot ran.

## Results

- Baseline managed verification and repeated read-only access snapshots passed.
  Preparation briefly paused the lab identity timer and renewal deployment timer;
  the original identity schedule resumed before observing the outage.
- A guest-local timer was armed before stopping only the existing lab identity
  server container. It independently started that exact container after 150
  seconds and restored the previously active source capture timer.
- The normal identity timer invoked its unchanged admission command while the
  server was stopped. Reconciliation failed and remained scheduled. After timed
  guest recovery, a later timer invocation completed real policy checks without
  a manual service start, failure reset or declaration change.
- The outage began at 06:16:53 UTC; successful scheduled reconciliation was
  observed at 06:20:07 UTC. This interval includes the deliberate outage and
  normal scheduling delay, not just service startup.
- Before/after hashes matched for identity-provider grants, Google configuration,
  branding, runtime secrets, VPN account settings, user grants, groups, policies,
  provider bindings and peer identity/grants. No new account or grant was seeded.
- All fourteen running container identities, both host boot/instance/SSH identities
  and one retained dormant ingress container were preserved. Final managed checks
  passed on both hosts. Eight monitoring targets were healthy, all twenty alert
  rules inactive and the Discord notification failure counter zero.
- Controller timers and source capture scheduling were restored. Production user
  unit, fixed launcher, executor configuration and renewal sudoers hashes matched.
  The transient guest recovery timer was disarmed after readiness returned.

## Preparation corrections and limits

The read-only access observer initially assumed string group members and omitted
NetBird's null representation for empty groups. Corrected canonical hashes then
matched repeated snapshots. The first drill preparation stopped at a controller
root metadata check inside the network wrapper's `no_new_privs` boundary. A
separate attempt moved that check to the host, then stopped at an overly strict
container count: one stopped ingress container remained from migration. Both
preparations ended before arming recovery or stopping the identity server; their
failed receipts were retained. The corrected predicate required eight healthy
running services and preserved the dormant container too.

This proves an unchanged scheduled reconciliation fails safely and later retries
through the existing timer. It does not test a newly admitted user, human client
reconnection, controller replacement/reboot or a new certificate rotation.
Application and VPN access grants were compared, not intentionally revoked.

Private evidence:
`/home/dev/.ssh/hou-lab/operational-acceptance/20261124-scheduled-identity/`.
The successful interruption and recovery are in `attempt03/invocation.json`;
earlier preparation failures remain separate.
