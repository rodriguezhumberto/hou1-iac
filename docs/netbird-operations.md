# NetBird operations

Run playbook commands from `ansible/`; see [README](../README.md#4-netbird-playbooks).

## Layout and dependencies

- Production hosts the shared control plane for test and production.
- Application runs use Vault-declared NetBird addresses. Control-plane and DNS
  playbooks use WireGuard for recovery.
- `/opt/hou1-netbird` holds the Compose project, SQLite data, configuration and
  `secrets.json`. Preserve its encryption keys when restoring or replacing a host.
- Application Nginx supplies HTTPS through a separate Docker network and managed
  Compose override. The application and its certificate store must exist first.
- DNS-01 certificates use the Cloudflare token; the application's renewal job
  renews them. The public hostname needs DNS-only records pointing to production.
- Public ingress requires TCP 80/443 and UDP 3478. Backend ports stay private.
- The ingress network is `172.30.80.0/29`; Nginx uses `172.30.80.2`, outside the
  backend allocation range `172.30.80.4/30`. Avoid overlapping networks.

## Credentials

Inventory Vault contains owner credentials, `test_netbird_ip` and `prod_netbird_ip`.
Owner credentials initialize a new instance; changing them does not reset an
existing account.

The control-plane playbook creates the controller PAT Vault at
`ansible/vault/netbird-token.yml` on the controller. This generated file is not
part of the repository. Back it up with the server state. The PAT expires after
365 days by default; changing `netbird_api_token_expire_days` does not renew it.

For a missing token file, restore its backup. For an expired, revoked or lost PAT,
sign in as owner, create a replacement, and save it with `ansible-vault create`
or `ansible-vault edit`:

```yaml
netbird_management_url: https://netbird.YOUR_DOMAIN
netbird_api_token: REPLACEMENT_PAT
```

Rerun the control-plane playbook. Never reset the owner or delete server data to
recover a token. For named Vault credentials, set `netbird_token_vault_id` and use
the matching `--vault-id`.

## Server addresses and replacement

Each environment maps `netbird_ip` to its Vault value. Enrollment identifies the
connected peer, assigns that address if needed, and verifies the API and interface.
Other devices receive automatic addresses.

To replace a server or change its address:

1. Edit `test_netbird_ip` or `prod_netbird_ip` in
   `inventory/group_vars/all/secrets.yml` with `ansible-vault edit`. Keep the current
   value for a replacement server. Addresses must be available within
   `netbird_subnet`, excluding network, broadcast and NetBird DNS addresses.
2. Remove the obsolete peer explicitly if it still owns the desired address.
   The playbook rejects conflicts rather than taking another peer's address.
3. Run the full application playbook for that environment with
   `-e management_vpn=wireguard` to enroll and update SSH/application listeners.
4. Verify fresh NetBird SSH access, then return to normal application runs.
5. Rerun the DNS playbook after changing declared addresses.

Changing the account subnet after enrollment requires an explicit address
migration; the playbook refuses to renumber an account with peers.

## Access and private DNS

Assign administrator devices to `administrators` and staff devices to `staff`.
Server enrollment manages `test-servers` or `prod-servers` membership.

| Source | Destination | Allowed traffic |
| --- | --- | --- |
| Administrators | Test and production servers | TCP 22, 443 |
| Staff | Test and production servers | TCP 443 |
| Administrators and staff | Production DNS resolver | TCP/UDP 1053 |

The built-in All-to-All policy is disabled. Other operator-managed policies may
still grant access. CMS authentication remains controlled by the application.

Private DNS records use `name` and `address` fields in `netbird_dns_records`.
Defaults point test hostnames to `test_netbird_ip`. The resolver binds to
production's declared NetBird address; server peers keep NetBird DNS disabled.
Client devices must keep NetBird DNS enabled. Browser secure DNS may bypass it.

## Backup, upgrade and restore

Before upgrades, run `/opt/hou1-netbird/backup.sh` as root over WireGuard or console.
It stops the server for a consistent SQLite backup, archives its directory and
certificate store, then restarts it. This briefly interrupts NetBird services.

Archives in `/var/backups/hou1-netbird` contain secrets. Copy them to encrypted
off-host storage and manage scheduling and retention separately. Back up the
controller token Vault, WireGuard configuration and client peer state too.

Server and client versions, dashboard and Certbot images are pinned. Back up
before changing versions and verify access after deployment.

To restore:

1. Connect over WireGuard or console, preserve failed state, and stop the NetBird
   Compose project.
2. Restore `/opt/hou1-netbird`, including the original encryption keys,
   configuration and data, with original permissions.
3. Restore the certificate lineage from the archive's `conf/` directory into the
   application certificate store. Preserve symlinks and unrelated certificates.
4. Restore the controller token Vault and update public DNS if the host changed.
   Do not run competing copies of the same server identity.
5. Rerun the control-plane playbook with compatible pinned versions. Verify peer
   connectivity, access policies and private DNS.

Test restoration in isolation. Use WireGuard to repair NetBird and the provider
console for host failure; the VPS and application Nginx remain shared dependencies.
