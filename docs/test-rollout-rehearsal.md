# Isolated test rollout and recovery rehearsal

> Original isolated rollout-harness reference. Current clean-OS tests use [clean test-host recovery](clean-host-recovery.md); production uses [production recovery](production-host-recovery.md). Historical pending gates below are not current production status.

Issue #27 is a gate for production approval. The rehearsal uses separate
`hou1_test` inventory, `auth-test` and `netbird-test` endpoints, test TLS
certificates, test Authentik clients/users/groups, and the test SOPS/age
ciphertext. Production domains, clients, certificates, users, and secret
references must not appear in the command or evidence files.

## Immutable deployment

Create a clean checkout at the exact commits under test. Record the three
commit SHAs in the issue and keep the checkout at the infrastructure SHA while
the playbooks run:

```bash
INFRA_REF=0123456789abcdef0123456789abcdef01234567
STAFF_REF=89abcdef0123456789abcdef0123456789abcdef
NETBIRD_REF=abcdef0123456789abcdef0123456789abcdef
git fetch origin "$INFRA_REF" && git checkout --detach "$INFRA_REF"
mkdir -m 700 -p /var/tmp/hou1-rollout-test

python3 scripts/test-rollout-rehearsal.py --phase plan \
  --infra-ref "$INFRA_REF" --staff-ref "$STAFF_REF" --netbird-ref "$NETBIRD_REF" \
  --test-domain test.example.com \
  --issuer https://auth-test.example.com/application/o/netbird-test/ \
  --netbird-url https://netbird-test.example.com \
  --cert-ref cert-test-2026-11-06 --secret-ref sops-age-test \
  --evidence-dir /var/tmp/hou1-rollout-test
```

Review the printed plan, then run the same command with `--phase deploy`. It
performs the capacity/isolation preflight and deploys only `hou1_test`, the
test web stack, Authentik, and its test identity data. The script refuses a
non-test endpoint, a production-looking value, a mutable ref, a non-pinned
inventory, or a checkout whose `HEAD` is different from `INFRA_REF`.

## Controlled checks and recovery drills

After deployment, the operator validates DNS and TLS (including forwarded
headers and WebSocket/OIDC routes), checks the health and monitoring alerts,
and runs the staff shared-password migration against test users only. Save
redacted command output in the evidence directory using the names below.

1. Stop the test Authentik project and record the staff and NetBird failure,
   API PAT/SSH recovery, and restart result as `authentik-outage.txt`.
2. Run the test clean-host restore drill with the encrypted test archive and
   expected test issuer. Record the checksum, duration, login, discovery, and
   JWKS results as `restore.txt`.
3. Restore the embedded NetBird identity from the test cutover snapshot and
   verify API, dashboard, gRPC/WebSocket, and peer connectivity. Record the
   immutable snapshot and health results as `netbird-rollback.txt`.
4. Run the host-console/NetBird-SSH break-glass procedure with a test PAT and
   record operator, reason, timestamps, and pass/fail (never the PAT) as
   `break-glass.txt`.

The remaining required files are `dns-tls.txt`, `monitoring.txt`, and
`password-migration.txt`. They must contain no tokens, passwords, private
keys, or production identifiers. The verify phase stores only SHA-256, byte
count, commit references, and timestamps:

```bash
python3 scripts/test-rollout-rehearsal.py --phase verify \
  --infra-ref "$INFRA_REF" --staff-ref "$STAFF_REF" --netbird-ref "$NETBIRD_REF" \
  --test-domain test.example.com \
  --issuer https://auth-test.example.com/application/o/netbird-test/ \
  --netbird-url https://netbird-test.example.com \
  --cert-ref cert-test-2026-11-06 --secret-ref sops-age-test \
  --evidence-dir /var/tmp/hou1-rollout-test \
  --dns-tls-evidence /var/tmp/hou1-rollout-test/dns-tls.txt \
  --monitoring-evidence /var/tmp/hou1-rollout-test/monitoring.txt \
  --password-migration-evidence /var/tmp/hou1-rollout-test/password-migration.txt \
  --authentik-outage-evidence /var/tmp/hou1-rollout-test/authentik-outage.txt \
  --restore-evidence /var/tmp/hou1-rollout-test/restore.txt \
  --netbird-rollback-evidence /var/tmp/hou1-rollout-test/netbird-rollback.txt \
  --break-glass-evidence /var/tmp/hou1-rollout-test/break-glass.txt
```

Attach `rollout-rehearsal.json` and the redacted findings to issues #27 and
#39. Every finding is either resolved with a linked change or explicitly
accepted with an owner and due date. Production approval requires a passed
deployment, all seven evidence hashes, successful restore and rollback, and no
unaccepted isolation finding.
