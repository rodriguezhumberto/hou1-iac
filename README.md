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

The dedicated control-plane playbook targets only the public production host in
`netbird_control_plane`. That group reuses `prod_hou1` and its existing Vault
variables. Test and production application runs configure clients only; they do
not deploy or update the control plane.

The shared hostname is `netbird.{{ primary_domain }}`, derived from the existing
Vault-backed website domain. Its URL is the same for both environments. Keep the
real hostname in protected variables and DNS, not tracked examples.

This phase retains WireGuard fallback on both hosts. NetBird server and CLI are
pinned to **0.82.0**; the separately versioned dashboard remains at v2.96.0.
Owner-account bootstrap and WireGuard removal are deferred to the next issue.
A fresh control plane cannot enroll clients until an owner and setup keys exist.
Existing installations can use the client enrollment workflow below.

Install controller dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
ansible-galaxy collection install -r ansible/requirements.yml
```

First deploy the production application normally, if not already provisioned.
Create a DNS-only A record for the shared hostname pointing to the public
production host, and permit public TCP 80/443 and UDP 3478. Check provider-console
and WireGuard SSH recovery access before changing shared ingress.

From `ansible/`, deploy the control plane explicitly over production WireGuard:

```bash
ansible-playbook playbooks/netbird-control-plane.yml --ask-vault-pass
```

Do not supply `target_hosts` to that playbook. The target is fixed to
`netbird_control_plane`; it rejects non-production or multiple hosts. It shares
Nginx with the production app, but keeps its own Compose project, persistent state,
and encryption secrets under `/opt/hou1-netbird`. See the
[deployment and recovery runbook](docs/netbird-migration.md) for ingress, TLS and
backup details. Deploying this service on production does not enroll or migrate
the production client.

Once owner setup and access policies are available, enable only the test client
in `test_env.yml` or protected test overrides:

```yaml
netbird_enabled: true
netbird_subnet: 100.90.0.0/16  # Replace with the actual shared account subnet.
wireguard_enabled: true
management_vpn: wireguard
```

Store a one-off, non-ephemeral `netbird_setup_key` in the test environment's Vault
variables. Do not put it on the command line. Both environments inherit the shared
management URL; do not deploy another control plane on the test environment.
The test environment needs outbound internet access; public inbound exposure,
LAN-wide exposure and router port forwarding are not enrollment prerequisites.

```bash
ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test --tags netbird --ask-vault-pass
```

Read the assigned IP in the dashboard or `sudo netbird status`, set `netbird_ip`
for test, and run the full test playbook while keeping WireGuard primary:

```bash
ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test --ask-vault-pass
```

Verify fresh NetBird SSH, CMS/private DNS, unauthorized-peer denial, relay fallback,
reboot recovery, and continued WireGuard SSH. Test client access while disconnected
from the home network and legacy VPN. Only after that, set
`management_vpn: netbird` for test and repeat the full run. This also moves Kuma's
binding. WireGuard SSH stays available, while its CMS allowance is removed by
default when NetBird becomes primary. Leave `wireguard_enabled: true`; disabling
it is rejected during this phase. Keep production client settings unchanged until
test validation succeeds, then enroll production with its own setup key and IP.

Control-plane updates always use its dedicated playbook. Application updates keep
the previously installed Compose override and mounts without managing the server
stack. A shared Nginx restart can still interrupt NetBird ingress briefly.

Offline validation (no live services are deployed):

```bash
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/hou1.yml --syntax-check
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/netbird-control-plane.yml --syntax-check
# Requires Nginx >=1.25.1, OpenSSL and Docker Compose for parser validation.
python3 -m unittest discover -s tests -v
```
