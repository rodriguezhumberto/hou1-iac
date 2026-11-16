# HOU1 infrastructure

This repo contains the Ansible playbooks for HOU1: the app, Nginx, Authentik and
NetBird. App images are built in CI and deployed by digest. HOU4 handles monitoring
and encrypted backups, with its own playbooks. Host security, the NetBird client
and systemd jobs run directly on the OS.

The [docs index](docs/README.md) lists the guides. For routine changes, use the
[production runbook](docs/production-oidc-runbook.md). To replace a lost host, follow
[production recovery](docs/production-host-recovery.md).

## Before running Ansible

Check out the infrastructure commit you intend to deploy. The controller needs
the [Ansible dependencies](requirements-dev.txt) and
[collections](ansible/requirements.yml). Keep private inventory, release inputs,
credentials and SSH configuration outside Git.

Use a private inventory with pinned SSH host keys for production; the example
inventory disables strict checking. Secrets use controller-side SOPS/age.
Select the environment's deployment identity and keep passwords and tokens out
of command arguments.

The examples below run from the repository root:

```bash
export INVENTORY=/private/production/inventory.yml
export INPUTS=/private/production/release-inputs.yml
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"
export SOPS_AGE_KEY_FILE=/private/production/inventory-prod.key
export ANSIBLE_HOST_KEY_CHECKING=True
```

## Updating a host

Connect the controller to NetBird, then run:

```bash
ansible-playbook -i "$INVENTORY" ansible/playbooks/hou1.yml \
  -e target_hosts=hou1_prod -e "@$INPUTS"
```

Use `--tags deploy` to update the app, `--tags ufw` to apply firewall/SSH settings,
or `--skip-tags deploy` to skip the app. `deploy_ref` must match the selected
image revision; it is not enough on its own. See [image inputs](docs/application-images.md).

`netbird-control-plane.yml` and `netbird-dns.yml` manage the control plane and DNS.
Use `hou1.yml --tags netbird` to check and update client enrollment. These also
run over the VPN.

## New or replacement hosts

For a lost production host, use [replacement recovery](docs/production-host-recovery.md);
do not run fresh identity bootstrap over restored databases.

For a new deployment, prepare the private inventory, CI image receipt,
certificates and identity settings first. `bootstrap-control-plane.yml`
creates ingress and NetBird over public SSH. Join the controller to that control
plane, then run `hou1.yml` against `hou1_prod_init` to enroll HOU1 and restrict SSH.
Run initialization without tags. Follow the production runbook to finish
Authentik, user admission and app setup, then enroll HOU4 and connect monitoring
and backups. Verify provider-console access before restricting SSH.

During initial SSH cutover, a 180-second fallback timer restores bootstrap access
if the VPN probe fails. It is removed after success and is not an ongoing recovery
path. See [NetBird operations](docs/netbird-operations.md).
