# Identity and access architecture

HOU1 hosts the application, Nginx, Authentik/PostgreSQL and the NetBird control
plane in Compose projects. HOU4 hosts monitoring and backup collection. Host
security, NetBird clients and systemd jobs remain OS services. Application images
are CI-built and deployed by digest. See [production operations](production-oidc-runbook.md)
for current evidence and the remaining recovery tests.

## Identity boundaries

| Service | Identity / authorization |
| --- | --- |
| Staff application | Authentik OIDC, signed subject and explicit staff-role groups |
| Grafana | Dedicated Authentik OIDC client and monitoring groups |
| NetBird 0.82.0 | Embedded issuer retained; Authentik is an external identity provider |
| SSH | Approved device network access plus an authorized operating-system key |

Google verifies upstream identity. Authentik admits active users through a
verified-email allowlist and assigns application roles separately. A Google
login or NetBird group alone does not grant a staff/Grafana role. Use
[admission reconciliation](authentik-bootstrap.md#google-admission-and-staffvpn-assignments)
for onboarding and [audited offboarding](operator-offboarding.md) for coordinated
revocation across sessions and devices.

Staff sessions bind to OIDC `sub`, not mutable email. Authorization Code + S256
PKCE uses server-side state/nonce, exact issuer/audience, signature and time-claim
validation. Missing or malformed role claims deny access. Each client has its own
callback/logout allowlist. Read authorization/token/userinfo/logout URLs from
discovery; they are not necessarily children of the per-provider issuer path.

The pinned NetBird combined server retains its own `/oauth2` issuer and registers
Authentik through `/api/identity-providers`. A direct Authentik replacement of
`server.auth.issuer` is not the supported 0.82.0 design. The
[compatibility guide](netbird-authentik-compatibility.md) explains this; the
[legacy cutover helper](netbird-authentik-cutover.md) still conflicts with it.

Client, application, role-group and scope names come from encrypted naming
bundles. Public hostnames, origins, Compose selectors and callback lists are
explicit inventory/release inputs. Test and production use separate clients,
secrets, networks and persistent roots; do not infer one from the other's name.

## Network and storage boundaries

Public HTTPS serves the website and identity/VPN enrollment. Staff, monitoring,
SSH and metrics require VPN access. Authentik sign-in is public, including its
flow executor; administration and the remaining API require the private route.
See [ingress](authentik-ingress.md) for split DNS and Private Relay behavior.

PostgreSQL uses an internal Compose network without a published port. Authentik
and NetBird have separate ingress networks and environment-specific roots.
[Placement checks](authentik-host-policy.md) validate capacity and firewall policy
before changes. Resource pressure blocks progression even when minimum RAM fits.

The [unified recovery set](hou4-backup-protocol.md) contains PostgreSQL SQL,
application SQLite/media, identity/VPN state, runtime configuration and immutable
source/image metadata. The offline owner age key stays off both nodes. HOU4's
transport key cannot decrypt backups. The scoped renewal peer can only reach its
fixed certificate/status gateways, not administer hosts.

## Failure and recovery

Existing staff sessions have their own expiry/revocation policy; Authentik failure
blocks new SSO. NetBird API PATs are independent of dashboard login but do not
repair a broken VPN transport. Preserve independent provider-console/rescue access
and separate local emergency identities.

Use [production recovery](production-host-recovery.md) for a lost host. Revoke
restored sessions and reconcile access changes newer than the point before
opening ingress. Never run two copies of the same VPN identity. A code or
configuration rollback does not undo data or external-provider changes. After
website cutover, reconcile new writes before reopening the old application.

Independent renewal-controller execution and uninterrupted production replacement
remain [#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77) and
[#78](https://github.com/rodriguezhumberto/hou1-iac/issues/78). Secret consolidation
remains [#45](https://github.com/rodriguezhumberto/hou1-iac/issues/45).
