# Infrastructure OIDC integration test plan

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

The automated matrix asserts invalid issuer, audience, signature, expiry,
`nbf`, state replay, nonce, PKCE, JWKS rotation, missing groups, clock skew,
and revocation. Live checks validate Authentik discovery and JWKS plus NetBird
API reachability; the test operator also records dashboard, CLI, gRPC,
WebSocket, and peer connectivity in the NetBird evidence files. The outage
evidence comes from the controlled Authentik outage and PAT/SSH recovery;
restore evidence comes from the clean-host Authentik drill; rollback evidence
comes from the embedded NetBird restoration.

The JSON report contains no tokens or secret values. Attach it to issues #26
and #37 with immutable refs, certificate/secret reference IDs, test users and
groups, start/end times, and accepted gaps. A report with production hostnames,
clients, or secret references is rejected before any live request.
