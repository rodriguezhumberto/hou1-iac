# NetBird operations

Use the private production inventory and pinned SSH keys described in the
[production runbook](production-oidc-runbook.md). NetBird remains at 0.82.0.
Management actions require an authorized NetBird owner/administrator account.

## Users, groups and devices

At the configured NetBird dashboard, open **Users**, select a user and edit their
automatic groups. `staff` gives their devices staff-network connectivity. Group
propagation is enabled for existing devices; verify the resulting memberships
under **Peers**. Users must first pass the encrypted Google allowlist and sign in.

For durable staff onboarding, update encrypted `staff_users` assignments and run
[admission reconciliation](authentik-bootstrap.md#google-admission-and-staffvpn-assignments)
after first login. It assigns the Authentik staff role and NetBird `staff`
auto-group, preserving unrelated memberships. GUI-only changes are not a complete
recovery record. Staff application roles and Grafana roles are separate grants.

Install NetBird on each personal device, select the same management server and
sign in with the same Google account. Each device becomes a separate owned peer
with its own VPN address. Use interactive sign-in rather than setup keys for
personal devices; never copy `/etc/netbird` between running machines.

Assign `administrators` to each explicitly approved **device**, rather than as a
user-wide automatic group. This enables SSH network access, not dashboard admin
rights or an operating-system login. SSH still requires an authorized OS key.

| Source | Destination | Traffic |
| --- | --- | --- |
| `administrators` | Managed server groups | TCP 22, 443 |
| `staff` | Managed server groups | TCP 443 |
| Both | Production private DNS | TCP/UDP 1053 |

All-to-All is disabled. Monitoring, backup and renewal peers have separate narrow
policies; do not add them to `administrators`. Keep NetBird DNS enabled. Private
service names must resolve through the VPN; see the production runbook for the
verified macOS Private Relay workaround.

## Credentials and addresses

Inventory supplies server VPN addresses; other peers receive automatic addresses.
Enrollment rejects conflicts and reserved addresses. Reconcile client enrollment
with `hou1.yml --tags netbird`; rerun `netbird-dns.yml` after server address changes.

The encrypted API-token document binds `netbird_management_url` to its
`netbird_api_token`. Preserve an owner-recoverable copy off-host. Replace expired
PATs through the applicable SOPS/Vault workflow; do not reset the owner or delete
server state. A PAT permits API operations, not dashboard sign-in.

## Recovery

Initial SSH cutover arms a 180-second public-SSH fallback timer. A successful
fresh VPN SSH probe cancels and removes it. Established hosts have no automatic
public SSH fallback; their listener is VPN-only and SSH password login is disabled.

Provider-console authentication is separate and is not disabled by SSH hardening.
Verify independent provider console or rescue access on both nodes before changing
the shared control plane. Repair through an existing VPN SSH session when possible;
use [break-glass recovery](netbird-break-glass.md) when identity access fails.

For backups and a lost server, follow the [unified backup protocol](hou4-backup-protocol.md)
and [production recovery](production-host-recovery.md). The old NetBird-only
archive is not a complete recovery set. Never run two copies of a server identity
or restore old authorization without reconciling later revocations.
