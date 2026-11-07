# Coordinated OIDC security acceptance — 2026-11-07

Staff [#37](https://github.com/rodriguezhumberto/pearland-paws/issues/37) and
infrastructure [#26](https://github.com/rodriguezhumberto/hou1-iac/issues/26)
share this test plan and the byte-identical
[assertion report](evidence/oidc-security-acceptance-2026-11-07.json).
The current-release run passed on the isolated VPS `139.177.151.12`.
This is test acceptance evidence; production rollout remains separate.

The claim matrix ran from 18:35:49 to 18:39:32 UTC. Outage and fresh restore
ran from 18:42:39 to 18:49:43 UTC; rollback, redirect and final cleanup checks
followed. The JSON records the evidence assembly time and individual drill
results. The candidate app remains running on the test VPS.

| Acceptance criterion | Evidence |
| --- | --- |
| Isolated clients, identities, domains, certificates and secrets | Test-only `pearland-paws-staff-test` client, `vps-staff-admin` / `vps-staff-reader` fixtures, separate test NetBird embedded issuer/external Authentik provider, `*-test.vps-test.pearlandpaws.com` names, TLS certificate fingerprints/SANs and private SOPS/age reference in JSON. No production credentials were used. |
| Automated assertions for listed failure modes | Current deployed claim/callback/session/NetBird probes and repository regression tests below; retained real-time JWKS and CLI/peer evidence keeps its original runtime refs. |
| Recorded outage, restore and rollback | Real Authentik stop/start, off-VPS encrypted backup restored into fresh database/configuration, reversible staff-image rollback, plus historical NetBird state rollback. Each records actual assertions and immutable artifact identities. |

| Failure mode or flow | Automated assertion and result |
| --- | --- |
| Issuer, audience, authorized party | Real Authentik-signed faults rejected by deployed callback; no session, API 401. Empty/false `azp` also rejected. |
| Signature | Corrupt one RS256 signature byte after real exchange; same claims/`kid`, callback rejected and API 401. |
| Expiry, `nbf`, `iat`, lifetime, skew | Outside-window and reversed-time tokens rejected; 30-second skew accepted within configured 60-second allowance. Recently expired fixture retains a valid issuance-to-expiry interval. |
| Missing or malformed groups | Missing, empty, string, duplicate and blank groups rejected; unmapped group gets protected API 403. |
| State, nonce, transaction cookies | Tampered state/nonce, missing or swapped cookie rejected without a staff session. |
| PKCE, redirect registration, replay | Wrong verifier gets token endpoint 400; unregistered callback rejected before code issuance; used callback/code replay rejected. |
| Roles, login/logout | Admin/reader reads 200; reader write 403; logged-out cookie gets 401. |
| Session revocation and expiry | Two sessions invalidated together; fresh login subsequently succeeds. Idle/absolute-expired test rows get real HTTP 401. Persistent operator offboarding has separate merged live evidence below. |
| Signing-key rotation and refresh | Current run: original/new/restored native keys each permit login without app restart. Retained 905-second drill: both cached public keys work, withdrawal/cache expiry and JWKS outage fail closed, current key recovers. |
| Authentik outage | Both pending callback and fresh login fail with no app session/API 401. Fresh pinned VPN SSH, independent NetBird local-owner PKCE/API login and PAT continue working. |
| Fresh application-state restore | Verified encrypted off-VPS archive restores into fresh PGDATA/configuration in **147.72 seconds**; issuer, public signing keys and hash of stable UUID/active/group bindings unchanged. Admin/reader, replay, logout and write denial pass afterward. |
| Release rollback | Retained offboarding-capable staff image passes admin/reader/replay/logout/write-denial checks; exact candidate image restored and the same assertions repeated. |
| NetBird | Current run repeats discovery/external-provider PKCE login, API 200, code replay, malformed/signature-corrupt bearer 401, wrong verifier 400, and outage-independent access. Prior device/CLI/peer and state-rollback checks remain linked under original refs. |
| Post-login return paths | Backslash and tab normalization attacks remain on staff origin through deployed HTTP callback; local regression also covers newline and protocol-relative forms. |
| Final isolation and recovery | Public staff login/API return 403, including spoofed VPN forwarding header; all seven services healthy; 33 monitor rules healthy, no firing rules or pending events. |

The current run contains **19 signed-claim** and **8 callback/authorization**
cases, plus the separately recorded session, rotation, recovery, rollback,
NetBird and return-path probes. Scripts assert the observed HTTP/session effects;
the synthetic local validator and nonempty drill files are not acceptance proof.

## Immutable source and recovery artifacts

- Running staff source: `bc2bc630831cb3f9522c0f026ebb2f2b39e63ada`.
- Candidate image: `sha256:5a6c454d9671a28e19dd9a328e1ba6ef238f9f4af73f2a049cc398890e082927`.
- Staff probe source: `855e3946f7dcea2c727150c6c475a58d50b4781d`.
  Later probe commits do not change the running application implementation.
- Infrastructure drill source: `2a4c1be9cdf448858a1ac5cef5a86ce6f5dd57ae`.
- Rollback source: `2f133f32aae5656a4dea1b2649b9af166ff16aa9`;
  retained image: `sha256:3e9053dee17dc2fdae7f2452336257fac2f018da90f0535d322d7238d940febe`.
- Restore point: `authentik-20261107T184305Z.tar.gz.age`, 6,900,656 bytes;
  SHA256 `187ce2730b722944ef6e68149367d669a8b440f301da5ce3aed1c5e1a74eda15`.
  Checksum and decryption verified off-VPS using a separate copy of the host
  recovery identity. Only the encrypted archive was uploaded back; no controller
  age identity was uploaded.

The restore worker quarantined the consistent cold original data/configuration
under root-only `/root/hou1-oidc-recovery`. A 20-minute independent host guardian
could restore it without controller SSH or the Authentik API; it was disarmed
only after acceptance assertions. Protected originals and the completed marker
remain for recovery. The release rollback used its own five-minute guardian.
No NetBird restart or version change was needed; NetBird remains **0.82.0** and
Authentik remains **2025.10.4**.

Temporary keys/mappings, proxies, RPC helpers, test probes and pending browser
files were removed. Original ingress was restored byte-for-byte, SHA256
`886ab5f9e47e9771e2ad49dfa22d1fb0d414d1ff3a327556d37d3c74d059f1b7`.
Monitoring was resumed and the supported persistent-offboarding CLI check passed.
The candidate image archive scan covered 1,483,654,144 bytes and found no known
test secrets; this exact-value check does not claim a general secret audit.
Private raw reports, passwords, allowlist emails, keys, codes, tokens and cookies
stay outside Git.

## Verification and retained evidence

Staff: **91 tests**, build and **9 integration cases** pass. Infrastructure:
**133 tests run: 132 pass**, one optional dnsmasq integration skip, all four existing
Ansible syntax checks pass. Four recovery-worker tests cover failed restore,
interrupted config recovery, late-timer safety and production rejection.
Python compilation and `git diff --check` pass.

Reproduce staff checks with `npm test`, `npm run build` and
`npm run test:integration` from `astro-app`. Infrastructure uses
`NGINX_BIN=/tmp/nginx-validator python3 -m unittest discover -s tests -v`
with the CI Nginx container wrapper and existing development dependencies;
its workflow lists the four syntax-check commands. The infrastructure
[`docs/live-security-tests.md`](https://github.com/rodriguezhumberto/hou1-iac/blob/2a4c1be9cdf448858a1ac5cef5a86ce6f5dd57ae/docs/live-security-tests.md)
describes ordered live phases, private prerequisites and independent recovery.
Do not execute those mutations outside the isolated fixture.

Retained checks are evidence from their stated runtime refs, not new runs:

- [Real 905-second JWKS overlap/cache drill](jwks-overlap-evidence-2026-11-07.md).
  Its JSON SHA256 is `f8918439ae71c4dbb29811c7abf334dd093e16228b39abb8f48fdc0df51e5478`.
- [Prior live CLI/device/peer and legacy-auth checks](live-security-evidence-2026-11-06.md).
  JSON SHA256 `853c7ff0a898de69b05fcc48fafbe9cb0ae6d0b1ce13d18f8fe755f7d9eed1c1`;
  its linked original rehearsal records NetBird state rollback/recovery.
- [Authenticated, audited operator offboarding](https://github.com/rodriguezhumberto/hou1-iac/blob/58c3bb29c782b61993dafbbacee312be1a27cda3/docs/operator-offboarding-evidence-2026-11-07.md)
  proves persistent denial of existing sessions/in-flight callbacks, retained
  NetBird token/peer removal, control-identity preservation and retry/audit
  failure handling. The session-matrix invalidation in the current report uses
  operator SQLite invalidation matching session semantics; it does not replace
  the separate supported-command evidence.

## Limits and subsequent gates

Authentik publishes one selected signing key. The retained overlap drill uses
a temporary union of the real public keys to prove consumer cache/retirement
behavior; it does not prove native dual-key publication. Native rotation must
respect this provider limitation and the documented cache window.

IdP GUI disable/group changes alone do not invalidate opaque app sessions.
Use the merged authenticated, audited offboarding coordinator for complete
revocation. Expiry checks adjust test-row timestamps rather than waiting the
full lifetime. Restore proves application-state recovery on this VPS, not
replacement-host or provider-console recovery. Historical NetBird state/peer
results are not relabeled as current-release repetitions.

Production monitoring, secret consolidation (#45 in infrastructure), storage
load, rollout/recovery (#27 / #39), final runbooks (#28 / #38) and production
approval remain separate. Neither this evidence nor merging its PRs promotes
the test deployment to production. Review both mirrored acceptance PRs before
closing #26 and #37; issue bodies remain unchanged.
