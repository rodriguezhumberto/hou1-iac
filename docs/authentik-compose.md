# Isolated Authentik Compose deployment

Issue #18 deploys one pinned Compose project for each environment. The project
name is `hou1-authentik-test` or `hou1-authentik-prod`, and its state is kept
under `/var/lib/hou1-authentik/{test,prod}`. PostgreSQL uses the private,
internal backend network and has no published port. Only the Authentik server
joins the environment-specific ingress network for the later Nginx work.

Run the capacity and firewall gate from issue #19 first, then deploy an
environment explicitly:

```bash
cd ansible
ansible-playbook playbooks/authentik.yml \
  -e target_hosts=hou1_test -e deploy_environment=test
ansible-playbook playbooks/authentik.yml \
  -e target_hosts=hou1_prod -e deploy_environment=prod
```

The play requires `authentik_secret_key` and
`authentik_postgres_password` to be rendered by the SOPS/age secret workflow.
The environment file is root-owned mode `0600`; persistent PostgreSQL, media,
templates, certificates, and blueprint/configuration directories have separate
roots and restrictive ownership. The server and worker wait for the PostgreSQL
health check, all services restart automatically, logs are capped at 20 MiB ×
3 files, and each service has explicit CPU, memory, and PID limits.

Validate the model and health state before allowing Nginx to reference the
ingress network. A failed Compose health gate aborts the deployment and leaves
the previous project state available for rollback.
