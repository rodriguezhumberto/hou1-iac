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

Some roles decrypt on the controller; Authentik still uses the managed host's
`authentik_age_key_file`. That must be a scoped runtime identity, never the owner's
personal identity. Ansible Vault inputs also remain. Controller-side consolidation
and Vault removal are unfinished [#45](https://github.com/rodriguezhumberto/hou1-iac/issues/45).

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
