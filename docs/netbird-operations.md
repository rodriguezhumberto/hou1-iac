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

Never reset the owner or delete server data to recover a token. Address changes
require provider-console access: update Vault, release conflicting peers, repair
the client's assigned address through the management API, and rerun normal
provisioning once NetBird SSH works. Rerun `netbird-dns.yml` afterward.

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
The retired standalone `wg0` service is separate.

## Recovery and rollback

Use the provider console when NetBird is unavailable. Log in, inspect
`systemctl status netbird ssh`, `netbird status -d`, and `journalctl -u netbird`.
Repair the daemon or restore `/etc/netbird` from encrypted backup. Do not clone a
peer identity onto a second running host.

If temporary public SSH is needed, use the console to move
`/etc/systemd/system/ssh.service.d/vpn.conf` aside, remove `ListenAddress` lines
from `/etc/ssh/sshd_config`, and add `ListenAddress PUBLIC_IP` in
`/etc/ssh/sshd_config.d/01-console-recovery.conf`. Allow TCP 22 in UFW and the
provider firewall **only from the controller's public IP**. Run `sshd -t`,
`systemctl daemon-reload`, and `systemctl restart ssh`. Use the administrative
user's key. Remove that temporary file and firewall rule after repair, then rerun
`hou1.yml` over NetBird to restore the service override and private listener.

Before upgrades or retirement, use the console to run
`/opt/hou1-netbird/backup.sh`; it briefly stops the management server. Back up
`/var/backups/hou1-netbird`, the controller PAT Vault, and `/etc/netbird` off-host.
Keep the previous infrastructure/app SHAs and an encrypted copy of legacy
`/etc/wireguard` until the retirement checks pass.

To restore the control plane, use the console to stop its Compose project and
restore `/opt/hou1-netbird` with original `secrets.json`, data, configuration,
permissions, certificate lineage, and controller PAT Vault. Start the compatible
pinned versions, verify NetBird SSH, then rerun normal provisioning. Never run two
copies of the same server identity. Restoring a legacy infrastructure revision
alone does not restore deleted VPN configuration or keys.

## Retire existing WireGuard installations

Before retirement, record passing results for **test and production** in issue #4:
NetBird SSH and CMS access, daemon reconnect, forced relay fallback, reboot
recovery, and a provider-console recovery/rollback rehearsal. Confirm no required
service uses `wg0`. These live checks are not covered by the offline suite.

Then run once per environment, test first:

```bash
ansible-playbook playbooks/retire-wireguard.yml -e "target_hosts=hou1_test netbird_retirement_verified=true" --ask-vault-pass
ansible-playbook playbooks/retire-wireguard.yml -e "target_hosts=hou1_prod netbird_retirement_verified=true" --ask-vault-pass
```

This verifies enrollment and fresh SSH, deploys NetBird-only CMS settings, then
removes `wg0`, its configuration, known firewall rules, and standalone packages.
Verify SSH/CMS again after reboot. Remove obsolete WireGuard keys/peer variables
from inventory Vault with `ansible-vault edit`; provisioning no longer reads them.
