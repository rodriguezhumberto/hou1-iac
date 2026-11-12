# Legacy NetBird issuer-cutover helper

**Do not use `netbird-authentik-cutover.yml` for routine provisioning.** Its
`config-authentik.yaml.j2` replaces `server.auth.issuer` directly, while the pinned
NetBird 0.82.0 [compatibility contract](netbird-authentik-compatibility.md) requires
the embedded issuer plus an Authentik external provider. The helper still exists;
this documentation cleanup does not change or validate that implementation.

Current production uses the external-provider/admission workflow. See
[identity admission](authentik-bootstrap.md#google-admission-and-staffvpn-assignments).
That command verifies an existing connector; it does not create a missing one.
Do not set a compatibility flag merely to bypass the legacy helper's guard.

The legacy helper snapshots configuration and state, swaps config/dashboard
files and runs limited readiness checks. Those checks do not establish a human
SSO login or the supported external-provider contract.

`authentik-rollback.sh <reviewed-snapshot-path>` restores only `config.yaml` and
`dashboard.env`, then restarts services. It does not import SQLite or remove an
external-provider registration. With no argument it selects the newest cutover
directory; that is not necessarily the correct incident snapshot. Preserve live
state and review an explicit path before an operator-approved rollback.

Removing or replacing these legacy entry points requires a separate code change.
Until then, follow [production recovery](production-host-recovery.md) for a lost
host and [break-glass guidance](netbird-break-glass.md) for an access incident.
