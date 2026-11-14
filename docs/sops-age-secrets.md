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

NetBird's token Vault fallback remains.
[#45](https://github.com/rodriguezhumberto/hou1-iac/issues/45) tracks its migration
and the remaining recovery checks. Verify owner-local decryption of new recovery
copies before retiring legacy recovery material.

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

Changing SOPS recipients does not revoke plaintext credentials already seen,
historical ciphertext copies, snapshots or prior memory. Rotate underlying
credentials separately when required. Client-secret overlap uses
`STAFF_OIDC_CLIENT_SECRET_OLD` / `NETBIRD_OIDC_CLIENT_SECRET_OLD`; verify a bounded
rotation before removing the old value. Recipient changes are not secret rotation.

[Historical scoping evidence](evidence/controller-key-scoping-2026-11-08.json)
records the earlier test controller and retired timer. It is not the current
production renewal status; use [controller TLS](controller-tls.md).
