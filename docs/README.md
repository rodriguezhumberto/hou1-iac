# Documentation

Use the current guides below. Commands run from the repository root unless a
guide says otherwise. [Development](development.md) maps the code and configuration;
[contributing](../CONTRIBUTING.md) covers setup and local checks.

## Operations

| Task | Guide |
| --- | --- |
| Upgrade installed OS packages | [OS maintenance](os-maintenance.md) |
| Deploy, accept or roll back a release | [Production runbook](production-oidc-runbook.md) |
| Select a CI image | [Application images](application-images.md), [release policy](releases.md) |
| Replace a lost production host | [Production recovery](production-host-recovery.md) |
| Capture and collect recovery points | [Backup protocol](hou4-backup-protocol.md) |
| Manage users and device access | [NetBird operations](netbird-operations.md), [identity admission](authentik-bootstrap.md#google-admission-and-staffvpn-assignments) |
| Revoke access and sessions | [Offboarding](operator-offboarding.md) |
| Recover identity/VPN access | [Break-glass recovery](netbird-break-glass.md) |
| Renew TLS or recover the controller | [Controller TLS](controller-tls.md), [renewal peer](controller-renewal-peer.md) |
| Configure monitoring and SSO | [Monitoring](environment-monitoring.md), [Grafana SSO](grafana-sso.md) |
| Inspect requests or synthetic logins | [Request metrics](request-login-monitoring.md), [staff probe](probes/staff-login-probe.md) |
| Manage secrets | [SOPS/age](sops-age-secrets.md) |
| Configure websites and storage | [Website routing](website-routing.md), [CMS storage](cms-storage-backups.md) |

## Design and setup

[Architecture](oidc-architecture.md), [Authentik Compose](authentik-compose.md),
[host policy](authentik-host-policy.md), [ingress](authentik-ingress.md),
[identity bootstrap](authentik-bootstrap.md) and
[NetBird compatibility](netbird-authentik-compatibility.md) describe supported contracts.
The [cleanup plan](cleanup-plan.md) records development decisions and follow-up work.
The [intervention audit](deployment-interventions.md) tracks reproducibility gaps;
the [managed lab workflow](deployment-workflow.md) coordinates existing test hosts.
[Lab DNS](lab-dns.md) owns opt-in bootstrap resolution and container DNS access.
[Input contracts](lab-inputs.md) check release, identity and backup bindings locally.
[Inventory generation](lab-inventory.md) prepares both access profiles and verifies
the managed handoff before selecting a new snapshot.
[Ingress separation](ingress-separation.md) records migration prerequisites and
acceptance order.
[Application lifecycle](application-lifecycle-plan.md) records portable health,
storage recovery and shutdown work.

## Probes and drills

[Probe procedures](probes/README.md) identify scope, prerequisites and cleanup.
Fixed-fixture security tests and recovery drills are test-only; they are separate
from production operations.

## Evidence and archive

[Evidence](evidence/README.md) contains dated Markdown reports, journals and JSON
receipts. Their results apply to the recorded revision and environment.
[Archive](archive/README.md) contains superseded plans and legacy helper notes.
