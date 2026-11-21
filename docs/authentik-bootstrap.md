# Authentik groups, claims, scopes, and isolated clients

Use a private inventory for the selected environment; test deployments require an
isolated host. See [environment targeting](development.md#environment-targeting).

Run `playbooks/authentik-bootstrap.yml` only after the matching DNS/TLS ingress
and SOPS/age secret workflow are ready:

```bash
cd ansible
ansible-playbook -i /private/test/inventory.yml playbooks/authentik-bootstrap.yml \
  -e target_hosts=hou1_test -e deploy_environment=test \
  -e authentik_age_key_file=/root/.config/sops/age/hou1-authentik-test.txt \
  -e authentik_controller_age_key_file=/private/authentik-test.key
```

## Identity contract

Bootstrap reconciles only manifest-owned groups, users, mappings, providers and
applications; unrelated identities are preserved and reruns are idempotent.
`authentik_identity_profiles` selects public endpoints, subnet, callbacks and
Compose selectors. Private client, application, role-group and mapping names
come from `identity-names-<environment>.enc.json`. Default NetBird clients are
`netbird-test` and `netbird-prod`; staff client names come from that bundle.

Override public values in inventory and edit private names through SOPS. Keep
test and production clients, domains, certificates and secrets separate. The
role checks that the selected Compose path matches its deployment.

The `groups` scope mapping emits only the exact staff group names. The staff
application and NetBird client each have a distinct audience and redirect
allowlist; the clients cannot be substituted across environments. The app's
server-side authorization helper defaults to deny when the groups claim is
missing, unknown, duplicated, or malformed. Individual users are supplied
through `authentik_staff_users` and are added to explicit role groups without
using email as an identity key.

## Google login

Google, identity bootstrap and branded login are enabled by default for both
environments. To restore the previous login, rerun the Authentik playbook with
`-e authentik_branding_enabled=false`; leave bootstrap enabled for restoration.
The environment-specific SOPS bundle must contain `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
`GOOGLE_ALLOWED_EMAILS` (an explicit lowercase address list). The selected manifest
configures Google with S256 PKCE, gates both default source flows with the
verified-email allowlist, enables the login button, and uses the normal internal
user type for application access. Role assignment is separate from admission;
other login sources remain on the identification stage. Bootstrap validates
client IDs and callbacks before API writes. Production uses its own Google
OAuth client and secret; register `https://<profile-issuer-host>/source/oauth/callback/google/`
as its Google authorized redirect URI.

Inventory may select `authentik_secret_source`; otherwise the role uses the
environment-specific bundle under `ansible/secrets`. Lab inventories must enable
`authentik_google_enabled` and `authentik_branding_enabled` when human Google login
is part of acceptance. A changed issuer hostname also needs its exact Google
callback registered; local probe login does not verify that upstream callback.

The source authentication flow uses `authentication: none` so a Google callback
also works when the browser already has an Authentik session, including when
returning to the applications overview after logout from another application.
Bootstrap applies this setting only after both verified-email policy bindings
are enabled, non-negated and fail closed in ALL mode. Check the applications
overview after fresh login and with an existing session; the source allowlist
and application role checks still apply.

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
Export the public profile from the same inventory and variable overrides used
for deployment. This playbook selects one host's variables and writes on the
controller without connecting to that host:

```bash
ansible-playbook -i /private/production/inventory.yml \
  ansible/playbooks/export-identity-profile.yml \
  -e target_hosts=hou1_prod -e deploy_environment=prod \
  -e identity_profile_output=/private/identity-prod.json
```

The exported profile contains no organization-specific client or application name,
role-group names, scope-mapping names, account allowlist, OAuth secret or token.
Treat it as trusted deployment configuration and protect it from modification;
the command does not accept a profile embedded in the encrypted admission file
as its expected destination. Refresh the export after inventory changes.

```bash
python3 scripts/rehearsal-netbird-admission.py \
  --profile /private/identity-prod.json \
  --identity-secrets ansible/secrets/identity-names-prod.enc.json \
  --config /private/admission-prod.enc.json --age-key-file /private/scoped-age-key
```

The role and admission command decrypt the naming bundle in controller memory
with the scoped age identity. The role suppresses output and retains only
ciphertext on HOU1 for recovery. Its `IDENTITY_NAMES` object contains:

- `environment`, matching the selected profile.
- `staff.client_id` and `staff.application_name`.
- `role_groups.reader`, `role_groups.editor`, and `role_groups.admin`.
- `scope_mapping_names.profile`, `.groups`, and `.audience`.

Committed naming bundles authorize only the owner recipient. Grant temporary
deployment access through encrypted copies as described in [SOPS/age](sops-age-secrets.md).
Missing or mismatched names fail closed. Changing a client or group name requires
a coordinated identity migration.

The encrypted admission config also needs the selected host's `ssh_host_ip`,
verified ED25519 `ssh_fingerprint` and `ssh -F CONFIG ALIAS` list. See the
[SSH target contract](probes/rehearsal-targets.md). Destination validation runs
before token decryption and API access.

The SOPS-encrypted admission configuration retains its environment and destination
bindings. They must exactly match the independently exported profile; mismatches
fail before API access. The default bindings are shown below. Never pass emails,
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
(`reader`, `editor`, or `admin`). HTTPS origins, callback/logout lists and subnet
syntax are validated, without prescribing hostname prefixes or DNS suffixes.
Ports, credentials, paths, queries and fragments are refused in the management
origin. The command discovers exactly one running Authentik container with
`docker compose -p <profile-project> -f <profile-compose-file> ps -q <profile-service>`;
there is no generated container-name assumption or fallback. Use an authenticated
SSH host/key binding and the approved environment-specific host inventory.

Reconciliation verifies both Google source bindings fail closed in ALL mode,
then exercises the running policies for approved, unlisted and unverified
identities before disabling NetBird's manual approval queue. A connector or
account/subnet mismatch leaves NetBird settings unchanged. Staff assignments
replace only managed Authentik staff-role groups and add NetBird's `staff`
auto-group. They preserve unrelated groups and the regular NetBird `user` role;
staff-app administrators receive no network-administrator grant.

After a new user's first Google/NetBird login, rerun admission, refresh the staff
session and verify the intended role and VPN reachability. Production admission
permits active, allowlisted Google-source users only; the local emergency identity
is preserved and has no rehearsal-fixture bypass. Use the
[offboarding command](operator-offboarding.md) to revoke an existing identity and its sessions/devices; changing an allowlist
alone does not revoke existing tokens or peers.

## Emergency identity and cleanup

`AUTHENTIK_EMERGENCY_PASSWORD`, when present in that SOPS bundle, initializes
the local `akadmin` account on first boot. Hand it to the operator encrypted;
bootstrap variables do not reset an existing database's password. Private
identity manifests are excluded from Ansible output and removed after bootstrap,
including command failure. Google allowlists remain in SOPS inputs and the
running Authentik database. For a lost node, use [production recovery](production-host-recovery.md)
rather than bootstrap; the [fresh-host journal](evidence/fresh-host-rehearsal.md) records
earlier test preparation.
