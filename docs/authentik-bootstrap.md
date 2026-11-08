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

For the isolated fresh-host rehearsal (#27), set `authentik_google_enabled: true`
alongside `authentik_bootstrap_enabled`. Its environment-specific SOPS bundle
must contain `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
`GOOGLE_ALLOWED_EMAILS` (an explicit lowercase address list). The test manifest
configures Google with S256 PKCE, gates both default source flows with the
verified-email allowlist, enables the login button, and uses the normal internal
user type for application access. This does not grant superuser or staff roles;
the encrypted admission configuration still manages explicit roles after first
login. Other login sources remain on the identification stage. This opt-in
source bootstrap refuses production environments.

`AUTHENTIK_EMERGENCY_PASSWORD`, when present in that SOPS bundle, initializes
the local `akadmin` account on first boot. Hand it to the operator encrypted;
bootstrap variables do not reset an existing database's password. Private
identity manifests are excluded from Ansible output and removed after bootstrap,
including command failure. Google allowlists remain in SOPS inputs and the
running Authentik database. See the [fresh-host checklist](fresh-host-rehearsal.md)
for input handoff, manual CMS seeding and outstanding restore evidence.
