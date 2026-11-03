# NetBird operations

Commands run from `ansible/`; see [README](../README.md#4-netbird-playbooks).

## Credentials and addresses

Inventory Vault contains the owner credentials and the fixed server addresses:
`test_netbird_ip` and `prod_netbird_ip`. Enrollment assigns those addresses to the
matching peers and rejects conflicts. Other peers receive automatic addresses.

The control-plane playbook creates the controller PAT Vault at
`ansible/vault/netbird-token.yml` on the controller. It is generated, untracked,
and must be backed up with the server state. PATs expire after 365 days by default.
For a replacement PAT, save this structure with `ansible-vault create` or `edit`:

```yaml
netbird_management_url: https://netbird.YOUR_DOMAIN
netbird_api_token: REPLACEMENT_PAT
```

Rerun the control-plane playbook. Never reset the owner or delete server data to
recover a token.

## Address changes and replacement

1. Edit the environment address in
   `inventory/group_vars/all/secrets.yml` with `ansible-vault edit`.
2. Remove an obsolete peer if it still owns the desired address.
3. Run the environment playbook with `-e management_vpn=wireguard`.
4. Verify NetBird SSH, then run the normal application playbook.
5. Rerun `netbird-dns.yml` after changing an address.

Addresses must be available within `netbird_subnet` and exclude its network,
broadcast, and NetBird DNS addresses. The playbook never takes an address from
another peer. Changing the account subnet after enrollment requires an explicit
migration.

## Access and DNS

Server enrollment manages `test-servers` and `prod-servers`. Assign devices to
`administrators` or `staff`.

| Source | Destination | Allowed traffic |
| --- | --- | --- |
| Administrators | Test and production servers | TCP 22, 443 |
| Staff | Test and production servers | TCP 443 |
| Administrators and staff | Production DNS resolver | TCP/UDP 1053 |

The All-to-All policy is disabled. Private DNS records use `name` and `address`
fields in `netbird_dns_records`; defaults point test names to `test_netbird_ip`.
The resolver binds to production's fixed address. Keep NetBird DNS enabled on
client devices.

## Backup and restore

Before upgrades, run `/opt/hou1-netbird/backup.sh` as root over WireGuard or console.
It archives `/opt/hou1-netbird` and the application certificate store after a
consistent SQLite backup. Store archives from `/var/backups/hou1-netbird`, the
controller PAT Vault, WireGuard configuration, and client peer state in encrypted
off-host storage.

To restore, stop the NetBird Compose project, restore `/opt/hou1-netbird` with its
original `secrets.json`, configuration, data and permissions, then restore the
certificate lineage and controller PAT Vault. Update public DNS if needed and rerun
the control-plane playbook with compatible pinned versions. Never run two copies of
the same server identity.

Use WireGuard to repair NetBird and provider console access for host failure.
