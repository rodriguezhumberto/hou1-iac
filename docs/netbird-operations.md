# NetBird operations

Run commands from `ansible/`; see [initialization](../README.md#1-first-initialization-public-ip).

## Credentials and addresses

Inventory Vault supplies owner credentials and `test_netbird_ip`/`prod_netbird_ip`.
Enrollment assigns these addresses and rejects conflicts, network/broadcast
addresses, and NetBird's reserved DNS address. Other peers receive automatic IPs.

Back up the generated `ansible/vault/netbird-token.yml` off-host. PATs expire after
365 days by default. Replace an expired PAT with `ansible-vault edit`, then rerun
the control-plane playbook. Its structure is:

```yaml
netbird_management_url: https://netbird.YOUR_DOMAIN
netbird_api_token: REPLACEMENT_PAT
```

Never reset the owner or delete server data to recover a token. Make address
changes through the management API while NetBird SSH is available, then rerun
normal provisioning. Rerun `netbird-dns.yml` afterward.

## Access

Assign devices to `administrators` or `staff`. Enrollment manages server groups.

| Source | Destination | Traffic |
| --- | --- | --- |
| Administrators | Both environments | TCP 22, 443 |
| Staff | Both environments | TCP 443 |
| Both groups | Production DNS | TCP/UDP 1053 |

All-to-All is disabled. Keep NetBird DNS enabled on operator devices. Private DNS
records use `name` and `address`; the resolver binds to production's NetBird IP.
NetBird uses WireGuard internally; its client UDP port is `netbird_listen_port`.

## Recovery and rollback

During first initialization, Ansible arms a 180-second public-SSH fallback timer
before the listener cutover. A fresh NetBird SSH probe cancels and removes it.
If the controller is disconnected during cutover, retry through the original
public IP after the timer restores the saved SSH configuration. The timer is
not installed on later NetBird-only runs.

Established hosts have no password or console recovery path. Keep the controller
connected to NetBird and repair the daemon through NetBird SSH. Do not clone a
peer identity onto a second running host.

```bash
ssh -o ControlMaster=no new_user@NETBIRD_IP sudo -n true
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod" --ask-vault-pass
```

Before upgrades, run `/opt/hou1-netbird/backup.sh` over NetBird SSH;
it briefly stops the management server. Back up
`/var/backups/hou1-netbird`, the controller PAT Vault, and `/etc/netbird` off-host.
Keep the previous infrastructure/app SHAs for rollback.

To restore the control plane, use NetBird SSH to stop its Compose project and
restore `/opt/hou1-netbird` with original `secrets.json`, data, configuration,
permissions, certificate lineage, and controller PAT Vault. Start the compatible
pinned versions, verify NetBird SSH, then rerun normal provisioning. Never run two
copies of the same server identity. Restoring a legacy infrastructure revision
alone does not restore deleted VPN configuration or keys.
