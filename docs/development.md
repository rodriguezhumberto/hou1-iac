# Development map

Use [CONTRIBUTING.md](../CONTRIBUTING.md) for setup and local checks.
The [cleanup plan](cleanup-plan.md) records the work order and design choices.

## Supported entry points

| Operation | Entry point | Current procedure |
| --- | --- | --- |
| Update the app and host configuration | `ansible/playbooks/hou1.yml` | [Production runbook](production-oidc-runbook.md) |
| Coordinate existing HOU1/HOU4 test hosts | `scripts/deploy-lab.py` | [Managed lab workflow](deployment-workflow.md) |
| Finalize a published lab restore and verify off-host recovery | `scripts/deploy-lab.py --phase finalize` | [Finalization](deployment-workflow.md#finalize-a-published-restore) |
| Check lab release, identity and backup inputs locally | `scripts/deploy-lab.py --phase inputs` | [Input contracts](lab-inputs.md) |
| Generate private inventories and verify managed access | `scripts/prepare-lab.py` | [Inventory generation](lab-inventory.md) |
| Bootstrap empty isolated VMs from frozen inputs | Existing rehearsal and host playbooks | [Fresh lab bootstrap](probes/lab-bootstrap.md) |
| Run commands through isolated bootstrap or VPN transport | `scripts/lab-network.py` | [Controller transport](deployment-workflow.md#isolated-controller-transport) |
| Prepare isolated host/container DNS | `ansible/playbooks/lab-dns.yml` | [Lab DNS](lab-dns.md) |
| Initialize public ingress and VPN control plane | `ansible/playbooks/bootstrap-control-plane.yml` | [Production runbook](production-oidc-runbook.md) |
| Upgrade installed OS packages | `ansible/playbooks/os-maintenance.yml` | [OS maintenance](os-maintenance.md) |
| Configure identity | `ansible/playbooks/authentik.yml` | [Identity bootstrap](authentik-bootstrap.md) |
| Configure VPN control plane and DNS | `ansible/playbooks/netbird-control-plane.yml`, `netbird-dns.yml` | [NetBird operations](netbird-operations.md) |
| Export private monitoring metrics | `ansible/playbooks/monitoring.yml` | [Monitoring](environment-monitoring.md) |
| Prepare off-host backups | `ansible/playbooks/backups.yml` | [Backup protocol](hou4-backup-protocol.md) |
| Restore and publish a replacement host | `ansible/playbooks/restore-production-host.yml`, `publish-production-recovery.yml` | [Recovery](production-host-recovery.md) |
| Revoke user access | `scripts/offboard-user.py` | [Offboarding](operator-offboarding.md) |
| Renew controller-managed TLS | `scripts/controller-tls.py` | [Controller TLS](controller-tls.md) |

The [documentation index](README.md) identifies test-only tools and historical
procedures. The legacy direct-issuer NetBird cutover is outside routine provisioning.

## Environment targeting

The isolated HOU1/HOU4 lab uses a privately configured namespace, separate hosts
and a separate NetBird account. See the [recorded deployment](evidence/isolated-lab-2026-11-17.md).
The default `hou1_test_init` and `hou1_test` groups remain empty; select lab hosts
through private inventories and independently verified SSH keys. The former test
deployment is production. Keep production hosts in `prod_env`; rehearsal hosts may use the
existing `isolated_test_vps` group instead of `test_env`.

Managed-host entry points run `tasks/validate-target.yml` before connections or
controller changes, including component-tag runs. It binds inventory membership
to `cms_environment`, NetBird server group, identity profile and connection
address. CMS uses `production`; Authentik and `deploy_environment` use `prod`.
Production Authentik entry points require matching explicit identity inputs.
Both repositories enable SSH host-key checking; verify keys before bootstrap or
replacement, and retain them for public and VPN addresses.

Direct SSH tools use [explicit address and host-key checks](probes/rehearsal-targets.md)
through `scripts/ssh_target.py`. Old fixture domains, containers and paths still
need review before use with a newly provisioned test environment.

## Execution locations

- The controller runs Ansible, decrypts deployment inputs, validates release
  artifacts and manages TLS issuance and renewal. Local tests use disposable fixtures.
- HOU1 runs OS security, the NetBird client, application/Nginx, Authentik/PostgreSQL
  and the NetBird server/dashboard. Host jobs collect metrics and prepare backups.
- HOU4 has its own repository. It runs monitoring, synthetic login checks and
  restricted collection of HOU1's encrypted recovery points.

The [webstack](../ansible/playbooks/roles/webstack/README.md) and
[Authentik](../ansible/playbooks/roles/authentik/README.md) role guides describe
task phases, required inputs and owned resources.
[Compose integration](../ansible/playbooks/roles/app_compose/README.md) owns the
shared override; [shared ingress](../ansible/playbooks/roles/shared_ingress/README.md)
prepares its networks and mounts; [activation](../ansible/playbooks/roles/compose_runtime/README.md)
applies changed service inputs and retains failed attempts for retry.
The [test source owner](../ansible/playbooks/roles/test_application_source/README.md)
refreshes the isolated VM's local Git remote from the selected release bundle.

## Configuration sources and outputs

| Location | Responsibility |
| --- | --- |
| Role `defaults/main.yml` | Component defaults owned by that role, such as image versions, resource limits and backup schedules |
| Inventory and `group_vars` | Host groups, environment settings, shared paths, network topology and identity profiles |
| SOPS inventory and component bundles | Encrypted deployment inputs with environment or job-specific access |
| Private release-input file | Selected image/revision and current deployment overrides, including paths, networking and integrations |
| `app_source_directory` | Verified Git source, separate from the stable `app_directory` runtime root |
| Generated host configuration | Compose, Nginx, service environments and host-job configuration rendered from inputs |

Treat generated `.env` files as outputs. Change their declared inputs and render
them through the owning role rather than editing or synchronizing runtime copies.
Inventory overrides role defaults; private `-e @file` inputs override inventory.
Keep shared paths and topology in inventory because recovery and standalone
playbooks consume them without running the application roles.
The [SOPS/age guide](sops-age-secrets.md) documents keys, bundles and recovery access.

## Reproducibility work

The [intervention audit](deployment-interventions.md) assigns the lab's manual
steps to code, private inputs or external prerequisites. The
[deployment workflow](deployment-workflow.md) describes the supported automation.
The [fresh-host result](evidence/lab-bootstrap-2026-11-19.md) records bootstrap
and repeat checks. [Capture coordination](evidence/capture-coordination-2026-11-19.md)
records the subsequent deployment/backup exclusion and controller-loss checks.
Keep reusable logic in Git and private values outside it.
`scripts/lab_finalize.py` orders restore finalization through component playbooks;
`lab_backup_client.py` shares pinned backup transport and offline verification
with the failure matrix. Source initialization stays under admission so activating
its timer cannot interrupt the playbook; capture runs after admission is released.
The [ingress migration sequence](ingress-separation.md) records the routing,
TLS/monitoring and recovery dependencies. Independent ingress is opt-in through
`shared_ingress_independent`; the combined project remains the default.
The [application lifecycle plan](application-lifecycle-plan.md) defines portable
health, storage-recovery and shutdown behavior before further orchestration work.
