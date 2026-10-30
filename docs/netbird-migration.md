# Self-hosted NetBird on HOU1

This PR hosts the NetBird control plane on the **same VPS** as the application.
It preserves WireGuard as an independent SSH recovery path. The implementation
is opt-in and has not been applied to a live server.

## Architecture and ownership

- The existing application Nginx continues to publish TCP 80/443. A separate
  public hostname, such as `netbird.example.com`, serves the NetBird dashboard,
  API, embedded local-user identity provider, signal and WebSocket relay.
- A separate Compose project, `hou1-netbird`, runs the combined NetBird server
  and dashboard. Only UDP 3478 (STUN) is published by that project. Backend HTTP,
  metrics and health ports are not published.
- `/opt/hou1-netbird` holds configuration, persistent SQLite/identity data,
  encryption secrets and ingress configuration. It is outside the application
  checkout and is not removed by application Git updates or Compose rebuilds.
- A dedicated external Docker network links application Nginx and the NetBird
  backends. Nginx has a fixed address on it, which is the only trusted HTTP proxy
  address in NetBird. It still belongs to its original application network.
- A managed, untracked `docker-compose.override.yml` adds Nginx's network and
  mounts. The role refuses to overwrite an application-tracked or operator-owned
  override. Normal app deployments preserve the integration after checkout.
- The stable Nginx include loads a directory-mounted configuration. Docker DNS
  and variable upstreams allow Nginx to start while NetBird is offline and recover
  after backend address changes. This avoids app ingress depending on successful
  backend DNS resolution during Nginx startup.
- No optional public NetBird resource proxy, separate Traefik, external identity
  provider or second VPS is installed. An external OIDC provider can be connected
  later; built-in user accounts are sufficient for browser enrollment.

Server and dashboard images are pinned to NetBird 0.82.0 and dashboard v2.96.0.
The server configuration follows the 0.82.0 combined-server installer schema.
Review upgrades and back up state before changing these versions. Features such
as some approval, provisioning and audit capabilities may require a self-hosted
Enterprise license; the basic migration does not depend on those features.

## Deployment prerequisites

1. Test provider browser/serial console or rescue access. Confirm it provides a
   privileged shell despite disabled root/password SSH. Save known-good SSH,
   WireGuard and firewall configuration outside the application checkout.
2. Keep a WireGuard admin device and fresh SSH access to the existing VPS VPN
   address. Colocated control-plane guards reject disabling WireGuard. An SSH
   session inside tmux is useful but does not replace a working recovery path.
3. Provision the application normally once before enabling the control plane.
   Existing Compose, environment and certificate storage must already exist.
4. Check resources: upstream quickstart specifies at least 1 CPU and 2 GB RAM for
   NetBird alone. Add headroom for the existing application and relay traffic.
   Confirm disk space and relay bandwidth as well as memory.
5. Create a public DNS-only A record for a distinct NetBird hostname pointing to
   this VPS. Avoid Cloudflare proxying. Add AAAA only if public IPv6 actually works.
   This hostname must resolve and be reachable before a device can enroll; do not
   depend on the VPN's private DNS to reach its own management service.
6. Permit public TCP 80/443 and UDP 3478 at the provider firewall. The role adds
   host UFW STUN access; existing public web rules remain. Audit effective Docker
   forwarding rules because published ports do not use UFW INPUT alone.
7. Ensure the existing Cloudflare DNS token can issue a certificate for the new
   name. Control-plane issuance is DNS-01 and does not displace the web listener.
8. Review `netbird_ingress_subnet` (default `172.30.80.0/29`) and Nginx's reserved
   address (`172.30.80.2`) against existing networks. Change both before the first
   deployment if they overlap another Docker/VPN/local network.
9. Prepare an on-host rollback timer for changes to SSH/firewall/resolver settings.
   It must run without SSH and restore known-good configuration. Validate its
   actions, re-arm for risky phases, and cancel only after fresh access tests.
   This PR does not install such a timer automatically.

## Commission the control plane before migrating clients

Set these values in the target environment's group vars:

```yaml
netbird_control_plane_enabled: true
netbird_domain: netbird.YOUR_DOMAIN
wireguard_enabled: true
netbird_enabled: false
```

The management URL is derived from this domain unless explicitly overridden.
Use distinct hostnames if commissioning a separate test VPS. Keep the canonical
hostname stable after users and devices have enrolled. Disabling the feature flag
does not tear down existing containers, data, certificates or the managed override;
removal is a separate recovery-tested operation.

From `ansible/`:

```bash
ansible-playbook playbooks/netbird-control-plane.yml -e target_hosts=hou1_test --ask-vault-pass
```

The playbook installs Docker if needed, generates private encryption secrets only
once, starts the separate stack, connects existing Nginx, checks its configuration
and verifies `https://NETBIRD_DOMAIN/api/instance`. It briefly recreates Nginx when
adding the network/mounts. Existing service traffic may see a short interruption.
Routine control-plane config changes use the directory mount and a validated
reload. Review shared-ingress changes in a maintenance window with WireGuard open.

A dedicated DNS-01 certificate is stored alongside the existing certificates.
The existing renewal job renews it too and reloads Nginx. The role uses the same
`/cloudflare.ini` credential path in the issuance container and renewal config.
Keep the application's Cloudflare credential valid for all managed names.

Open `https://NETBIRD_DOMAIN/setup` and create the first owner. Ansible deliberately
does not collect or create that password or enable setup-time API token creation.
POST `/api/setup` is restricted by Nginx before the first account exists, preventing
an internet visitor from claiming the installation. Discovery and normal login
remain public. The write allowlist defaults to the existing WireGuard subnet.

For bootstrap, temporarily resolve the hostname to the VPS's WireGuard IP on your
admin device (a local hosts entry), keeping its HTTPS hostname/certificate intact.
Remove that entry after creating the owner so normal public DNS is used for
subsequent enrollment. Alternatively set `netbird_bootstrap_allowed_cidrs` to your
admin's public IPv4 `/32` before deploying. Never make the endpoint writable from
all of the internet. Store the owner credentials and recovery information in your
password manager, and verify later logins from a non-WireGuard device.

Create local users, server groups, administrator/editor groups and one-off,
non-ephemeral server setup keys. Define the replacement policies before removing
the initial default All-to-All policy:

- Administrators to the appropriate servers: TCP 22 and 443; add 3001 only for Kuma.
- CMS editors to the appropriate servers: TCP 443 only.
- Test access must not imply production access.

NetBird policies are additive: a restrictive rule does not override All-to-All.
Device offboarding requires revoking/removing its peer access; expiring or revoking
its setup key only prevents new enrollments.

## Existing dependencies addressed by the migration

- Standard Ansible inventory currently uses production `10.10.0.1` and test
  `10.10.0.10`. `management_vpn` selects WireGuard or the configured `netbird_ip`.
- Existing SSH listens only on the WireGuard address. The updated security role
  manages explicit private listeners, validates included SSH settings, and orders
  SSH after the enabled VPN services with startup retries for late addresses.
- WireGuard uses UDP 51820; the NetBird peer defaults to 51821 to avoid collision.
  This is separate from the control plane's public STUN port 3478.
- The common role previously overwrote and locked `/etc/resolv.conf`. With the
  NetBird client enabled it unlocks and preserves it; server NetBird DNS management
  is disabled. User clients still need managed private DNS for CMS access.
- Application deployment updates the CMS Nginx allowlist and retains `deny all`.
  Once NetBird is primary, the WireGuard CMS allowance is removed by default,
  while SSH recovery can remain. The deployed template's expected original ACL
  is checked before editing it; an upstream contract change stops deployment.
- Kuma binds to the selected management IP. NetBird cutover moves that binding
  without changing the legacy address needed by recovery WireGuard.
- Application deployments explicitly recreate Nginx so its entrypoint renders
  changed templates and replaces stale bind-mounted files after Git checkout.
  Directus and Astro continue using their existing Docker network.

## Client rollout and CMS routing

1. Set `netbird_enabled: true` and the actual account `netbird_subnet`, leaving
   `management_vpn: wireguard` and `wireguard_enabled: true`. The account CIDR is
   assigned by NetBird; do not assume the example subnet or allow all CGNAT space.
2. Store that environment's `netbird_setup_key` in Vault. Enroll over WireGuard:

   ```bash
   ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test --tags netbird --ask-vault-pass
   ```

   Enrollment uses a root-only temporary key file, removes it even on failure,
   and records verified enrollment for unattended reconnects. It rejects changing
   the management endpoint for an existing peer and checks the real interface IP
   and listening port. Subsequent runs do not need the consumed setup key.
3. Read the peer IP in the dashboard or `netbird status`. Set `netbird_ip` for
   that environment and run the full playbook while WireGuard remains primary.
4. Install clients, select the self-hosted management URL and sign in through the
   browser. Enroll two admin devices and a CMS-only test user. No subnet router or
   exit node is needed for SSH and the CMS.
5. Test fresh SSH to the NetBird IP using the existing user/key and an Ansible ping
   over that address. Confirm fresh WireGuard SSH still works.
6. Keep the current CMS HTTPS names and certificates. Distribute private records
   resolving each CMS hostname to its server's NetBird IP using NetBird Custom
   Zones or an internal DNS server. Avoid shadowing unrelated public site records
   when defining a private zone. Check client OS/browser secure-DNS behavior.
7. Before DNS cutover, test the intended TLS name against the new private address:

   ```bash
   curl --resolve cms-test.example.com:443:NETBIRD_TEST_IP \
     https://cms-test.example.com/admin/
   ```

   Substitute the actual address. Test login, assets, uploads and WebSockets in a
   browser, then repeat using private DNS normally.
8. Test an editor (CMS succeeds, SSH fails), an unauthorized NetBird peer (CMS
   denied), and an external device with VPN disabled (CMS and SSH denied, including
   public IPv6). Public sites and NetBird login/enrollment must keep working.
   Inspect Nginx source addresses and effective NetBird/Docker firewall rules.
   Docker HTTPS uses forwarding/DNAT; UFW's host INPUT rules alone do not establish
   the editor/admin boundary. Never allow a Docker gateway address just to bypass
   the CMS ACL: that can admit public traffic too.
9. With provider recovery available, restart the control plane, client daemon and
   VPS. Test fresh SSH, private DNS, CMS, Kuma and Ansible after reboot, plus client
   reconnect and relay fallback on a network that blocks direct UDP. Shared Nginx
   also waits for the application's health dependency; verify recovery from that
   dependency being slow or unavailable.
10. After several days of real use, set `management_vpn: netbird`, retain
    `wireguard_enabled: true`, and run the full playbook. Verify Kuma's moved binding
    and WireGuard's SSH-only recovery role. Cancel rollback only after fresh access
    succeeds. Repeat the proven process on production.

The generic migration still supports full WireGuard retirement if the control
plane is later moved elsewhere. With colocated hosting enabled, disabling recovery
WireGuard is rejected. Do not advertise the old VPN subnet, Docker networks or a
default route just to reach services on this VPS.

## Backup and recovery

Run `/opt/hou1-netbird/backup.sh` as root over WireGuard or provider console before
upgrades and on a regular operator-managed schedule. It takes an exclusive lock,
requires a running NetBird server, stops only that server for SQLite consistency,
archives its complete directory plus the application's certificate store, and
restarts the server through an EXIT trap even if archiving fails. The peer daemon,
dashboard, application and WireGuard are not stopped. Enrollment/policy changes
and relayed connections can be interrupted during this pause.

Root-only archives are written under `/var/backups/hou1-netbird`. They contain
private keys, secrets, identity data and certificate material. Transfer them to
encrypted off-host storage with an appropriate retention policy. Off-host upload,
retention and scheduling are not automated by this PR; do not treat local copies
as recovery from disk or VPS loss.

To restore:

1. Use WireGuard or provider console. Preserve any failed state for diagnosis.
2. Stop the NetBird Compose project without deleting data or changing secrets.
3. Extract a trusted archive into a root-only staging directory. Restore the
   original server directory, including `secrets.json`, `config.yaml` and `data`,
   to the configured control-plane path with original permissions. Never generate
   replacement encryption keys against existing server data.
4. The archive also contains `conf/`, the certificate store. Restore the required
   NetBird certificate lineage into the application's `certbot/conf` location,
   preserving its archive/live symlinks. Avoid rolling back unrelated application
   certificates that are already valid and newer.
5. Restore public DNS to the canonical hostname/address if the host changed. Do
   not run a second live server with a competing copy of the same identity.
6. Re-run the control-plane playbook using the backed-up compatible image versions,
   validate Nginx and endpoint access, then test enrolled devices and policies.
7. Test this procedure on an isolated replacement instance before relying on it.
   Back up the client peer state and WireGuard recovery configuration separately.

If NetBird fails, connect through WireGuard to fix the stack, public DNS, TLS or
Nginx. If shared host networking or the whole VPS fails, use provider console or
rescue. Do not assume an existing peer connection survives control-plane outage,
expired login, a relay outage or reboot. A separate Compose project does not remove
the shared VPS/Nginx failure dependency.

## Validation and sources

Offline tests render the actual Ansible templates, parse/merge Compose without a
Docker daemon, validate Nginx with a temporary certificate, test persistent secret
generation, and exercise VPN/colocation guards. Live servers, production drift,
provider console, Docker policy behavior, end-user enrollment and restore/reboot
recovery have not been tested. No server has been changed by preparing this PR.

Official sources consulted:

- [Combined-server installer, pinned 0.82.0](https://github.com/netbirdio/netbird/blob/v0.82.0/infrastructure_files/getting-started.sh)
- [Current self-hosted quickstart](https://docs.netbird.io/selfhosted/selfhosted-quickstart)
- [External reverse proxy configuration](https://docs.netbird.io/selfhosted/external-reverse-proxy)
- [Setup API and initial-owner bootstrap](https://docs.netbird.io/selfhosted/automated-setup)
- [Linux installation and enrollment](https://docs.netbird.io/get-started/install/linux)
- [Access control](https://docs.netbird.io/manage/access-control)
- [Setup keys](https://docs.netbird.io/manage/peers/register-machines-using-setup-keys)
- [Custom DNS zones](https://docs.netbird.io/manage/dns/custom-zones)
- [Ports and host firewalls](https://docs.netbird.io/about-netbird/ports-and-firewalls)
