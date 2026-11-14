# Authentik groups, claims, scopes, and isolated clients

Run `playbooks/authentik-bootstrap.yml` only after the matching DNS/TLS ingress
and SOPS/age secret workflow are ready:

```bash
cd ansible
ansible-playbook playbooks/authentik-bootstrap.yml \
  -e target_hosts=hou1_test -e deploy_environment=test \
  -e authentik_age_key_file=/root/.config/sops/age/hou1-authentik-test.txt \
  -e authentik_controller_age_key_file=/private/authentik-test.key
```

The bootstrap utility uses the Authentik API and only reconciles the named
groups, users, property mappings, providers, and applications in the selected
manifest. It never deletes or rewrites unrelated identities. Re-running it is
idempotent. `authentik_identity_profiles` in inventory defines the public issuer,
management endpoint, subnet, callback/logout lists, and Compose selectors.
Organization-specific client, application, group and scope-mapping names come
from the matching SOPS-encrypted `identity-names-<environment>.enc.json` bundle. One shared manifest template consumes the selected profile; bootstrap
and admission share its validator. The default profiles are:

| environment | staff client/audience | NetBird client/audience |
| --- | --- | --- |
| test | From the encrypted naming bundle | `netbird-test` |
| prod | From the encrypted naming bundle | `netbird-prod` |

Override public deployment values in the inventory profile. Edit identity names
only through SOPS; do not add them to inventory, examples or source code. Keep test/production clients, domains,
certificates and SOPS secrets separate. The role checks that its Compose selector
matches its deployment path. Client/provider/application identifiers use the
configured client ID; other naming conventions are not enforced in Python.

The `groups` scope mapping emits only the exact staff group names. The staff
application and NetBird client each have a distinct audience and redirect
allowlist; the clients cannot be substituted across environments. The app's
server-side authorization helper defaults to deny when the groups claim is
missing, unknown, duplicated, or malformed. Individual users are supplied
through `authentik_staff_users` and are added to explicit role groups without
using email as an identity key.

Google, identity bootstrap, and branded login are enabled by default for both
environments. To restore the previous login, rerun the Authentik playbook with
`-e authentik_branding_enabled=false`; leave bootstrap enabled for restoration.
The environment-specific SOPS bundle
must contain `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
`GOOGLE_ALLOWED_EMAILS` (an explicit lowercase address list). The selected manifest
configures Google with S256 PKCE, gates both default source flows with the
verified-email allowlist, enables the login button, and uses the normal internal
user type for application access. This does not grant superuser or staff roles;
the encrypted admission configuration still manages explicit roles after first
login. Other login sources remain on the identification stage. This
source bootstrap validates the environment's exact client IDs and callback/logout
contract before API writes. Production uses its own Google OAuth client and
secret; register `https://<profile-issuer-host>/source/oauth/callback/google/`
as its Google authorized redirect URI. Do not reuse the rehearsal OAuth client.

The source authentication flow uses `authentication: none` so a Google callback
also works when the browser already has an Authentik session, including when
returning to the applications overview after logout from another application.
Bootstrap applies this setting only after both verified-email policy bindings
are enabled, non-negated and fail closed in ALL mode. This flow setting does not
grant application roles or bypass the account allowlist. Check the overview
after a fresh login and again with an existing session when validating changes.

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
ansible-playbook -i ansible/inventory/inventory.yml \
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

The role decrypts the naming bundle on the managed host into memory, suppresses
its output, and retains only ciphertext alongside the deployment for recovery.
The controller command decrypts the same bundle in memory with its scoped age
identity. Its `IDENTITY_NAMES` object contains:

- `environment`, matching the selected profile.
- `staff.client_id` and `staff.application_name`.
- `role_groups.reader`, `role_groups.editor`, and `role_groups.admin`.
- `scope_mapping_names.profile`, `.groups`, and `.audience`.

The committed bundles preserve the existing identity names and are encrypted
only to the owner's age recipient. A temporary deployment recipient must be
granted access to copies of these bundles alongside the other deployment secrets.
Missing or mismatched naming inputs fail closed; there is no plaintext default.
Keep existing values during this cleanup: changing a client or group name is
a separate identity migration requiring coordinated application configuration.

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

New identities are counted as awaiting first login. Rerun after their first
Google/NetBird login, then refresh the staff session and verify the intended
role and VPN reachability. Production application admission permits active,
allowlisted Google-source users only; the local emergency identity is preserved
and has no rehearsal-fixture bypass, regardless of profile settings. Use the [offboarding command](operator-offboarding.md)
to revoke an existing identity and its sessions/devices; changing an allowlist
alone does not revoke existing tokens or peers.

`AUTHENTIK_EMERGENCY_PASSWORD`, when present in that SOPS bundle, initializes
the local `akadmin` account on first boot. Hand it to the operator encrypted;
bootstrap variables do not reset an existing database's password. Private
identity manifests are excluded from Ansible output and removed after bootstrap,
including command failure. Google allowlists remain in SOPS inputs and the
running Authentik database. See the [fresh-host checklist](fresh-host-rehearsal.md)
for input handoff, manual CMS seeding and outstanding restore evidence.
