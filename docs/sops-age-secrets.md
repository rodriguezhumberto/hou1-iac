# SOPS/age Authentik and OIDC secrets

`ansible/secrets/authentik-test.enc.yaml` and
`ansible/secrets/authentik-prod.enc.yaml` now authorize only the operator's
public recipient:
`age1ne73ewzuzw9l6cjstaykp5w79h9sv7h04j6y53g32kktleqeny9qeq3hf7`.
The creation policy preserves this boundary for future edits. The operator
confirmed local access before controller identities were restricted. New data
keys encrypt the same secret values; retained historical ciphertext is not a
revocation of values or identities previously exposed.

The controller's ongoing SOPS access is limited to three job identities:

| Job | Decryptable inputs |
| --- | --- |
| HOU1 synthetic health checks | Two isolated test-account passwords |
| HOU1 certificate renewal | Its DNS token document |
| HOU4 certificate renewal | Its DNS token, ACME state and current/previous TLS bundles |

All three are denied the repository deployment files, NetBird PAT, admission
allowlist, offboarding inputs and complete deployment bundles. Those documents
are operator-only. The operator remains a recipient on each job document.
Private identities and runtime bundles remain outside Git.

File modes are 0600 and private directories are 0700. This controller still has
passwordless sudo and administrative SSH access to the test hosts, so this is
cryptographic job scoping, not isolation from another same-user or privileged
session. Production automation needs a separate restricted controller without
shared sudo or routine test-host access. The operator identity stays off this
controller.

## Deployment and recovery

Existing automation cannot deploy using the full secret bundles. An operator
must authorize a short-lived, environment-specific deployment identity and a
purpose-limited encrypted input before deployment. Keep operator and recovery
identities offline. The legacy Authentik role decrypts on the managed host using
`authentik_age_key_file`; never place the operator private identity at that path.
Controller-side decryption and Vault removal remain infrastructure #45. This
change leaves the existing Vault path and deployed service values unchanged.

Verify operator access locally without printing secrets:

```bash
sops --decrypt ansible/secrets/authentik-test.enc.yaml >/dev/null
sops --decrypt ansible/secrets/authentik-prod.enc.yaml >/dev/null
```

Set `SOPS_AGE_KEY_FILE` to the local operator identity if SOPS does not already
discover it. Twelve previous deployment/recovery identity files were escrowed
in an age archive encrypted solely to the operator, then removed from their
controller paths after job verification. Download and verify that archive
locally; it preserves identities needed by historical ciphertext and non-SOPS
backup archives. Keep the decrypted recovery material offline. New encryption
cannot erase prior copies, process memory, snapshots or access to unchanged
underlying credentials.

The controller installer accepts separate monitor and TLS inputs:

```bash
python3 scripts/install-rehearsal-monitor.py \
  --config /private/controller/monitor.json \
  --tls-config /private/controller/tls-controller.json
```

The monitor config selects only the health bundle/key. The TLS config selects
only its DNS bundle/key. HOU4's private renewal `age_recipient` list contains
its renewal recipient and the operator recipient for future writes.

For client-secret rotation, add `STAFF_OIDC_CLIENT_SECRET_OLD` or
`NETBIRD_OIDC_CLIENT_SECRET_OLD`, deploy and verify the bounded overlap, then
remove `_OLD` in a second edit. Existing overlap checks and rollback procedures
still apply. Secret rotation was not performed by this access change.

See [controller scoping evidence](evidence/controller-key-scoping-2026-11-08.json).
Earlier [recipient-addition evidence](evidence/operator-sops-recipient-2026-11-08.json)
records the previous policy. HOU1's legacy TLS renewal timer was already disabled
and its monitor reports a stale renewal receipt; that separate rollout finding
must be resolved before production. This change preserves that scheduling state.
