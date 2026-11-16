# SOPS/age secrets and controller access

The repository Authentik bundles authorize the owner recipient through the
creation policy. Keep the owner's long-lived private key offline. Deployment
copies may add an explicitly authorized temporary recipient while retaining the
owner; retire that grant after verified owner access and recovery handoff.

Ongoing jobs use separate scoped identities for synthetic login and HOU1/HOU4
certificate renewal. Give each only its required credential/DNS/ACME/certificate
bundles, not the full deployment secrets, NetBird PAT or admission allowlist.
Keep the owner as a recovery recipient. Protect files with mode 0600 and private
directories with 0700; runtime identities and bundles remain outside Git.

Cryptographic scoping is not isolation from the same privileged OS user, Docker
or sudo. Production's separate renewal peer restricts transport and fixed commands;
it must not be reused for administration. See [the transport contract](controller-renewal-peer.md).
Temporary production admin grants were retired after handoff.

## Deployment and recovery

Inventory secrets are in `group_vars/test_env.sops.yml` and
`group_vars/prod_env.sops.yml`, with separate deployment recipients and the owner
as recovery recipient. Keep deployment identities on the controller and retain
an owner-encrypted recovery copy. Select the matching key with
`SOPS_AGE_KEY_FILE`. Secrets load when needed by a target, so a test-only
play does not require the production key. `ansible-inventory --list` includes all
hosts and requires access to both environments; its output contains secrets.
Shared settings remain in both bundles; credential values and scopes are unchanged.
Production NetBird bootstrap credentials are only in the production bundle.

Authentik loads deployment secrets and identity names on the controller through
`community.sops.load_vars`. Set `authentik_controller_age_key_file` to the private
controller identity, or select it with `SOPS_AGE_KEY_FILE`. Only rendered runtime
files and retained ciphertext are copied to HOU1. Keep `authentik_age_key_file`
as the separate host-only identity for the existing backup/restore contract;
never install the owner's or controller's identity there.

NetBird token storage uses SOPS/age only. `netbird_token_age_key_file` defaults to
`SOPS_AGE_KEY_FILE`; `netbird_token_recovery_recipients` declares offline recovery
recipients. New PATs encrypt to both identities, and reads check the recovery
recipients. The action and `netbird_token_vault_file` input retain their names
for existing inventories; the file must contain SOPS ciphertext.

Before using legacy private token copies, convert them on the controller and
verify the endpoint and token are unchanged. Retain encrypted legacy copies
until owner-local decryption of the new recovery material passes.
[#45](https://github.com/rodriguezhumberto/hou1-iac/issues/45) tracks the remaining
deployment and recovery checks.

Verify owner access locally without printing plaintext:

```bash
sops --decrypt ansible/secrets/authentik-test.enc.yaml >/dev/null
sops --decrypt ansible/secrets/authentik-prod.enc.yaml >/dev/null
```

Set `SOPS_AGE_KEY_FILE` locally if needed. Preserve encrypted recovery copies of
historical deployment identities and the independent controller handoff. Check
actual decryption, not only that the files downloaded. Owner-local authenticated
decryption [passed](evidence/owner-recovery-access-2026-11-12.json); executing a
replacement controller remains [#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77).

## Recipient rotation

Generate a separate replacement deployment identity for each environment. Retain
the offline owner recipient in the complete recipient list:

```bash
SOPS_AGE_KEY_FILE=/private/current.key \
SOPS_AGE_NEW_KEY_FILE=/private/replacement.key \
  scripts/sops-age-rotate.sh ansible/inventory/group_vars/test_env.sops.yml \
  age1_REPLACEMENT_RECIPIENT age1_OWNER_RECIPIENT
```

The script stages ciphertext beside the original, updates recipients and rotates
the SOPS data key. It replaces the original only after decryption with the
replacement identity passes; failures leave the original unchanged. Temporary
files contain ciphertext only and are removed on exit.

Repeat for every bundle granted to the retired identity and update `.sops.yaml`
to the same recipient lists. Verify the replacement identity decrypts each
bundle and the retired identity fails, using a clean controller without ambient
keys. Verify owner decryption offline, refresh the owner-encrypted recovery copy,
and update controller key paths before deleting the retired deployment key.
Keep existing encrypted recovery material until owner-local verification passes.

Changing SOPS recipients does not revoke plaintext credentials already seen,
historical ciphertext copies, snapshots or prior memory. Rotate underlying
credentials separately when required. Client-secret overlap uses
`STAFF_OIDC_CLIENT_SECRET_OLD` / `NETBIRD_OIDC_CLIENT_SECRET_OLD`; verify a bounded
rotation before removing the old value. Recipient changes are not secret rotation.

[Historical scoping evidence](evidence/controller-key-scoping-2026-11-08.json)
records the earlier test controller and retired timer. It is not the current
production renewal status; use [controller TLS](controller-tls.md).
