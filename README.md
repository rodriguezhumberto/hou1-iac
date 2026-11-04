Run all commands from `ansible/`.

### 1. Infrastructure Initialization

Use these commands when configuring a brand new instance.
This connects via the public or local network IP address to configure system packages, create the administrative user, setup WireGuard, and lock down SSH.

#### Test Environment Init
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test_init" --ask-vault-pass
```
#### Production Environment Init
Production blocks root login by default, initialization requires providing the temporary root password via an extra variable.
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod_init" -e "manual_root_password=YOUR_TEMP_ROOT_PASS" --ask-vault-pass
```
May still not work, so may need to manually copy keys
```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub username@remote_host
```

### 2. Standard Runs (Post-Initialization)

Standard test and production runs connect over the NetBird addresses declared in
the encrypted inventory. For enrollment or address repair, add
`-e management_vpn=wireguard` to use the recovery VPN.

#### Run Everything Against Test VPS
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test" --ask-vault-pass
```

#### Run Everything Against Production VPS
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --ask-vault-pass
```

### 3. Running Targeted Operations (Using Tags)

Instead of evaluating the entire playbook, tags can target specific tasks.

#### Deploy/Update Application Code Only
Skips system package updates, security lockouts, and Docker installations. Pulls updated repository contents and rebuilds containers.

```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --tags "deploy" --ask-vault-pass
```

#### Troubleshoot or Reconfigure Firewalls Only
Targets only UFW related rules and blocks.
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --tags "ufw" --ask-vault-pass
```

#### Repair Directus Settings

Deployment manages the image preset and CMS read access. Run separately on test:

```bash
ansible-playbook playbooks/directus.yml --check --ask-vault-pass
ansible-playbook playbooks/directus.yml --ask-vault-pass
```

Add `-e target_hosts=hou1_prod` for production. Changes are backed up; users,
content, and licenses are preserved.

#### CMS storage and restore

The deploy role keeps Astro CMS SQLite/media under the host's environment
specific `cms-data` directory and leaves Directus under its existing paths.
Nightly archives are written to `/var/backups/pearlandpaws/<environment>/cms`.
See [the short storage and restore guide](docs/cms-storage-backups.md) for
integrity checks and the rollback-safe restore sequence.

The deployment detects the `directus` Compose service before applying its
transitional settings. This lets a later application release remove the
Directus ingress/service without an infrastructure role failure; until then,
the existing VPN-only ingress and settings reconciliation remain enabled.

#### Update System WireGuard Configurations Only
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --tags "wireguard" --ask-vault-pass
```

#### Run Everything EXCEPT Application Deployment
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --skip-tags "deploy" --ask-vault-pass
```

#### Selecting an Application Branch, Tag, or Commit
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test deploy_ref=cms-rollout" --ask-vault-pass
```

`deploy_ref` selects the application revision. The legacy `deploy_branch` argument
still works; `deploy_ref` takes precedence. The default remains `main`.
Select the infrastructure revision with Git before running Ansible.
See [release and rollback commands](docs/releases.md).

### 4. NetBird Playbooks

#### Enroll or Repair Test Over WireGuard
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test management_vpn=wireguard" --tags "netbird" --ask-vault-pass
```

#### Enroll or Repair Production Over WireGuard
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod management_vpn=wireguard" --tags "netbird" --ask-vault-pass
```

#### Deploy/Update Control Plane
Targets production over WireGuard.
```bash
ansible-playbook playbooks/netbird-control-plane.yml --ask-vault-pass
```

To connect over NetBird instead:
```bash
ansible-playbook playbooks/netbird-control-plane.yml -e 'ansible_host={{ netbird_ip }}' --ask-vault-pass
```

#### Deploy/Update Private DNS
Targets production over WireGuard and uses the Vault-declared server addresses.
```bash
ansible-playbook playbooks/netbird-dns.yml --ask-vault-pass
```

After enrollment, verify NetBird SSH access and use the standard runs above.
See the [operations guide](docs/netbird-operations.md) for maintenance and recovery.
