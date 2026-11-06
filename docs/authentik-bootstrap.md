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
