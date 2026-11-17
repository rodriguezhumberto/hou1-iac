# Development map

Use [CONTRIBUTING.md](../CONTRIBUTING.md) for setup and local checks.
The [cleanup plan](cleanup-plan.md) records the work order and design choices.

## Supported entry points

| Operation | Entry point | Current procedure |
| --- | --- | --- |
| Update the app and host configuration | `ansible/playbooks/hou1.yml` | [Production runbook](production-oidc-runbook.md) |
| Initialize public ingress and VPN control plane | `ansible/playbooks/bootstrap-control-plane.yml` | [Production runbook](production-oidc-runbook.md) |
| Configure identity | `ansible/playbooks/authentik.yml` | [Identity bootstrap](authentik-bootstrap.md) |
| Configure VPN control plane and DNS | `ansible/playbooks/netbird-control-plane.yml`, `netbird-dns.yml` | [NetBird operations](netbird-operations.md) |
| Export private monitoring metrics | `ansible/playbooks/monitoring.yml` | [Monitoring](environment-monitoring.md) |
| Prepare off-host backups | `ansible/playbooks/backups.yml` | [Backup protocol](hou4-backup-protocol.md) |
| Restore and publish a replacement host | `ansible/playbooks/restore-production-host.yml`, `publish-production-recovery.yml` | [Recovery](production-host-recovery.md) |
| Revoke user access | `scripts/offboard-user.py` | [Offboarding](operator-offboarding.md) |
| Renew controller-managed TLS | `scripts/controller-tls.py` | [Controller TLS](controller-tls.md) |

The [documentation index](README.md) identifies test-only tools and historical
procedures. The legacy direct-issuer NetBird cutover is outside routine provisioning.

## Execution locations

- The controller runs Ansible, decrypts deployment inputs, validates release
  artifacts and manages TLS issuance and renewal. Local tests use disposable fixtures.
- HOU1 runs OS security, the NetBird client, application/Nginx, Authentik/PostgreSQL
  and the NetBird server/dashboard. Host jobs collect metrics and prepare backups.
- HOU4 has its own repository. It runs monitoring, synthetic login checks and
  restricted collection of HOU1's encrypted recovery points.

## Configuration sources and outputs

| Location | Responsibility |
| --- | --- |
| Role `defaults/main.yml` | Component defaults for roles that declare them |
| Inventory and `group_vars` | Host groups, environment settings and shared defaults; some defaults still overlap role defaults |
| SOPS inventory and component bundles | Encrypted deployment inputs with environment or job-specific access |
| Private release-input file | Selected image/revision and current deployment overrides, including paths, networking and integrations |
| Generated host configuration | Compose, Nginx, service environments and host-job configuration rendered from inputs |

Treat generated `.env` files as outputs. Change their declared inputs and render
them through the owning role rather than editing or synchronizing runtime copies.
Private `-e @file` inputs override inventory; keep their source explicit.
The [SOPS/age guide](sops-age-secrets.md) documents keys, bundles and recovery access.
