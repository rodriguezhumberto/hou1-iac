# HOU1 infrastructure

Ansible provisions the application, ingress, Authentik and NetBird on HOU1.
Application images are built in CI and deployed by digest. HOU4 provides private
monitoring and encrypted off-host backup collection; its deployment is maintained
separately. Host security, the NetBird client and systemd jobs run on the OS.

Start with the [documentation index](docs/README.md). The
[production runbook](docs/production-oidc-runbook.md) covers releases and acceptance;
[production recovery](docs/production-host-recovery.md) covers replacing a lost host.

## Before running Ansible

Use a clean checkout at a reviewed infrastructure commit, a controller with the
[Ansible dependencies](requirements-dev.txt), and the collections in
[ansible/requirements.yml](ansible/requirements.yml). Keep inventory, release
inputs, credentials and SSH configuration outside Git.

Production runs require a private inventory with pinned SSH host keys. The
repository inventory disables strict checking and is not a production access
profile. Use SOPS/age and supply `--ask-vault-pass` where legacy Vault inputs are
loaded. Never pass passwords or tokens in command arguments.

The examples below run from the repository root:

```bash
export INVENTORY=/private/production/inventory.yml
export INPUTS=/private/production/release-inputs.yml
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"
export ANSIBLE_HOST_KEY_CHECKING=True
```

## Established hosts

Connect the authorized controller to NetBird before running:

```bash
ansible-playbook -i "$INVENTORY" ansible/playbooks/hou1.yml \
  -e target_hosts=hou1_prod -e "@$INPUTS" --ask-vault-pass
```

Use `--tags deploy` for an application update, `--tags ufw` for firewall/SSH
reconciliation, or `--skip-tags deploy` to omit application deployment.
`deploy_ref` must match the selected image revision; a source ref alone no longer
selects a deployable release. See [image inputs](docs/application-images.md).

The dedicated `netbird-control-plane.yml` and `netbird-dns.yml` playbooks manage
the existing control plane and DNS. Use `hou1.yml --tags netbird` for client
enrollment reconciliation. These operations require private management access.

## New or replacement hosts

For a lost production host, use [replacement recovery](docs/production-host-recovery.md);
do not run fresh identity bootstrap over restored databases.

For a genuinely new deployment, prepare the private inventory, CI image receipt,
TLS material and identity inputs before bootstrap. `bootstrap-control-plane.yml`
creates ingress and NetBird over public SSH. Join the controller to that control
plane, then run `hou1.yml` against `hou1_prod_init` to enroll HOU1 and restrict SSH.
Initialization requires full runs without tags. Complete Authentik/admission and
application provisioning using the production runbook, then enroll HOU4 and pair
monitoring/backups. Verify independent console access before restricting SSH.

During initial SSH cutover, a 180-second fallback timer restores bootstrap access
if the VPN probe fails. It is removed after success and is not an ongoing recovery
path. See [NetBird operations](docs/netbird-operations.md).
