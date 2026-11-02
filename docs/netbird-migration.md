# Shared self-hosted NetBird with WireGuard fallback

## Current scope

One public control plane on the existing production host serves clients in both
test and production. Client rollout starts in the home-network test environment;
production client access stays on WireGuard until test validation succeeds.
The control plane itself must run on the public production host first.

This phase provides deployment and enrollment support while retaining WireGuard
fallback everywhere. Initial owner/account bootstrap and WireGuard removal are
follow-up work. A fresh deployment serves the dashboard and discovery API but
cannot enroll clients until owner setup, policies and setup keys are available.
No live servers have been changed or tested by this PR.

## Independent deployment targets

- `netbird-control-plane.yml` targets the single `netbird_control_plane` inventory
  member, `prod_hou1`. It is the same production host identity, inheriting the
  existing production and all-host Vault variables. It always connects over that
  host's WireGuard address, even after client inventory cutover.
- `hou1.yml -e target_hosts=hou1_test` configures only test clients/application.
- `hou1.yml -e target_hosts=hou1_prod` configures only production clients/application.
- Application runs do not invoke the control-plane role, render server config,
  update its Compose project or open STUN ports. They preserve the previously
  installed untracked ingress override. Shared Nginx can still restart as part
  of an application update, temporarily interrupting control-plane ingress.
- Test must never be added to the control-plane group. The dedicated playbook
  rejects non-production hosts, multiple members and the `target_hosts` override.

The default shared domain is `netbird.{{ primary_domain }}` and the management URL
is `https://{{ netbird_domain }}`. The base domain comes from existing protected
variables. No literal deployment hostname needs to be committed. Both clients
use the same endpoint and account subnet, but distinct setup keys and peer IPs.

## Production service design

The existing application Nginx continues to publish TCP 80/443. The dedicated
hostname serves the NetBird dashboard, API, embedded identity provider, native
gRPC signal/management and WebSocket relay. A separate `hou1-netbird` Compose
project runs the combined server and dashboard, publishing only UDP 3478 (STUN).
Backend HTTP, metrics and health ports are not published.

Configuration, SQLite/identity data and encryption secrets live under
`/opt/hou1-netbird`, outside the app checkout. Keys are created once with private
permissions; deployment refuses to generate replacements against existing data.
The server and CLI are pinned to 0.82.0; APT preferences prevent unattended client
upgrades. The dashboard uses its own release scheme and remains pinned to v2.96.0.
Version changes should be reviewed and backed up separately from application work.

A dedicated external Docker network connects Nginx and the backends. Nginx has a
fixed address trusted by NetBird. The managed, untracked
`docker-compose.override.yml` preserves the original app network and mounts the
NetBird configuration. Deployment refuses to replace an app-tracked or
operator-owned override. A stable include and directory mount allow validated
reloads, while Docker DNS/variable upstreams let Nginx start if NetBird is offline.
No second public reverse proxy or optional public resource proxy is installed.

A dedicated DNS-01 certificate uses the existing Cloudflare token and certificate
store. The existing app renewal job renews it and reloads Nginx. Initial writes to
`/api/setup` remain restricted to configured administrator CIDRs (WireGuard by
default); this PR does not create owners or collect passwords. Do not open setup
writes to the internet while bootstrap is deferred.

## Before deploying the control plane

1. Confirm privileged provider console/rescue access and fresh WireGuard SSH.
   Preserve known-good SSH, WireGuard, firewall and application configuration.
2. Confirm the production application is already provisioned. Its Nginx, Compose,
   environment and certificate storage are prerequisites for integration.
3. Create a DNS-only A record for the derived hostname pointing to production.
   Do not proxy it through Cloudflare; add AAAA only if IPv6 works end to end.
4. Permit public TCP 80/443 and UDP 3478 at the provider firewall. Verify egress
   and the effective Docker/host firewall rules too; Docker publishes via forwarding.
5. Check the Cloudflare token can issue a certificate for the hostname.
6. Check capacity beyond the application workload: upstream specifies at least
   1 CPU and 2 GB RAM for NetBird alone; allow disk and relay bandwidth headroom.
7. Check the dedicated `172.30.80.0/29` network and reserved Nginx address
   `172.30.80.2` do not overlap existing Docker, VPN or local networks.
8. Prepare a tested on-host rollback timer for risky SSH/firewall/resolver changes.
   This PR does not install the timer. Existing sessions or tmux do not replace
   fresh access tests and console recovery.

From `ansible/`:

```bash
ansible-playbook playbooks/netbird-control-plane.yml --ask-vault-pass
```

This deploys production ingress and the server only. It does not enroll production
or change the selected application management VPN. The role checks Nginx syntax
and the public HTTPS `/api/instance` endpoint. Initial integration can briefly
recreate Nginx; use a maintenance window and keep WireGuard open.

The owner/bootstrap workflow is deferred. Keep clients disabled on a fresh
installation until that workflow supplies accounts, policies and setup keys.
No bootstrap credentials are requested or generated by Ansible.

## Test-first client validation

When an owner and enrollment keys are available:

1. Create distinct `test-servers`, `prod-servers`, administrator and CMS editor
   groups. Grant one-way TCP 22/443 (optionally 3001 for Kuma) to administrators
   and TCP 443 to editors for the appropriate server groups. Remove the default
   All-to-All policy after replacement rules exist; policies are additive.
2. In test vars, enable `netbird_enabled`, set the actual shared `netbird_subnet`,
   and store a test-only one-off non-ephemeral `netbird_setup_key` in Vault.
   Leave `management_vpn: wireguard` and `wireguard_enabled: true`.
3. Enroll from the existing home-network/WireGuard management path:

   ```bash
   ansible-playbook playbooks/hou1.yml -e target_hosts=hou1_test --tags netbird --ask-vault-pass
   ```

   The home-network test environment needs outbound connectivity to the public
   management/signal, relay and STUN services. No public port forwarding or
   LAN-wide exposure is required. Direct NAT traversal or relay carries traffic.
4. Read its assigned address in the dashboard or `netbird status`, set test
   `netbird_ip`, and run the full test playbook. NetBird uses 51821, avoiding
   WireGuard's UDP 51820. The client verifies its endpoint, interface IP and port.
   Temporary key files are removed even on failure; verified identity is preserved.
5. Install a client on an administrator device and select the self-hosted URL.
   Test fresh SSH to the test NetBird IP with the existing OpenSSH user/key and
   an Ansible ping over that IP. Verify fresh WireGuard SSH still works.
6. Keep the current CMS HTTPS name and certificate. Distribute private DNS for
   that name pointing to the test NetBird IP using Custom Zones or internal DNS.
   Avoid shadowing unrelated public site records. Validate client/browser DNS.
7. Test CMS login, assets, uploads and WebSockets; test an editor (CMS succeeds,
   SSH denied), an unauthorized peer (CMS denied), and an external device without
   VPN (CMS and SSH denied, including IPv6). Public websites must remain functional.
   Inspect Nginx source addresses and effective NetBird/Docker forwarding rules;
   UFW's host INPUT listing alone is insufficient. Do not allow a Docker gateway
   source just to bypass the CMS ACL.
8. Test client reconnect, relay fallback when direct UDP is blocked, and test
   environment reboot recovery. Repeat access from outside the home network and
   with legacy WireGuard disconnected on the client. Keep a separate device or
   console available for recovery.
9. Only then select `management_vpn: netbird` for test and repeat the full run.
   Kuma's binding moves; WireGuard SSH remains, while its CMS allowance is removed
   by default. Keep `wireguard_enabled: true`; this phase rejects disabling it.
10. After sustained test use, enroll production separately and repeat validation.
    Updating the control plane never implicitly opts the production client in.

Initial account provisioning and eventual removal of WireGuard fallback belong
to the next issue. Do not treat the deferred bootstrap as completed merely
because the HTTPS discovery endpoint is healthy.

## Backup and recovery

Run `/opt/hou1-netbird/backup.sh` as root over WireGuard or console before upgrades
and on an operator-managed schedule. It locks, requires a running server, stops
only `netbird-server` for SQLite consistency, archives its directory plus the
certificate store, and restarts it through an EXIT trap even if archiving fails.
The peer daemon, WireGuard and application are not stopped. Relayed connections
and enrollment/policy changes can be interrupted while the server is paused.

Archives under `/var/backups/hou1-netbird` contain keys, identities and secrets.
Transfer them to encrypted off-host storage with a retention policy. Scheduling,
retention and off-host upload remain operator-managed; local copies do not cover
VPS/disk loss.

For restore, connect through WireGuard or console, preserve failed state, stop the
NetBird project, and stage a trusted archive privately. Restore the original server
directory including `secrets.json`, `config.yaml` and `data` with original
permissions. Never replace encryption keys for existing state. Restore the
NetBird certificate lineage from the archive's `conf/` into the app certificate
store, preserving symlinks and avoiding rollback of unrelated newer certificates.
Restore canonical public DNS if the host changed; do not run competing live copies
of the same server identity. Re-run the dedicated playbook with compatible pinned
versions and test enrolled devices/policies. Validate restoration in isolation
before relying on the backup. Back up WireGuard and client peer state separately.

One VPS and shared Nginx remain a common failure dependency. If NetBird fails,
repair it through WireGuard. If shared host networking or the whole host fails,
use provider console/rescue. Do not assume peer connectivity survives a control-
plane outage, expired login, relay outage or reboot.

## Validation and sources

Offline tests exercise actual inventory targeting, shared endpoint derivation,
WireGuard guards, enrollment identity checks, SSH/CMS stages, Compose merging,
Nginx syntax and persistent secrets. Live enrollment, production drift, provider
console, effective Docker policies, relay/reboot behavior and restore remain
rollout checks. No live connectivity or deployment claims follow from parser tests.

- [Combined-server installer, pinned 0.82.0](https://github.com/netbirdio/netbird/blob/v0.82.0/infrastructure_files/getting-started.sh)
- [Self-hosted quickstart](https://docs.netbird.io/selfhosted/selfhosted-quickstart)
- [External reverse proxy configuration](https://docs.netbird.io/selfhosted/external-reverse-proxy)
- [Linux installation and enrollment](https://docs.netbird.io/get-started/install/linux)
- [Access control](https://docs.netbird.io/manage/access-control)
- [Setup keys](https://docs.netbird.io/manage/peers/register-machines-using-setup-keys)
- [Custom DNS zones](https://docs.netbird.io/manage/dns/custom-zones)
- [Ports and host firewalls](https://docs.netbird.io/about-netbird/ports-and-firewalls)
