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

All future configuration runs route strictly over the encrypted VPN interface.

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

#### Update System WireGuard Configurations Only
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --tags "wireguard" --ask-vault-pass
```

#### Run Everything EXCEPT Application Deployment
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --skip-tags "deploy" --ask-vault-pass
```

#### Selecting a Branch for Playbook to Pull
```bash
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test deploy_branch=cms" --ask-vault-pass
```

### NetBird: shared control plane, test-first clients

NetBird deployment and migration instructions are maintained in the [deployment and recovery runbook](docs/netbird-migration.md). The dedicated control-plane playbook targets the production host; application runs enroll clients only. WireGuard remains the recovery path while test and production access are validated.

See the runbook for prerequisites, Vault credentials, deployment commands,
test-first enrollment, validation, and recovery.
