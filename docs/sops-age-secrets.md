# SOPS/age Authentik and OIDC secrets

`ansible/secrets/authentik-test.enc.yaml` and
`ansible/secrets/authentik-prod.enc.yaml` are SOPS files. Test and production
have distinct deployment recipients; both also include the offline recovery
recipient shown in `.sops.yaml`. The repository contains ciphertext only.
Private age identities stay in the operator's offline recovery store and are
provided at deploy time through `SOPS_AGE_KEY_FILE`.

The operator's public recipient is
`age1ne73ewzuzw9l6cjstaykp5w79h9sv7h04j6y53g32kktleqeny9qeq3hf7`.
It is included alongside the environment deployment and offline recovery
recipients in both repository files and `.sops.yaml`. Adding this recipient
rewrapped the existing data keys; the encrypted values and decrypted contents
were preserved. The operator private identity was not supplied to this
controller, so operator-side decryption still needs local verification.

With that private identity configured locally, verify access without printing
secrets:

```bash
sops --decrypt ansible/secrets/authentik-test.enc.yaml >/dev/null
sops --decrypt ansible/secrets/authentik-prod.enc.yaml >/dev/null
```

Set `SOPS_AGE_KEY_FILE` to the existing local identity file if SOPS does not
already discover it. Retain the current deployment/recovery identities until
operator decryption and recovery custody are confirmed. Runtime rehearsal
bundles remain outside Git; the ciphertext-only operator handoff includes a
local verifier. HOU4 certificate renewal's private `age_recipient` setting
includes both current and operator recipients for future writes.
See [recipient handoff evidence](evidence/operator-sops-recipient-2026-11-08.json).
This recipient addition does not migrate Ansible Vault or re-encrypt existing
non-SOPS backup archives; secrets consolidation remains infrastructure #45.

Deploy an environment with its own identity file and never place that file in
the repository or image:

```bash
export SOPS_AGE_KEY_FILE=/root/.config/sops/age/hou1-authentik-test.txt
ansible-playbook playbooks/authentik.yml -e target_hosts=hou1_test \
  -e deploy_environment=test -e authentik_age_key_file="$SOPS_AGE_KEY_FILE" \
  --ask-vault-pass
```

The role copies ciphertext to a root-only temporary path, decrypts it without
command output, renders root-owned mode `0600` Authentik and OIDC client files,
and removes both temporary ciphertext and plaintext files in an `always` block.
The client file contains separate staff and NetBird values for later bootstrap;
it is never mounted into an image or printed in logs.

Rotate recipients with an authenticated current key and preserve the encrypted
backup until the new ciphertext decrypts:

```bash
scripts/sops-age-rotate.sh ansible/secrets/authentik-test.enc.yaml AGE1_NEW_TEST_RECIPIENT
```

For client-secret rotation, add `STAFF_OIDC_CLIENT_SECRET_OLD` or
`NETBIRD_OIDC_CLIENT_SECRET_OLD` to the encrypted file, deploy the new value,
verify both values during the bounded overlap window, then remove the `_OLD`
key in a second SOPS edit. `scripts/sops-age-overlap-check.sh` fails unless
both current and old encrypted values exist. Rollback restores the prior
ciphertext and previous rendered files from the protected deployment backup;
the old client value remains valid only for that documented overlap window.

Create an offline recovery copy without exposing plaintext:

```bash
umask 077
sops --encrypt --age AGE1_OFFLINE_RECOVERY_RECIPIENT \
  ansible/secrets/authentik-prod.enc.yaml > /offline/hou1/authentik-prod.enc.yaml
chmod 0600 /offline/hou1/authentik-prod.enc.yaml
```

The recovery operator verifies the copy with the offline private identity,
records its checksum and backup identifier, and keeps the identity offline.
Normal deployment hosts receive only their environment-specific identity.
