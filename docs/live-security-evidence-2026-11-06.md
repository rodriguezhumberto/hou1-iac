# Live VPS security evidence — 2026-11-06

Infrastructure #26 and staff #37 were tested together against isolated VPS
`139.177.151.12`. The [machine-readable assertions](evidence/live-security-2026-11-06.json)
are mirrored in both repositories. Deployment refs remain infrastructure
`8db0d2563ce314498a4a4840538fe5a1c720de81`, staff
`af9a46b3f3b7a96543481c82979fe4068bc01f4c`, image
`sha256:8dc72772a33a6ba570d9e5ecd5184001faded60bf254a4fd789cf324600c0c85`.
The app was not restarted for key rotation. NetBird remains 0.82.0 with its
embedded issuer and Authentik as an external provider.

| Live check | Observed assertion |
| --- | --- |
| Signed issuer/audience/authorized-party faults | Real Authentik tokens rejected; no session; protected API 401 |
| Expired token, future `nbf`/`iat`, excessive lifetime | Rejected; no session; protected API 401 |
| Missing/empty/string/duplicate/blank groups | Rejected; no session; protected API 401 |
| Unmapped group | Session can exist; protected API 403 |
| Clock skew | Tokens 30 seconds past expiry or before `nbf`/`iat` accepted within configured 60-second allowance |
| State tampering, absent/swapped cookie, nonce mismatch | Callback rejected; no session; protected API 401 |
| PKCE mismatch | Authentik token endpoint 400; staff callback creates no session |
| Invalid RS256 signature | One signature byte flipped after real exchange, same claims and `kid`; callback rejected; protected API 401 |
| Unregistered redirect URI | Authentik rejects before issuing a callback code |
| Signing-key rotation | Warm-cache original key, new key, original-key restoration logins pass without app restart |
| Reader authorization | Read 200; write 403 |
| Replay/logout | Callback replay rejected; original cookie gets 401 after logout |
| Subject revocation | Two real sessions invalidated together; both get 401; fresh login succeeds |
| Session idle/absolute expiry | Advance matched test-row timestamps; real protected HTTP request gets 401 |
| Legacy migration | Valid old HMAC-signed cookie gets 401; shared-password POST gets 405 |
| NetBird external-provider/API | Authentik through embedded issuer; real PKCE exchange; authenticated API 200 |
| NetBird negatives | Code replay rejected; wrong verifier 400; malformed/signature-corrupt bearer 401 |
| Fresh CLI SSO | Isolated 0.82.0 peer completes device authorization through Authentik; management/signal connected |
| VPN policy and staff authentication | Ordinary peer cannot connect to staff; temporary policy grant permits transport but staff still requires authentication (401); group removal blocks transport again |
| Final restored stack | Admin/reader/NetBird probes pass; public staff API 403; all seven original containers running, app/Auth/database healthy |

All 16 signed-claim cases passed. Receipts show claims reaching the deployed
callback, rather than treating the synthetic validator as security proof. The
fault proxy had no published port and corrupted only one armed code-hash exchange.

## Cleanup and repository checks

Original staff mappings/key matched the pre-test snapshot. Temporary mapping/key,
proxy, root-side RPC helpers, control files, pending peer flows, fresh CLI peer
and container were removed. Existing management peers and policy membership
were retained. Ingress was restored byte-for-byte (SHA256
`886ab5f9e47e9771e2ad49dfa22d1fb0d414d1ff3a327556d37d3c74d059f1b7`);
Nginx configuration validation passed.

Staff: **79 passed, zero failures**. Infrastructure's 82-test run had 80 passes,
one skipped controller-only resolver fixture and one environment failure from
missing native Nginx. That single check passed using existing
`/tmp/nginx-validator` (`nginx:1.28-alpine`) and Compose wrapper:
**81 effective passes, one skip**. Python syntax and undefined-name checks passed.
No app or deployed infrastructure implementation changed in these test PRs.

## Prior drills and limits

The earlier [same-ref rehearsal](vps-test-evidence-2026-11-06.md) recorded fresh
sign-in failing during Authentik outage, private SSH/PAT and independent local-owner
login surviving it, application-state restore in **214 s**, and embedded-only
rollback/state recovery in **73 s**. Those drills were not repeated. This report
records their assertion lines and log hashes; both off-VPS encrypted archive
checksums were reverified. Nonempty files are no longer labeled passed drills.

These issues remain open for coordinated acceptance:

- Authentik publishes only the selected signing key in this JWKS. New-key
  refresh and restoration passed; old/new overlap and old in-flight tokens
  across cache expiry were not drilled. Do not claim an overlap window.
- Subject invalidation used operator SQLite revocation matching app semantics.
  Automatic IdP logout/group-change propagation is absent; existing sessions
  require explicit app revocation or configured expiry.
- Expiry tests changed test-session timestamps rather than waiting for full
  lifetimes. Browser rendering was outside this HTTP security run.
- Production egress isolation, separate DNS API credentials, representative
  storage load, provider-console recovery, scheduled offsite retention and
  alert delivery remain deployment gates. Production promotion, prerequisite
  merges and issue #24's issuer-swapping scripts were not attempted.

See the infrastructure [execution notes](live-security-tests.md). Passwords,
private keys, raw codes, tokens and session cookies are excluded from evidence.

The [2026-11-07 follow-up](live-security-followup-2026-11-07.md) measures the
deployed 15-minute JWKS cache and IdP disable/group-removal boundaries on the
rebuilt test host. It distinguishes controlled consumer overlap from native
Authentik publication and explicit revocation from automatic IdP propagation.
