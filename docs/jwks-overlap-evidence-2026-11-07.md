# JWKS overlap gate evidence — 2026-11-07

The isolated test staff application completed the real cache-lifetime gate on
the test VPS. Authentik 2025.10.4 itself publishes one selected signing key; the
overlap was supplied only by a temporary private Docker-network fixture serving
the two real Authentik public keys at the exact test staff JWKS URL. The fixture
handled no tokens, codes, cookies, or private key material.

- Cache warm-up fetched the old and new public keys together.
- Old-key and new-key logins both returned protected HTTP 200 without another
  JWKS fetch.
- Removing the old key upstream still allowed the old key while the cache was
  warm.
- The real elapsed cache interval was 905 seconds. The app was not restarted
  and the clock was not changed.
- A JWKS 503 after expiry failed closed: callback rejected, no session created,
  protected API returned 401.
- After refresh, the withdrawn old key was rejected with 401 and the current key
  recovered with protected HTTP 200. Callback replay and logout revocation also
  passed.
- Ingress restoration was byte exact, with SHA256
  `886ab5f9e47e9771e2ad49dfa22d1fb0d414d1ff3a327556d37d3c74d059f1b7`. The
  temporary proxy and generated key were removed.

The result proves the staff consumer's overlap and retirement behavior. It does
not establish native Authentik dual-key publication; native rotation remains a
provider limitation and must use the documented cache window. Recovery now uses
the Authentik container-local bootstrap token path and independently restores
ingress if the provider is unavailable. The monitor was paused during the timed
window and restored afterward with 33 healthy rules and no pending alerts.

The [mirrored JSON evidence](evidence/jwks-overlap-gate-2026-11-07.json) records
the eight assertions, public-key request receipts, runtime image identities and
verified cleanup. An earlier run detected an intervening refresh and was
discarded; only the controlled repeat is evidence. No NetBird provider key,
NetBird release, production host, or real-user state was changed.
