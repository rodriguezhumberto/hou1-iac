# Infrastructure OIDC integration test plan

> Original integration plan. Results and accepted limits are linked from [production operations](production-oidc-runbook.md); the fixed-fixture [live security suite](live-security-tests.md) remains test-only.

Run the harness only with isolated test Authentik and NetBird clients,
users/groups, domains, certificates, and SOPS/age secret references:

```bash
export OIDC_TEST_ENVIRONMENT=test
export OIDC_TEST_CLIENT_ID=netbird-test
export OIDC_TEST_CERT_REF=cert-test-2026-11-06
export OIDC_TEST_SECRET_REF=sops-age-test
python3 scripts/oidc-integration-harness.py \
  --issuer https://auth-test.example.com/application/o/netbird-test/ \
  --audience netbird-test \
  --netbird-url https://netbird-test.example.com \
  --report /var/tmp/oidc-test-evidence.json \
  --outage-evidence /var/tmp/outage.txt \
  --restore-evidence /var/tmp/restore.txt \
  --rollback-evidence /var/tmp/rollback.txt
```

The harness exercises a synthetic local validator. Its live requests cover
only discovery/JWKS and API reachability. Neither that matrix nor a nonempty
drill file establishes security readiness; files are labeled present/unverified.

Use [the live security probes](live-security-tests.md) for deployed callback,
claim, signature, PKCE, session, rotation and NetBird assertions. The mirrored
[2026-11-06 evidence](live-security-evidence-2026-11-06.md) records observed
results and gaps. Prior drills retain their original assertions, refs and hashes;
do not infer a passed drill from file size.

The JSON report contains no tokens or secret values. Attach it to issues #26
and #37 with immutable refs, certificate/secret reference IDs, test users and
groups, start/end times, and accepted gaps. A report with production hostnames,
clients, or secret references is rejected before any live request.

The follow-up in `scripts/live_oidc/jwks_overlap.py` measures the deployed
15-minute JWKS cache, controlled public-key overlap, key withdrawal and failed
refresh. `revocation_boundary.py` distinguishes blocked fresh login from
revocation of existing sessions after IdP disable/group changes. Use the
follow-up instructions in [the execution notes](live-security-tests.md),
including local recovery timers and exact ingress restoration.

The coordinated [2026-11-07 acceptance report](oidc-security-acceptance-2026-11-07.md)
records the current-release signed-token/callback matrix, real outage, fresh
application-state restore, image rollback and final isolation checks. It keeps
earlier CLI/peer and real-time JWKS evidence under their original runtime refs.
