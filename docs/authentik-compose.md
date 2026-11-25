# Isolated Authentik Compose deployment

Test examples require an explicitly selected isolated host and private inventory.
The [recorded lab acceptance](cleanup-plan.md) does not select a host for a new run;
see [environment targeting](development.md#environment-targeting).

Each environment has a pinned Compose project. The default names are
`hou1-authentik-test` and `hou1-authentik-prod`, selected by the identity profile;
state lives under `/var/lib/hou1-authentik/{test,prod}`. PostgreSQL uses the private,
internal backend network and has no published port. Only the Authentik server
joins the environment-specific ingress network for the later Nginx work.

Run the [capacity and firewall gate](authentik-host-policy.md) first, then deploy
an environment explicitly:

```bash
cd ansible
ansible-playbook -i /private/test/inventory.yml playbooks/authentik.yml \
  -e target_hosts=hou1_test -e deploy_environment=test
ansible-playbook -i /private/production/inventory.yml playbooks/authentik.yml \
  -e target_hosts=hou1_prod -e deploy_environment=prod
```

The play loads `authentik_secret_source` on the controller and requires the
matching controller and host identity paths; see [SOPS/age](sops-age-secrets.md).
The environment file is root-owned mode `0600`; persistent PostgreSQL, media,
templates, certificates, and blueprint/configuration directories have separate
roots and restrictive ownership. The server and worker wait for the PostgreSQL
health check, all services restart automatically, logs are capped at 20 MiB ×
3 files, and each service has explicit CPU, memory, and PID limits.

Compose validation and startup health checks must pass before ingress is
published. A failure stops the role; it does not automatically roll back data or
configuration. Use [Authentik recovery](authentik-backup-restore.md) with a
verified archive and compatible revision/state; the application image rollback
does not restore Authentik or PostgreSQL.
