# Documentation

Use the operational guides below for current procedures. Dated evidence and
rehearsal journals record results at a particular revision; they are not current
deployment instructions. The [documentation review](documentation-review.md)
lists corrected conflicts and remaining implementation limits.

## Operations

| Task | Guide |
| --- | --- |
| Release, production acceptance, rollback | [Production runbook](production-oidc-runbook.md) |
| Select and deploy a CI image | [Application images](application-images.md), [release policy](releases.md) |
| Replace a lost production host | [Production recovery](production-host-recovery.md) |
| Capture, collect and retain recovery points | [Backup protocol](hou4-backup-protocol.md) |
| Manage users and device access | [NetBird operations](netbird-operations.md), [identity admission](authentik-bootstrap.md#google-admission-and-staffvpn-assignments) |
| Revoke a user's access and sessions | [Operator offboarding](operator-offboarding.md) |
| Repair identity/VPN access | [Break-glass recovery](netbird-break-glass.md) |
| Renew certificates and recover the controller | [Controller TLS](controller-tls.md), [scoped renewal peer](controller-renewal-peer.md) |
| Configure monitoring and SSO | [Environment monitoring](environment-monitoring.md), [Grafana SSO](grafana-sso.md) |
| Inspect requests or synthetic login failures | [Request metrics](request-login-monitoring.md), [staff probe](staff-login-probe.md) |
| Handle secrets | [SOPS/age](sops-age-secrets.md) |
| Configure websites and storage | [Website routing](website-routing.md), [CMS storage](cms-storage-backups.md) |

## Design and setup references

[Architecture](oidc-architecture.md), [Authentik Compose](authentik-compose.md),
[capacity/firewall gate](authentik-host-policy.md), [ingress](authentik-ingress.md),
[identity bootstrap](authentik-bootstrap.md) and
[NetBird compatibility](netbird-authentik-compatibility.md) explain the contracts.
The [legacy issuer cutover](netbird-authentik-cutover.md) conflicts with the
supported external-provider design; do not use it for routine provisioning.

## Test procedures and historical results

Use [clean test-host recovery](clean-host-recovery.md) and the
[isolated component drill](reproducible-recovery-drill.md) only with their test
guards intact. [Live security tests](live-security-tests.md) have fixed historical
fixture bindings and must not be pointed at production.

The [integration plan](oidc-integration-test-plan.md),
[original rollout harness](test-rollout-rehearsal.md),
[fresh-host journal](fresh-host-rehearsal.md),
[monitoring-isolation journal](rehearsal-monitoring-isolation.md) and
[VPN monitoring rehearsal](rehearsal-vpn-monitoring.md) describe earlier phases.
Their pending gates and command examples must be read in that context.

Dated Markdown reports and [evidence JSON](evidence/) remain unchanged as historical
records. The current production runbook links accepted results and follow-ups;
later success does not retroactively turn a failed attempt into a clean run.
