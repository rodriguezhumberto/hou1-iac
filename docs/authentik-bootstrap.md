# Authentik groups, claims, scopes, and isolated clients

Run `playbooks/authentik-bootstrap.yml` only after the matching DNS/TLS ingress
and SOPS/age secret workflow are ready:

```bash
cd ansible
ansible-playbook playbooks/authentik-bootstrap.yml \
  -e target_hosts=hou1_test -e deploy_environment=test \
  -e authentik_age_key_file=/root/.config/sops/age/hou1-authentik-test.txt \
  --ask-vault-pass
```

The bootstrap utility uses the Authentik API and only reconciles the named
groups, users, property mappings, providers, and applications in the selected
manifest. It never deletes or rewrites unrelated identities. Re-running it is
idempotent. Test and production manifests use separate issuer hosts,
certificates, client IDs, redirect/logout URIs, and SOPS-rendered secrets:

| environment | staff client/audience | NetBird client/audience |
| --- | --- | --- |
| test | `pearland-paws-staff-test` | `netbird-test` |
| prod | `pearland-paws-staff` | `netbird-prod` |

The `groups` scope mapping emits only the exact staff group names. The staff
application and NetBird client each have a distinct audience and redirect
allowlist; the clients cannot be substituted across environments. The app's
server-side authorization helper defaults to deny when the groups claim is
missing, unknown, duplicated, or malformed. Individual users are supplied
through `authentik_staff_users` and are added to explicit role groups without
using email as an identity key.

For either environment, set `authentik_google_enabled: true` alongside
`authentik_bootstrap_enabled`. Its environment-specific SOPS bundle
must contain `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
`GOOGLE_ALLOWED_EMAILS` (an explicit lowercase address list). The selected manifest
configures Google with S256 PKCE, gates both default source flows with the
verified-email allowlist, enables the login button, and uses the normal internal
user type for application access. This does not grant superuser or staff roles;
the encrypted admission configuration still manages explicit roles after first
login. Other login sources remain on the identification stage. This opt-in
source bootstrap validates the environment's exact client IDs and callback/logout
contract before API writes. Production uses its own Google OAuth client and
secret; register `https://auth.<production-suffix>/source/oauth/callback/google/`
as its Google authorized redirect URI. Do not reuse the rehearsal OAuth client.

For production, select the production inventory and `deploy_environment=prod`,
the production SOPS bundle and its scoped age identity. Confirm production
DNS/TLS and permitted Google discovery/token/userinfo egress first; this identity
bootstrap does not configure network exceptions. Keep the local emergency admin
separate. After bootstrap, reconcile the encrypted allowlist and staff assignments
using the [admission command](#google-admission-and-staffvpn-assignments).

## Google admission and staff/VPN assignments

The historical filename `scripts/rehearsal-netbird-admission.py` supports both
environments. It decrypts the configuration and endpoint-matched NetBird token
only in memory; plaintext inputs and SOPS integrity failures are rejected.

```bash
python3 scripts/rehearsal-netbird-admission.py \
  --config /private/admission-prod.enc.json --age-key-file /private/scoped-age-key
```

Select these values inside the SOPS-encrypted configuration; never pass emails,
role assignments or tokens on the command line:

| Input | Test | Production |
| --- | --- | --- |
| `environment` | `test` | `prod` |
| `netbird_url` | `https://netbird-test.<suffix>` | `https://netbird.<suffix>` |
| `authentik_issuer` | `https://auth-test.<suffix>/application/o/netbird-test/` | `https://auth.<suffix>/application/o/netbird-prod/` |
| `netbird_subnet` | `100.91.0.0/16` | `100.90.0.0/16` |
| `fixture_users` | Explicit internal `@example.invalid` fixtures | Omit or use `[]`; fixtures are rejected |

Also supply `ssh` (the SSH argument list for the selected host),
`netbird_token_file` (a separate SOPS-encrypted, endpoint-bound API token bundle),
`age_key_file` (its scoped identity path), `google_emails` (exact lowercase approved
addresses), and optional `staff_users` entries with `email` and `role`
(`reader`, `editor`, or `admin`). The issuer and management endpoint must share
the same DNS suffix. Ports, credentials, paths, queries and fragments are refused
in the management origin. The Authentik container is derived from the environment;
it cannot be supplied as an override. Use an authenticated SSH host/key binding
and the approved environment-specific host inventory.

Reconciliation verifies both Google source bindings fail closed in ALL mode,
then exercises the running policies for approved, unlisted and unverified
identities before disabling NetBird's manual approval queue. A connector or
account/subnet mismatch leaves NetBird settings unchanged. Staff assignments
replace only managed Authentik staff-role groups and add NetBird's `staff`
auto-group. They preserve unrelated groups and the regular NetBird `user` role;
staff-app administrators receive no network-administrator grant.

New identities are counted as awaiting first login. Rerun after their first
Google/NetBird login, then refresh the staff session and verify the intended
role and VPN reachability. Production application admission permits active,
allowlisted Google-source users only; the local emergency identity is preserved
and has no rehearsal-fixture bypass. Use the [offboarding command](operator-offboarding.md)
to revoke an existing identity and its sessions/devices; changing an allowlist
alone does not revoke existing tokens or peers.

`AUTHENTIK_EMERGENCY_PASSWORD`, when present in that SOPS bundle, initializes
the local `akadmin` account on first boot. Hand it to the operator encrypted;
bootstrap variables do not reset an existing database's password. Private
identity manifests are excluded from Ansible output and removed after bootstrap,
including command failure. Google allowlists remain in SOPS inputs and the
running Authentik database. See the [fresh-host checklist](fresh-host-rehearsal.md)
for input handoff, manual CMS seeding and outstanding restore evidence.
