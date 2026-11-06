# HOU1 OIDC architecture and outage decisions

This record is the decision gate for the Authentik migration described in
infrastructure issues #16–#28 and staff-app issues #32–#39.  It names the
identities and stores that will be provisioned by later issues; a name here is
not a secret value.

## Decision

Authentik is the external OpenID Connect (OIDC) issuer for the staff app and
NetBird.  It runs on the existing HOU1 node in an isolated Compose project,
with one project and one persistent-data root per environment.  NetBird
0.82.0 remains the pinned version until the compatibility spike in #17 proves
that its external OIDC integration is sufficient.  The existing NetBird
embedded IdP remains the rollback configuration and is retained in the
configuration backup.

The test and production environments have separate hostnames, Authentik
providers, clients, users, certificates, secrets, and data roots.  A test
credential or endpoint must never be accepted by a production client, and a
production credential or endpoint must never be used by a test client.

## Trust boundaries and named identities

| Component | Test identity | Production identity | Boundary |
| --- | --- | --- | --- |
| Authentik issuer | `https://auth-test.<primary_domain>/application/o/hou1-test/` | `https://auth.<primary_domain>/application/o/hou1-prod/` | Public HTTPS through Nginx; administration is private NetBird-only |
| Staff OIDC client | `pearland-paws-staff-test` | `pearland-paws-staff-prod` | Authorization Code + PKCE; redirect and logout URIs are environment-specific |
| NetBird OIDC client | `netbird-test` | `netbird-prod` | NetBird dashboard and CLI use this client only |
| Staff application | `https://staff-test.<primary_domain>` | `https://staff.<primary_domain>` | Trusts only its environment issuer and audience |
| NetBird | `https://netbird-test.<primary_domain>` | `https://netbird.<primary_domain>` | Dashboard/API/gRPC/WebSocket ingress; management data stays on the node |

The immutable identity for a staff session is the OIDC `sub` claim.  Email is
an attribute and may change.  Authentik emits `sub`, `iss`, `aud`, `exp`,
`nbf`, `iat`, `nonce`, and `groups`; the staff client additionally requests
`openid profile email groups`, while NetBird requests the scopes required by
its 0.82.0 external-OIDC contract.  The staff audience is
`pearland-paws-staff-{environment}` and the NetBird audience is
`netbird-{environment}`.  Group-to-role mapping is explicit and default deny;
missing, unknown, or malformed groups do not grant access.

## Endpoints and protocol contracts

Each issuer's discovery document is the source of endpoint metadata.  The
expected Authentik endpoints, all under the environment-specific provider
path above, are:

| Function | Test path | Production path |
| --- | --- | --- |
| Discovery | `/.well-known/openid-configuration` | `/.well-known/openid-configuration` |
| Authorization | `/authorize/` | `/authorize/` |
| Token | `/token/` | `/token/` |
| JWKS | `/jwks/` | `/jwks/` |
| User info | `/userinfo/` | `/userinfo/` |
| Logout | `/end-session/` | `/end-session/` |
| Authentik readiness | `/-/health/ready/` (private NetBird access) | `/-/health/ready/` (private NetBird access) |
| Authentik administration | `/if/admin/` (private NetBird access) | `/if/admin/` (private NetBird access) |

The staff callback is
`https://staff-{environment}.<primary_domain>/api/staff/oidc/callback`; the
post-logout return is
`https://staff-{environment}.<primary_domain>/staff/login`.  The staff app
uses a server-generated state, nonce, and S256 PKCE verifier, validates
discovery, issuer, audience, signature, time claims, and nonce, and never
places tokens in a browser URL or log.  Session invalidation is server-side
and keyed by `sub`.

NetBird's planned redirect URIs are
`https://netbird-{environment}.<primary_domain>/nb-auth` and
`https://netbird-{environment}.<primary_domain>/nb-silent-auth`; its CLI
redirect is `http://localhost:53000/`.  The management API and gRPC endpoint
are `https://netbird-{environment}.<primary_domain>`; WebSocket upgrades use
the same Nginx host and forwarded `Host`, `X-Forwarded-Proto`, and
`X-Forwarded-For` headers.  The current embedded issuer is
`https://netbird-{environment}.<primary_domain>/oauth2`; it is retained until
#17 and is the rollback issuer.

## Named secrets and persistent stores

The values below are generated or supplied through SOPS/age in #21.  No value
is committed, baked into an image, printed in command output, or written to a
normal log.

| Secret or key | Owner and use | Persistence/backup |
| --- | --- | --- |
| `AUTHENTIK_SECRET_KEY_{TEST,PROD}` | Authentik signing and application secret key | Authentik encrypted recovery material |
| `AUTHENTIK_BOOTSTRAP_TOKEN_{TEST,PROD}` | One-time initial provisioning | SOPS/age only; rotate after bootstrap |
| `AUTHENTIK_POSTGRES_PASSWORD_{TEST,PROD}` | PostgreSQL role used by Authentik | SOPS/age and encrypted backup |
| `AUTHENTIK_SIGNING_KEY_{TEST,PROD}` | OIDC signing key material | Authentik certificates/signing store and encrypted backup |
| `STAFF_OIDC_CLIENT_SECRET_{TEST,PROD}` | Staff client overlap during rotation | SOPS/age; old and new values overlap only for the rotation window |
| `NETBIRD_OIDC_CLIENT_SECRET_{TEST,PROD}` | NetBird client credential | SOPS/age; environment-specific |
| `NETBIRD_RELAY_AUTH_SECRET`, `NETBIRD_COOKIE_ENCRYPTION_KEY`, `NETBIRD_STORE_ENCRYPTION_KEY` | Existing embedded NetBird state | `/opt/hou1-netbird/config.yaml` plus encrypted backup |
| `NETBIRD_API_PAT` | Operator/API break-glass access | Controller-side encrypted vault and off-host recovery copy |
| `STAFF_SESSION_SECRET_{TEST,PROD}` | Staff session integrity/rotation | Staff deployment secret store and encrypted backup |
| `TEST_STAFF_PASSWORD` / `PROD_STAFF_PASSWORD` | Legacy shared-password migration input only | SOPS/age during #36; removed or unusable in production after migration |
| `TEST_VAPID_PUBLIC_KEY`, `TEST_VAPID_PRIVATE_KEY`, `TEST_VAPID_SUBJECT` / production equivalents | Existing staff notification signing and delivery | Environment-specific application secret store and encrypted backup |
| `STAFF_EMERGENCY_CREDENTIAL_{TEST,PROD}` | Time-bounded emergency access | Offline recovery store; audited and rotated after use |
| `AGE_RECIPIENT_{TEST,PROD}` and offline recovery key | Encrypt/decrypt deployment and recovery artifacts | Separate environment recipient files; offline recovery key is never on the host |

Persistent stores are named independently for each environment:

* Authentik Compose project: `/opt/hou1-authentik/{test,prod}`.
* PostgreSQL data: `/var/lib/hou1-authentik/{test,prod}/postgres`.
* Authentik media, templates, certificates, generated configuration, and
  signing keys: `/var/lib/hou1-authentik/{test,prod}/{media,templates,certs,config,signing}`.
* Encrypted backups: `/var/backups/hou1-authentik/{test,prod}` with an
  off-host copy and retention from #22.
* NetBird SQLite/config/data: `/opt/hou1-netbird/{data,config.yaml,secrets.json}`
  and `/var/backups/hou1-netbird`.
* Staff application SQLite/session store: the environment's existing
  `cms_database_directory` and its application backup; it is never shared with
  Authentik PostgreSQL.

## Same-node placement gate

The same-node decision is conditional.  #19 must first verify CPU, memory,
disk, IOPS, and inode headroom for both existing NetBird/CMS workloads and
both Authentik projects.  Deployment refuses insufficient capacity.  Docker
networks must isolate Authentik, PostgreSQL, NetBird, and the public ingress;
PostgreSQL binds only to its private Compose network and has no public
listener.  UFW exposes only intended HTTPS and private NetBird administration
paths, and monitoring has thresholds for resource exhaustion, PostgreSQL,
Nginx, Authentik, NetBird, and staff login failures.

Placement is approved only after all of these checks pass:

1. #19 capacity and firewall inspection passes on the target node.
2. #18 health-gated Compose startup, bounded logs, limits, persistence, and
   test/production separation pass validation.
3. #21 proves encrypted secret rendering, restrictive ownership/mode, key
   rotation, and offline recovery decryption.
4. #22 restores PostgreSQL, Authentik media/templates/certificates/config and
   signing keys on a clean host and verifies login, discovery, JWKS, groups,
   clients, and signing-key continuity.
5. #17 proves the exact NetBird 0.82.0 images and settings against isolated
   Authentik.  A failed result blocks cutover and requires an explicitly
   approved upgrade issue.

Until the gates pass, Authentik is not placed in the production request path.

## Outage, rollback, and emergency decisions

* **Authentik outage:** Existing staff sessions remain subject to their normal
  expiry and server-side revocation policy; new login and refresh fail closed
  with a generic error.  Operators use the documented, time-bounded emergency
  credential only through the audited recovery procedure.  NetBird operators
  use `NETBIRD_API_PAT` for API operations and NetBird SSH for host repair;
  PAT access does not imply dashboard login.
* **NetBird authentication failure:** Keep the node console/host recovery
  path and restore the embedded NetBird configuration snapshot, including its
  issuer, encryption keys, SQLite state, peers, policies, addresses, and PAT.
  This returns management access without re-enrollment.  Do not run two
  copies of the same server identity.
* **Rollback trigger:** Failed discovery/JWKS, dashboard/API/CLI or peer
  checks, lost identity/state, failed restore validation, or breached outage
  thresholds aborts the cutover.  Restore the last verified Authentik and
  NetBird backups, then switch NetBird to its embedded issuer and rerun health
  checks.  Preserve the pre-cutover configuration and immutable image refs.
* **Staff emergency access:** Emergency access is separate from normal staff
  accounts, time-bounded, stored offline, rotated after use, and recorded with
  operator, reason, start/end time, and affected environment.  It does not
  bypass role checks for ordinary staff routes.

The compatibility result, backup identifier, restore-drill duration, and any
accepted gaps are recorded in the issue-linked implementation and rollout
runbooks before production approval.
