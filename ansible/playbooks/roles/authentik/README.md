# Authentik

Deploys an isolated test or production Authentik project through `authentik.yml`.

## Task phases

`main.yml` statically imports these phases to retain role tags and task order:

1. `preflight.yml`: derive project names/roots and validate environment inputs.
2. `storage.yml`: prepare shared ingress, persistent directories and
   separate local/off-host backup roots.
3. `secrets.yml`: load SOPS inputs on the controller, retain encrypted identity
   names, render private runtime files and always remove legacy temporary files.
4. `compose.yml`: include the existing branding lifecycle and render Compose.
5. `backup.yml`: retain environment ciphertext and install backup/restore
   commands and the guarded legacy backup schedule.
6. `deploy.yml`: validate Compose, wait for startup, inspect services and retain
   the conditional server restart used to clear changed branding templates.
7. `ingress.yml`: reconcile the declared Nginx attachment, prepare TLS and render
   the virtual host; validate Nginx and reload when its configuration changes.
8. `bootstrap.yml`: discover the private API, apply identity inputs, always
   remove the plaintext manifest and optionally configure Grafana identity.

## Inputs and ownership

Role defaults hold implementation settings; shared inventory holds topology and
public identity profiles. Environment inventory and scoped SOPS bundles supply
private inputs. Required inputs include
`authentik_environment` (`test` or `prod`), its `authentik_identity_profiles` entry,
`authentik_secret_source`, absolute `authentik_age_key_file`, backup encryption
recipient and the existing ingress/application/TLS settings. Controller secret
loading can select `authentik_controller_age_key_file`. Enabled identity
bootstrap also requires `authentik_identity_secret_source` and private names.

The role owns its environment's `/opt/hou1-authentik/{test,prod}` project files,
`/var/lib/hou1-authentik/{test,prod}` data, configured backup
roots/schedule, managed login assets and Authentik Nginx virtual host.
`secrets.yml` alone renders the private environment files; `bootstrap.yml` owns
the temporary identity manifest. Backup scripts retain existing recovery formats.

## Prerequisites

Run `authentik-preflight.yml` first. The controller needs SOPS/age, declared
Ansible collections and scoped decryption access; the host needs Docker Compose,
backup encryption/recovery prerequisites and the existing application Nginx.
Enable `authentik_enable_ingress_network` and select the environment's
network with `authentik_active_network` before publication. Shared ingress resources
and the Nginx attachment are prepared without a running NetBird backend.
Import certificates first in controller-managed TLS mode. Branding
requires Google bootstrap and the guarded Authentik version; the role retains
the existing opt-in conditions and private bootstrap access path.
