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
### NetBird migration (self-hosted)

The playbook supports staged replacement of the existing WireGuard connection.
Defaults retain WireGuard until NetBird and its access policies are ready.
The control plane runs on the same VPS in its own Compose project, with the
existing application Nginx serving HTTPS. Its data lives outside the app checkout;
only STUN adds a public port (UDP 3478). No second VPS or external identity
provider is required.

Install controller dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
ansible-galaxy collection install -r ansible/requirements.yml
```

First deploy the existing app normally, keeping the new flags disabled. Then
follow [the same-VPS deployment and recovery runbook](docs/netbird-migration.md).
Create a DNS-only public A record for the control-plane hostname pointing to
HOU1. Do not proxy it through Cloudflare or make it depend on NetBird's own DNS.
Keep provider console access and the existing WireGuard client.

Enable the control plane in the relevant environment file first:

```yaml
netbird_control_plane_enabled: true
netbird_domain: netbird.YOUR_DOMAIN
wireguard_enabled: true
netbird_enabled: false
```

Use a different hostname for a test VPS. The role derives
`netbird_management_url` from this hostname, generates persistent server secrets
once, obtains a dedicated DNS-01 certificate using the existing Cloudflare token,
and connects Nginx through a dedicated external Docker network. Review the
`netbird_ingress_subnet` default for conflicts before deployment.

From `ansible/`, provision the control plane over the existing WireGuard path:

```bash
ansible-playbook playbooks/netbird-control-plane.yml -e target_hosts=hou1_test --ask-vault-pass
```

Create the initial owner account at `https://netbird.YOUR_DOMAIN/setup`.
Writes to `/api/setup` are restricted to `netbird_bootstrap_allowed_cidrs`, which
defaults to the existing WireGuard subnet. To bootstrap through WireGuard, use a
temporary local hosts-file entry resolving this hostname to the VPS WireGuard IP;
remove the entry afterward so enrollment uses public DNS. Alternatively configure
your administrator's public IPv4 `/32` before deployment. Normal discovery and
login stay public. Keep the initial owner password in your password manager;
Ansible does not create an owner or output credentials.

Create users, groups, explicit access policies, and one-off server setup keys in
the dashboard. Disable the default All-to-All policy. Now enable the clients:

Set the following per-environment values in `ansible/inventory/group_vars/test_env.yml`
first, then repeat in `prod_env.yml` after testing. Example addresses are placeholders;
use the actual subnet allocated by your self-hosted instance:

```yaml
netbird_enabled: true
netbird_management_url: https://netbird.YOUR_DOMAIN
netbird_subnet: 100.90.0.0/16
# Leave management_vpn at wireguard for the initial parallel rollout.
wireguard_enabled: true
management_vpn: wireguard
```

Store that environment's one-off, non-ephemeral `netbird_setup_key` in Ansible
Vault (use separate test/prod variables or files). Never pass the key with `-e`
on the command line. Enrollment uses a temporary root-only key file, removes it
even on failure, and preserves the existing peer identity on subsequent runs.
The role refuses to switch an enrolled peer to a different management server.
Server DNS management is deliberately disabled; enable private CMS DNS on user
clients through the self-hosted dashboard. Existing resolver configuration is
unlocked and preserved when NetBird is enabled.

From `ansible/`, enroll over the old WireGuard connection:

```bash
ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test --tags netbird --ask-vault-pass
```

Read the enrolled address from the self-hosted dashboard or `sudo netbird status`.
Set `netbird_ip` for that environment, then run the full playbook while keeping
`management_vpn: wireguard`. This adds both private SSH listeners and both CMS
subnets. NetBird uses UDP 51821 to avoid the existing WireGuard port. Configure
NetBird policies for administrators (TCP 22/443) and editors (TCP 443), replacing
the default All-to-All policy. The deployed app template is patched after each
checkout; deployment stops if the expected CMS allowlist has changed upstream.

Verify fresh SSH, CMS, private DNS, unauthorized-user denial, and reboot recovery
using the runbook. Set `management_vpn: netbird` and verify Ansible connects over
it. This also moves Uptime Kuma's private binding on the next deployment.

For this same-VPS topology, keep `wireguard_enabled: true` after the parallel
trial. The guards reject disabling it while the colocated control plane is
enabled: it is the independent SSH recovery path if the NetBird stack or shared
Nginx fails. Deploy application updates normally with the existing playbook; its
Compose override preserves control-plane ingress after checkout.

For installations that later move the control plane elsewhere, the full
playbook still supports `wireguard_enabled: false`: it removes legacy access,
verifies a fresh NetBird Ansible connection, and only then stops the old VPN.
Tagged runs never stop it. The old configuration is retained for console recovery.

To retain WireGuard as an SSH recovery path, leave `wireguard_enabled: true`.
After `management_vpn: netbird`, the WireGuard CMS allowance is removed by
default while its SSH recovery path remains. Set `wireguard_cms_enabled`
explicitly only if you need to extend CMS coexistence. NetBird interface/firewall
policies must be tested with Docker forwarding; host UFW rules alone do not establish CMS authorization.

For rollback, reconnect through WireGuard if it is still active, restore the
previous variables/revision, and run the relevant roles. If WireGuard was stopped,
use the provider console to restore its known-good configuration and rules before
attempting to reconnect. Automated rollback timers are an operator prerequisite
from the runbook, not installed by this PR.

Offline validation (does not touch any server):

```bash
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/hou1.yml --syntax-check
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/netbird-control-plane.yml --syntax-check
# Tests also require Nginx >=1.25.1, OpenSSL and Docker Compose (no servers are started).
python3 -m unittest discover -s tests -v
```

NetBird enrollment intentionally rejects Ansible check mode because package
installation, registration, and actual address checks require a real host.

Control-plane backups are manual: run `/opt/hou1-netbird/backup.sh` as root over
WireGuard or the provider console. It briefly stops only `netbird-server`, archives
its persistent data, encryption secrets, configuration and certificate state, and
restarts it even if archiving fails. Copy the root-only archive to encrypted
off-host storage; retention and off-host upload are operator-managed. Test restore
before relying on NetBird for routine access. See the runbook for restore steps.
