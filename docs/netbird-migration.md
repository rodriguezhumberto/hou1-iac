# NetBird deployment and recovery

One control plane on production serves both environments. Both servers use their
Vault-declared NetBird addresses for normal application management. WireGuard
remains enabled for enrollment, address repair and control-plane recovery.

## Deployment layout

- `netbird-control-plane.yml` targets only `prod_hou1` in the
  `netbird_control_plane` group, always over WireGuard. Do not pass `target_hosts`.
- `hou1.yml` manages application deployments and clients. It does not update the
  control plane. Both environments use `https://netbird.{{ primary_domain }}`.
- `netbird-dns.yml` enrolls the production host and deploys private DNS there,
  using WireGuard for SSH. It does not change the application's listeners.
- The separate `hou1-netbird` Compose project stores configuration, encryption
  secrets and SQLite data in `/opt/hou1-netbird`. Never regenerate keys for
  existing data.
- Application Nginx serves HTTPS through a dedicated Docker network and a managed
  `docker-compose.override.yml`. Deployments refuse to overwrite an app-owned or
  operator-owned override. App updates can briefly interrupt shared Nginx ingress.
- NetBird publishes UDP 3478 for STUN; backend HTTP, metrics and health ports stay
  private. DNS-01 certificates use the existing Cloudflare token and certificate
  store, with renewal handled by the app's renewal job.
- `netbird_version` pins both server and CLI; APT preferences prevent unattended
  client upgrades. Dashboard and Certbot images have separate pins. Review and
  back up before changing versions.

## Deploy the control plane

Before deploying:

1. Verify fresh WireGuard SSH and provider-console access. Preserve working SSH,
   firewall and application configuration; prepare rollback for network changes.
2. Provision the production application first; its Nginx and certificate store
   are required. Allow a brief Nginx interruption during initial integration.
3. Create a DNS-only A record for the NetBird hostname pointing to production.
   Add AAAA only if IPv6 works end to end. Permit TCP 80/443 and UDP 3478 at the
   provider firewall, and verify Docker forwarding rules and outbound access.
4. Check the Cloudflare token can issue the certificate. Allow capacity beyond
   the app workload (at least 1 CPU and 2 GB RAM for NetBird, plus relay bandwidth).
5. Check `172.30.80.0/29` and the reserved Nginx address `172.30.80.2` do not
   overlap existing Docker, VPN or local networks. Backend containers allocate
   dynamically from `172.30.80.4/30`, leaving the Nginx address outside that pool.
6. Store `netbird_owner_email`, `netbird_owner_name`, and `netbird_owner_password`
   in Vault. NetBird accepts passwords of 8–72 bytes. Never pass credentials on
   the command line.

From the repository root, install controller dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
ansible-galaxy collection install -r ansible/requirements.yml
```

From `ansible/`, deploy:

```bash
ansible-playbook playbooks/netbird-control-plane.yml --ask-vault-pass
```

This deploys the server and ingress without enrolling the production client.
Owner creation uses the private container API; public `/api/setup` requests remain
blocked. Bootstrap requests an owner PAT and saves it immediately to
`ansible/vault/netbird-token.yml` on the controller, encrypted using the Vault
password supplied to this run. Only ciphertext is written to disk (mode `0600`);
the saved file is reused without rewriting it on subsequent runs.

The API sets the shared account subnet to `netbird_subnet` (`100.90.0.0/16` by
default), preserving other settings. A different range can be applied only before
peers are enrolled; later address migrations require explicit operator action.
Both client environments inherit this subnet. Check for overlap with other VPNs
or carrier networks used by your devices before enrolling them.

### Token storage and recovery

Keep a protected backup of the encrypted token file alongside the server backup.
It is separate from inventory and is not automatically committed. Its path can be
overridden with `netbird_token_vault_file`; for named Vault credentials set
`netbird_token_vault_id` and pass the matching `--vault-id` instead of
`--ask-vault-pass`. The token has owner privileges and expires after
`netbird_api_token_expire_days` (365 by default, maximum 365). Changing that setting
does not renew an existing token.

If the owner exists but the token file is missing, restore the file. If the PAT
expired, was revoked, or was lost between creation and persistence, sign in as the
owner, create a replacement PAT, and use `ansible-vault create` (missing file) or
`ansible-vault edit` (existing file) to save this structure at the same path:

```yaml
netbird_management_url: https://netbird.YOUR_DOMAIN
netbird_api_token: REPLACEMENT_PAT
```

Do not reset the owner or delete server state to recover a token. Rerun the
playbook after recovery. Changing owner credentials in inventory does not reset
an existing account; use NetBird's account recovery/password change process.
The setup response cannot be recovered after it is lost, so a crash between
server creation and saving the Vault still requires this recovery procedure.

## Enroll and validate clients

1. The control-plane playbook creates `test-servers`, `prod-servers`,
   `administrators`, and `staff` through the API (`netbird_groups` configures
   the names). Existing groups and peer membership are preserved; removing a name
   from this list does not delete its group. Rerun the control-plane playbook to
   add groups to an existing installation.
   It also reconciles these policies, identified by their `hou1-` names and
   `Managed by hou1-iac.` descriptions:

   | Policy | Source group | Destination groups | Allowed ports |
   | --- | --- | --- | --- |
   | `hou1-administrators` | `administrators` | `test-servers`, `prod-servers` | TCP 22, 443 |
   | `hou1-staff` | `staff` | `test-servers`, `prod-servers` | TCP 443 |
   | `hou1-dns-tcp` | `administrators`, `staff` | `prod-servers` | TCP 1053 |
   | `hou1-dns-udp` | `administrators`, `staff` | `prod-servers` | UDP 1053 |

   Each policy has one rule: NetBird 0.82 omits repeated source groups when
   returning multi-rule policies, preventing reliable readback verification.
   Reruns migrate earlier DNS rules into the dedicated policies above.

   Connections can be initiated from source to destination; reply traffic is
   allowed. Policies do not grant server-initiated access to staff devices.
   After verifying these replacements, the playbook disables the built-in
   All-to-All `Default` policy. Other policies remain untouched and can grant
   additional access. Duplicate managed names, operator-owned name conflicts,
   and customized enabled `Default` policies stop reconciliation for review.
   The required group names above must remain available in `netbird_groups`.

   Assign your device to `administrators` and staff devices to `staff`. Server
   enrollment assigns its environment group automatically. Empty groups
   grant no access. Network groups describe peers/devices, not CMS user accounts.
   HTTPS allows staff to reach the CMS; application credentials control editing.
   Staff-page Nginx restrictions and private DNS must also allow the NetBird path.
   TCP 3001 (Kuma) and ICMP ping are not granted by these policies.
2. The encrypted inventory stores `test_netbird_ip` and `prod_netbird_ip`.
   Each environment maps `netbird_ip` to its own value; both enable NetBird and
   select `management_vpn: netbird`. Run from a controller connected to NetBird
   as an administrator, holding `ansible/vault/netbird-token.yml`, and supply its
   Vault password. No manual setup key is required. Test and production select
   `test-servers` and `prod-servers`, respectively. Keep `wireguard_enabled: true`.
3. From `ansible/`, enroll or repair test over WireGuard before relying on its
   declared NetBird address:

   ```bash
   ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test \
     -e management_vpn=wireguard --tags netbird --ask-vault-pass
   ```

   Test needs outbound connectivity to management, relay and STUN; home-router
   port forwarding is unnecessary. NetBird uses UDP 51821 to avoid WireGuard's
   51820. The controller creates a one-off, non-ephemeral setup key with the
   environment's auto-group and a ten-minute lifetime. The key is passed through a
   root-only temporary file and deleted from the API after the connection attempt;
   the temporary file is also removed on failure. A controller crash can leave an
   unused key until expiration. The administrator PAT stays on the controller.
   Reruns reuse peer identity, verify group membership, and repair missing
   membership while preserving other peers/resources. A peer already in the
   opposite environment group requires explicit correction.
4. Enrollment initially receives an automatically allocated address. The client
   role identifies that peer through its observed address, verifies its environment
   group, then uses `PUT /api/peers/{peerId}` to assign the Vault-declared `netbird_ip`
   if needed. It preserves the peer's name and SSH/login-expiration settings,
   rejects another peer occupying the address, and waits for API, client status
   and interface verification. Reruns keep the same address without another PUT.
   A deleted and re-enrolled peer is assigned the declared address again; this is
   not a reservation before enrollment. Other devices keep automatic addresses.

   Run the full test playbook over NetBird after confirming fresh SSH access:

   ```bash
   ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test --ask-vault-pass
   ```

   Staff ingress uses `netbird_subnet`. Security/SSH tagged runs also run the client
   role to enforce and verify the declared address. Kuma's binding follows
   `management_vpn`. WireGuard SSH remains available; its CMS allowance is removed
   when NetBird is selected for management.

5. Connect an administrator device to the self-hosted endpoint. Verify fresh SSH
   and Ansible access over NetBird and continued WireGuard SSH. Keep a recovery
   session or console available.
6. Deploy private DNS as described below, retaining the existing HTTPS names and
   certificates. Check DNS, login, assets, uploads and WebSockets.
   Verify staff can access CMS but not SSH, unauthorized peers cannot access
   CMS, and non-VPN devices cannot access CMS or SSH, including over IPv6.
   Public websites must keep working. Check actual Nginx source addresses and
   Docker/NetBird rules; UFW INPUT rules alone do not cover forwarded traffic.
7. Test reconnects, relay fallback with direct UDP blocked, and server reboot.
   Repeat access from outside the home network with WireGuard disconnected on
   the test client.
8. For production, first enforce and validate the declared address over WireGuard:

   ```bash
   ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_prod \
     -e management_vpn=wireguard --tags netbird --ask-vault-pass
   ```

   After verifying fresh NetBird SSH access, run the full production playbook:

   ```bash
   ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_prod --ask-vault-pass
   ```

   The dedicated control-plane and DNS playbooks continue using WireGuard.
   Disabling WireGuard is rejected during this migration stage.

### Replacing a server or changing its address

Edit `test_netbird_ip` or `prod_netbird_ip` with `ansible-vault edit
inventory/group_vars/all/secrets.yml`. Use an available address within
`netbird_subnet`, excluding its network, broadcast and NetBird DNS addresses.
Preserve the existing values when replacing a server. If the old peer still owns
an address, remove that obsolete peer explicitly before enrolling its replacement.
The playbook never takes an address away from another peer.

Run the appropriate enrollment command above with `management_vpn=wireguard`.
For an address change or replacement, run the full application playbook with that
same override first so SSH and application listeners are updated while access
still uses WireGuard. Then verify NetBird SSH and return to the normal command.
Rerun `netbird-dns.yml` after changing declared addresses to refresh private DNS.
Only declaring an address in Vault does not apply it to a running server.

## Private DNS

After enrolling the test server, run from `ansible/` on the controller holding the
encrypted API token:

```bash
ansible-playbook playbooks/netbird-dns.yml --ask-vault-pass
```

This enrolls production if necessary, enforces `prod_netbird_ip`, and installs
`netbird-dns.service`. Private records use the declared server addresses from Vault;
DNS no longer discovers server addresses through `/api/peers`. Enroll and validate
test before publishing its records. Additional peers in server groups do not
change DNS answers.

`netbird_dns_records` defaults to `test`, `staff-test`, and `cms-test` under the
Vault-backed `primary_domain`, all pointing to `test_netbird_ip`. For different
names, override `netbird_dns_domain` and `netbird_dns_records` in Vault. Each record
contains `name` and `address`, for example:

```yaml
netbird_dns_records:
  - name: "test.{{ primary_domain }}"
    address: "{{ test_netbird_ip }}"
```

Existing custom records using `peer_group` must be converted to `address`.

The resolver binds only to production's NetBird IP on `netbird_dns_port` (1053),
with TCP/UDP allowed on the NetBird interface. NetBird owns port 53; dnsmasq uses
a separate port published in the nameserver API settings. Non-overridden names
forward to `netbird_dns_upstreams`
(Cloudflare and Quad9 by default). After testing every override over TCP and UDP,
the playbook reconciles access policies and publishes `hou1-private-dns` through
the API. Only `administrators` and `staff` receive this split-domain resolver;
public DNS stays unchanged. Managed nameserver settings are corrected on reruns;
operator-owned conflicts stop deployment. Managed server peers keep DNS disabled
to avoid making server management depend on this resolver.

Remove temporary `/etc/hosts` entries on the Mac, then verify using macOS's system
resolver (plain `dig` does not reliably follow macOS split DNS):

```bash
netbird status
scutil --dns
dscacheutil -q host -a name test.YOUR_DOMAIN
curl --connect-timeout 10 https://test.YOUR_DOMAIN/
```

Expect an available NetBird nameserver and the test peer's address. Keep DNS
management enabled on client devices. Browser-specific secure DNS can bypass the
system resolver; use system DNS for this test. End-to-end client verification must
be performed from an enrolled administrator or staff device.

## Backup and recovery

If an earlier deployment created the ingress network without a dynamic range,
recreate it once before rerunning the updated playbook. On production, stop the
NetBird project and remove the Nginx container and ingress network:

```bash
sudo docker compose -p hou1-netbird -f /opt/hou1-netbird/compose.yaml down
cd /srv/pearlandpaws.com
sudo docker compose rm --stop --force nginx
sudo docker network rm hou1-netbird-ingress
```

Then rerun `netbird-control-plane.yml` from the controller. This interrupts Nginx
until the playbook recreates it; persistent NetBird and application data remain.
Use your configured application directory if different from the example.

Run `/opt/hou1-netbird/backup.sh` as root over WireGuard or console before upgrades
and on an operator-managed schedule. It pauses the server for a consistent SQLite
backup, archives its directory and certificate store, and restarts the server even
if archiving fails. Relay and enrollment traffic may be interrupted.

Archives in `/var/backups/hou1-netbird` contain secrets. Transfer them to encrypted
off-host storage and manage retention; scheduling and off-host upload are not
provided by the playbook. Back up WireGuard and client peer state separately.

To restore:

1. Connect over WireGuard or console, preserve failed state, and stop the NetBird
   project. Stage a trusted backup privately.
2. Restore `/opt/hou1-netbird`, including the original `secrets.json`, `config.yaml`
   and `data`, with original permissions. Never replace encryption keys.
3. Restore the NetBird certificate lineage from the archive's `conf/` into the app
   certificate store. Preserve symlinks and unrelated newer certificates.
4. Restore DNS if the host changed. Do not run competing copies of the same server
   identity. Rerun the dedicated playbook with compatible pinned versions and
   validate enrolled devices and policies.

Test restoration in isolation before relying on backups. Use WireGuard to repair
NetBird and provider console/rescue for host failure. Shared Nginx and the VPS are
common dependencies; peer connectivity is not guaranteed during an outage.

## Offline validation

From the repository root (requires Nginx >=1.25.1, OpenSSL and Docker Compose):

```bash
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/hou1.yml --syntax-check
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/netbird-control-plane.yml --syntax-check
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/netbird-dns.yml --syntax-check
python3 -m unittest discover -s tests -v
```

Tests cover inventory and migration guards, enrollment, SSH/CMS configuration,
Compose/Nginx parsing, persistent secrets, encrypted token persistence, and account
configuration against a local API fixture. They do not deploy services or replace the live access, relay, reboot and
restore checks above.
Install `dnsmasq-base` or set `DNSMASQ_BIN` to run the disposable resolver tests;
these verify TCP/UDP overrides, public forwarding, and absence of public AAAA
fallback for overridden names without changing the machine's DNS settings.

## Sources

- [Combined-server installer, pinned 0.82.0](https://github.com/netbirdio/netbird/blob/v0.82.0/infrastructure_files/getting-started.sh)
- [Self-hosted quickstart](https://docs.netbird.io/selfhosted/selfhosted-quickstart)
- [External reverse proxy configuration](https://docs.netbird.io/selfhosted/external-reverse-proxy)
- [Linux installation and enrollment](https://docs.netbird.io/get-started/install/linux)
- [Access control](https://docs.netbird.io/manage/access-control)
- [Setup keys](https://docs.netbird.io/manage/peers/register-machines-using-setup-keys)
- [Custom DNS zones](https://docs.netbird.io/manage/dns/custom-zones)
- [Ports and host firewalls](https://docs.netbird.io/about-netbird/ports-and-firewalls)
