# NetBird 0.82.0 and Authentik compatibility spike

This is the compatibility gate for infrastructure issue #17. It tests the exact
images already pinned by this repository:

| component | image |
| --- | --- |
| combined server (management, signal, relay, STUN) | `netbirdio/netbird-server:0.82.0` |
| dashboard | `netbirdio/dashboard:v2.96.0` |

The spike treats Authentik as a NetBird **external identity provider**. The
combined server's embedded Dex provider stays enabled and remains the issuer
for NetBird's own dashboard/API tokens (`https://netbird.<domain>/oauth2`).
Replacing `server.auth.issuer` with an Authentik issuer is not a supported
combined-server configuration in 0.82.0: the image always builds and enables
its embedded provider. The external provider is added through the
`/api/identity-providers` API and is selected by the embedded login flow.

## Reproducible test

Create isolated test-only Authentik objects before running the spike:

* Provider issuer: `https://auth-test.<primary-domain>/application/o/netbird/`
* Client type: public for the browser authorization flow, with exact redirect
  URIs `https://netbird-test.<primary-domain>/nb-auth`,
  `https://netbird-test.<primary-domain>/nb-silent-auth`, and
  `http://localhost:53000/`.
* Scope: `openid profile email groups`; subject mode is based on the Authentik
  user ID; the signing certificate is an RSA or EC key published by JWKS.
* A separate Authentik management client and service account are used for the
  NetBird `/api/identity-providers` create/delete probe. Store its secret with
  SOPS/age and never paste it into a command line or issue comment.

Run from the repository root. The default is read-only; set
`SPIKE_APPLY_IDP=1` only against the isolated test account to exercise the
provider create/delete round trip:

```bash
NETBIRD_URL=https://netbird-test.example.invalid \
AUTHENTIK_ISSUER=https://auth-test.example.invalid/application/o/netbird/ \
AUTHENTIK_CLIENT_ID=REDACTED \
AUTHENTIK_CLIENT_SECRET=REDACTED \
NETBIRD_PAT=REDACTED \
./scripts/netbird-oidc-spike.sh
```

The script fails closed if either image is missing, if a tag differs, if
Authentik discovery does not return the exact issuer, if JWKS is empty, if the
PKCE authorization probe is rejected, or if the NetBird API is unavailable. It
never prints the PAT or client secret. Install `grpcurl` and `websocat` in the
test runner to turn the optional gRPC and WebSocket probes into required
checks; their skipped status is explicit in the output.

After the script, complete the interactive checks with the isolated test user:

1. Sign into the dashboard and verify the browser returns through `/nb-auth`.
2. Sign out and sign back in; verify the provider logout redirect returns to
   the dashboard and that a stale callback code is rejected.
3. Run the NetBird CLI with the pinned 0.82.0 client and verify its loopback
   callback on `http://localhost:53000/`, then confirm `netbird status` and a
   peer connection.
4. Call a management API endpoint with the returned bearer token and verify a
   denied/expired token is rejected. Capture the API response status, not the
   token.
5. Use `grpcurl` against the public management endpoint and a WebSocket client
   against `/ws-proxy/`; record the status and close reason.
6. Stop Authentik and verify existing NetBird API PAT and NetBird SSH access
   still work, while a new dashboard login fails closed. Restore Authentik and
   repeat discovery/JWKS and login.

Record the image digests, test client/user/domain/certificate identifiers,
UTC start/end times, each probe result, and any skipped optional probe in the
issue and the rollout evidence. Do not record secrets or tokens.

## Result and upgrade gate

The static configuration and exact-image compatibility result is **sufficient
for 0.82.0 when Authentik is an external provider and the embedded IdP is
retained**. Direct issuer replacement is rejected by the spike's configuration
review and is not part of the design. No NetBird upgrade is approved or
required by this result. A future upgrade may be proposed only if the live
isolated run above records a failed dashboard, CLI, API, gRPC, WebSocket,
PKCE, logout, or restore check; that failure must open a separate, explicitly
approved upgrade issue.

This spike is a test reference, not a current production deployment sequence.
The accepted production result is linked from the [production runbook](production-oidc-runbook.md).
The [legacy direct-issuer cutover helper](netbird-authentik-cutover.md) still
conflicts with this external-provider contract and must not be treated as a
compatible next step.
