# Isolated NetBird compatibility probe

`scripts/netbird-oidc-spike.sh` tests the pinned [external-provider contract](../netbird-authentik-compatibility.md).
Run it only against isolated Authentik/NetBird test services; it has no inventory
guard that makes an arbitrary destination safe.

## Inputs and execution

Use an isolated Authentik provider with the selected profile's exact issuer,
`openid profile email groups`, user-ID subject mode and published RSA/EC signing
key. Its client must support the test authorization flow and the exact callbacks
`https://<netbird-host>/nb-auth`, `https://<netbird-host>/nb-silent-auth` and
`http://localhost:53000/`. Keep test clients and secrets separate from production.

Load `AUTHENTIK_CLIENT_SECRET` and `NETBIRD_PAT` from protected test inputs into
the runner environment without putting values in shell history. From the
repository root, with `curl`, `jq`, `openssl`, Docker Compose and both pinned
images available:

```bash
NETBIRD_URL=https://netbird-test.example.invalid \
AUTHENTIK_ISSUER=https://auth-test.example.invalid/application/o/netbird-test/ \
AUTHENTIK_CLIENT_ID=test-client-id \
./scripts/netbird-oidc-spike.sh
```

The default run does not change remote provider configuration. It checks exact
image tags, discovery/JWKS, PKCE authorization initiation and PAT API access;
it does not complete an authenticated browser exchange. `SPIKE_APPLY_IDP=1`
adds a provider create/delete round trip. After failure, inspect for the created
`spike-authentik-0-82-0` provider and remove only that test object.

`grpcurl` and `websocat` enable optional probes. The script uses insecure TLS for
those probes; its WebSocket result records an attempted connection and ignores
the exit status. Neither establishes trusted TLS or a successful full protocol
exchange. Record skips and perform those checks independently.

## Complete the live checks

1. Sign into the dashboard, verify the `/nb-auth` return, sign out and back in,
   then reject a stale callback code.
2. Use the pinned CLI client with its `http://localhost:53000/` callback. Verify
   status and a peer connection.
3. Verify an authenticated management API call and rejection of invalid/expired
   bearer tokens. Retain response status rather than tokens.
4. Verify trusted TLS plus expected gRPC and WebSocket behavior; record the
   actual response and close reason rather than the script's attempt marker.
5. Stop Authentik. Prove existing PAT API access and private SSH still work while
   new Authentik-backed dashboard login fails closed. Restore Authentik and repeat
   login/discovery.

Record exact image digests, fixture IDs, UTC times, results and skips under
[historical evidence](../evidence). Keep credentials and browser artifacts private.
