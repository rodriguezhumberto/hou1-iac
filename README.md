Run commands from `ansible/`. The controller needs Ansible dependencies, its SSH
key, the inventory Vault password, and NetBird administrator membership.

### 1. First initialization: public IP

For a new production host, first create the application ingress and shared
NetBird control plane. Public SSH stays available during this step:

```bash
ansible-playbook playbooks/bootstrap-control-plane.yml -e "manual_root_password=YOUR_TEMP_ROOT_PASS" --ask-vault-pass
```

Join the controller to that NetBird instance and assign it to `administrators`.
Then enroll the node and restrict SSH to NetBird:

```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod_init manual_root_password=YOUR_TEMP_ROOT_PASS" --ask-vault-pass
```

Test uses the existing production control plane:

```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test_init" --ask-vault-pass
```

Use complete runs for initialization. The init targets connect to public IPs;
SSH hardening requires a fresh administrative connection over NetBird to pass,
then switches the rest of that run to the administrative user over NetBird. A
180-second public SSH recovery timer is armed during this cutover and is
removed after the fresh NetBird probe succeeds; if the controller disappears,
the timer restores the original public SSH path for a retry.

### 2. Subsequent runs: NetBird

```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test" --ask-vault-pass
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --ask-vault-pass
```

Both use the fixed NetBird addresses in the encrypted inventory. Keep the
controller connected to NetBird. Public-IP fallback is disabled.

### 3. Targeted operations

Add `--tags deploy` for application updates, `--tags ufw` for firewall and SSH
configuration, or `--skip-tags deploy` to skip application deployment.

Select the application revision with `-e deploy_ref=TAG_OR_SHA`. Legacy
`deploy_branch` remains supported; `deploy_ref` takes precedence. The default is
`main`. Select the infrastructure revision with Git before running Ansible.
See [releases](docs/releases.md) and [CMS backups](docs/cms-storage-backups.md).

### 4. NetBird playbooks

These connect to production over NetBird:

```bash
ansible-playbook playbooks/netbird-control-plane.yml --ask-vault-pass
ansible-playbook playbooks/netbird-dns.yml --ask-vault-pass
```

Reconcile a client's enrollment with the normal environment target and
`--tags netbird`. Established hosts require NetBird SSH; public-IP access is
available only during first initialization through the bootstrap fallback.
See [operations and retirement](docs/netbird-operations.md).
