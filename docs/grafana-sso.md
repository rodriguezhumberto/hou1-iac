# Grafana identity provisioning

The optional `grafana_identity` role manages one dedicated Authentik application,
confidential OIDC client, signed group scope, access policy and three monitoring
groups. Google remains the upstream login; Authentik controls monitoring access.
Staff and VPN memberships do not grant Grafana access.

The production owner confirmed Google login, logout and re-login. Live checks
also confirm the administrator role, nonmember denial, local emergency access
and zero-change reapplication. See the
[redacted gate evidence](evidence/grafana-sso-2026-11-11.json).

Keep the complete contract in a separate SOPS/age JSON bundle:

| Field | Value |
| --- | --- |
| `environment` | `test` or `production`, matching the selected inventory. |
| `issuer_origin` / `hostname` | Exact HTTPS Authentik origin / private Grafana FQDN. |
| `application_slug` / `application_name` | Dedicated application identifier / private display name. |
| `client_id` / `client_secret` | Dedicated identifier / randomly generated URL-safe secret, 48–200 characters. |
| `signing_key_name` | Existing active Authentik signing key. |
| `role_groups` | Distinct `viewer`, `editor` and `admin` group names. |
| `seed_members` | Explicit lists of existing active, approved Google accounts for each role. |

The HOU4 client bundle contains only `environment`, `issuer`, `client_id`,
`client_secret` and `role_groups`. Its issuer is
`<issuer_origin>/application/o/<application_slug>/`. Preserve owner-encrypted
copies of both bundles. Give a temporary deployment identity access only for the
approved work, and remove its access afterward.

Set these variables in private deployment inputs:

```yaml
grafana_identity_enabled: true
grafana_identity_secret_source: /private/grafana-identity.enc.json
grafana_identity_age_key_file: /private/scoped-deployment.agekey
```

On an established node, select the intended environment and VPN inventory:

```bash
ansible-playbook -i /private/inventory.yml ansible/playbooks/grafana-identity.yml \
  -e @/private/hou1-runtime.json
```

The full Authentik role also applies this optional contract after bootstrap on
established nodes. The dedicated playbook discovers Authentik through its declared
Compose service. It requires the selected environment/issuer and a VPN connection.
Secrets travel through protected stdin; no API token is copied out of the container.

Provisioning refuses conflicting application, provider or group ownership and
missing/inactive seed accounts before writing. It preserves unrelated memberships,
binds the access policy before attaching a new provider, and can resume an
interrupted attachment. Reapplication must report zero changes. Seed membership
is additive: remove subsequent grants in Authentik's monitoring groups; removing
an account from `seed_members` alone does not revoke it.

Provisioning checks each seeded user's live Authentik claim preview before
reporting success. The preview must match the issuer, audience, subject email
and monitoring membership. Scope mappings read the token's `user` subject;
the HTTP request actor can differ during token exchange.

The exact callback is `https://<hostname>/login/generic_oauth`. Authentik signs
ID tokens with the selected key. A dedicated minimal profile avoids the built-in
profile's unfiltered groups claim; only the three monitoring groups appear in
this client's groups scope. Inactive users and users outside those
groups fail the application policy. Other clients, scopes and Google admission
rules remain unchanged.

Google's verified-email admission stays at the Authentik source policy. Grafana
identifies accounts by the signed subject and disables automatic email linking;
its authorization does not depend on Authentik's stock `email_verified: false`.

Before enabling the HOU4 client, verify its retained local emergency password.
Then apply HOU4's `grafana-sso.yml` playbook and check Google login, the assigned
role, logout and local emergency login. Record live evidence separately from the
disposable container tests. Passing fixtures cannot prove a human Google callback.
