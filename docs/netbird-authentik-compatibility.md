# NetBird and Authentik compatibility

The pinned components are `netbirdio/netbird-server:0.82.0` and
`netbirdio/dashboard:v2.96.0`. NetBird's embedded Dex provider remains enabled
and issues dashboard/API tokens at `https://<netbird-host>/oauth2`.

Authentik is an external identity provider registered through
`/api/identity-providers`. Replacing `server.auth.issuer` with Authentik is not
the supported combined-server configuration. The [legacy cutover helper](archive/netbird-authentik-cutover.md)
conflicts with this contract.

## Validation

The [isolated compatibility probe](probes/netbird-oidc-spike.md) covers pinned
images, discovery/JWKS, PKCE initiation and API access. Full validation also
requires dashboard and CLI login/logout, rejected replay/invalid tokens,
gRPC and WebSocket behavior, peer connectivity and recovery. Optional probe
attempts do not establish all of these properties.

No NetBird upgrade is approved by the existing compatibility result. A failed
isolated check should identify the unsupported behavior before an upgrade is
proposed separately. Production uses the [release runbook](production-oidc-runbook.md),
not the spike or legacy cutover sequence.
