# Live JWKS and revocation follow-up — 2026-11-07

Coordinated infrastructure #26 and staff #37 checks used the existing isolated
VPS, test Authentik staff client, internal admin/reader fixtures, test DNS and
certificates, and controller SOPS/age secrets. NetBird remains on 0.82.0.
No production deployment, human Google membership, or application behavior
was changed. See the [mirrored assertions](evidence/live-security-followup-2026-11-07.json)
for runtime image identities, source hashes, receipts and cleanup evidence.

## Deployed JWKS lifecycle

The installed Authentik 2025.10.4 publishes only its selected signing key.
Switching to a generated RSA key published that key alone; restoring the
original key restored its native JWKS. This is a measured limitation of the
current fixture, not a passing native overlap window.

An unpublished temporary container supplied a union of those two real public
keys only at the test staff JWKS path. It handled no tokens or private keys.
The deployed staff app performed real authorization-code/PKCE exchanges with
Authentik throughout; its process and clock were unchanged.

| Check | Observed result |
| --- | --- |
| New signing `kid` with an old warm cache | Refresh fetches both keys; protected read 200 |
| Old and new keys during the controlled overlap | Both logins pass; no additional JWKS fetch |
| Old key withdrawn from upstream, cache still warm | Old-key login remains 200; no additional fetch |
| Natural 900-second cache lifetime | Waited 906.07 seconds; no intervening refresh or app restart |
| Expired cache and JWKS 503 | Callback denied, no session created, protected read 401; failed JWKS request recorded |
| Old key absent after successful refresh | Fresh old-key token rejected; no session, protected read 401 |
| Current key after JWKS recovery | Login, protected read 200, replay rejection and logout revocation pass |

Withdrawal of a key from JWKS does not immediately remove it from a warm cache.
The app may trust that public key for up to 15 minutes, subject to normal token
validation. Existing opaque app sessions are a separate revocation concern.
The temporary union verifies consumer overlap handling; it does not add overlap
support to Authentik or establish uninterrupted native rotation for in-flight
old-key tokens. Keep the native rotation procedure as an acceptance finding.

## IdP changes versus app-session revocation

Only the internal admin fixture's active flag and staff groups were changed.
Its original state was restored in `finally`, with an independent five-minute
host recovery timer available if controller access failed.

| Check | Observed result |
| --- | --- |
| Disable fixture in Authentik | Fresh login denied; two existing app sessions still return 200 |
| Explicitly revoke that app subject | Both retained cookies return 401; separate reader control remains 200 |
| Remove fixture's staff groups | Fresh callback denied with no session, protected API 401; existing session still returns 200 |
| Explicitly revoke after group removal | Retained cookie returns 401; reader control remains 200 |
| Restore fixture's active state and groups | Admin and reader logins pass again |

Authentik disable/group removal is not automatic app-session revocation.
Offboarding must explicitly invalidate the app subject as well as disable the
identity/remove its roles. Otherwise sessions retain their group snapshot until
logout, explicit revocation, 30-minute idle expiry, or eight-hour absolute
expiry. The tested invalidation uses the private SQLite helper matching
`revokeStaffSubject`; it does not prove automatic back-channel logout, an
operator-facing revocation UI, or a cross-service offboarding transaction.
This remains an explicit acceptance finding in #26/#37.

## Regression coverage and cleanup

- All 16 real signed-claim cases passed again, including issuer, audience,
  authorized party, expiry/activation times, group shape and clock skew.
- All eight callback/authorization cases passed again, covering state/cookies,
  nonce, PKCE, real signature corruption, redirect URI and reader write denial.
  Positive flows also assert callback replay rejection and logout revocation.
- The security-branch infrastructure suite ran 92 tests: 91 passed and one
  `dnsmasq` controller fixture was skipped. The staff suite passed all 80 tests,
  and the app build passed. All four CI playbook syntax checks, Python
  compilation and whitespace checks passed.
- The new validator regression uses real RSA signatures and a controlled local
  clock. The deployed cache drill above separately waits real elapsed time.
  HTTP fixture tests verify the exact test-only JWKS path, key retirement,
  outage responses, public-only receipts, and refusal of token/other-client paths.
- Original provider mappings/key and fixture state were restored. Temporary
  scopes, signing key, proxies, helpers and private browser artifacts were removed.
  Ingress restoration is byte-exact, SHA256
  `886ab5f9e47e9771e2ad49dfa22d1fb0d414d1ff3a327556d37d3c74d059f1b7`.

Final admin/reader and NetBird logins passed after cleanup, with replay and
logout checks intact. Staff login and API remain 403 outside the VPN. All seven
original services are running with their declared health checks passing.
Rehearsal monitoring resumed successfully: all 33 rules healthy, zero pending
alerts. The audit and execution notes are recorded in the JSON assertions.
These checks do not repeat restore, rollback, sustained-load or provider-console
drills. Historical evidence retains its original refs and limits. Production
approval and the paired rollout issues remain separate gates; #26/#37 stay
open for coordinated acceptance of the measured limitations.
