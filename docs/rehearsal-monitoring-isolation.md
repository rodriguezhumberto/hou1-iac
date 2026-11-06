# Monitoring and isolation gates for the test VPS

Infrastructure #27 and staff #39 use the existing isolated stack and a trusted
off-host controller. These controls are opt-in for the private test inventory;
they neither change the production firewall nor replace the identity design.
NetBird stays on 0.82.0 with its embedded issuer and external Authentik provider.

## Egress and credential boundary

`ansible/playbooks/rehearsal-gates.yml` refuses production membership, public
bootstrap access, the production VPN CIDR, and unmanaged DNS credentials. It
installs only `inet hou1_test_isolation`, validates an entire nft transaction,
and replaces that table atomically. It never flushes UFW, Docker or NetBird
rules. Its service is required before Docker startup; no Docker restart is
needed to apply it to a running test stack.

Host output and container forwarding default to deny. Explicit production
IPv4/IPv6 addresses and the production VPN prefix are denied before established
connection or local/update exceptions. Local test addresses, Docker networks,
DNS, host time synchronization, existing NetBird transport and explicit host
package/image update destinations are allowed. Workloads have no general
external HTTP permission. A separate postrouting guard prevents NetBird's
inserted forwarding exceptions from bypassing production denials. Input and
service ACLs continue to require VPN plus application authentication for staff.

Build `rehearsal_deny_cidrs`, `rehearsal_allow_local_cidrs`,
`rehearsal_allow_update_cidrs` and `rehearsal_test_addresses` from the approved
inventory and current trusted DNS results. Include both address families and
all production destinations. The installer rejects a deny set overlapping the
protected test addresses or unrestricted update/local prefixes. Refresh these
inputs before destination or dependency changes; failed/unknown update traffic
stays denied. The controller checks DNS coverage each cycle and raises a critical
alert if a known production name moves outside the configured deny set. A
reserved currently nonexistent name is recorded explicitly and checked again.
This is an address-based network policy, not an HTTP hostname proxy.

NetBird 0.82.0 normally checks its geolocation download endpoint at startup,
even when databases already exist. Before enforcing isolation, the rehearsal
requires nonempty `GeoLite2-City_*.mmdb` and `geonames_*.db` files in its existing
data directory. Restore them from the encrypted test NetBird backup or retain
the files from the initial test bootstrap. Both configuration templates and
the gate role set `server.disableGeoliteUpdate: true` for controller-managed
isolated rehearsals. Geolocation remains enabled against those cached files;
the workload needs no package-host egress exception. Missing/empty cache fails
the gate before configuration or firewall changes. Production defaults are
unchanged. Cache dates and recovery/startup evidence must be recorded; this
does not validate automatic database refresh under isolation.

DNS capability stays on the trusted controller. The fixture preparation command
creates a different host age identity, re-encrypts application-only SOPS data,
removes both plaintext DNS files, and updates the private inventory/recovery
recipient. The host key cannot decrypt the controller bundle or earlier
credential-bearing backups. Preserve both recovery identities privately; issue
new backups under the host recipient before relying on the new restore point.
Do not discard historical recovery keys with encrypted archives still in use.

```bash
python3 scripts/prepare-rehearsal-host-secrets.py \
  --controller-dir /var/tmp/hou1-vps-test

ansible-playbook -i /var/tmp/hou1-vps-test/management.yml \
  ansible/playbooks/rehearsal-gates.yml
```

For `rehearsal_controller_managed_tls: true`, the normal deployment requires a
separate `rehearsal_host_age_key_source` and filtered `authentik_secret_source`.
The webstack/NetBird/Authentik roles require pre-issued test certificates and
skip host DNS credential installation and renewal. A private full-stack rerun
reasserts these gates. Fresh public bootstrap must be followed by the private
gate before test isolation is accepted. Production defaults are unchanged.

For a fresh replacement host, issue the four test certificate lineages on the
controller and encrypt their certificate state plus a fresh NetBird seed under
the separate host recipient. The NetBird seed contains new encryption material
and the two cached geolocation databases, without an old identity database.
After the standard host dependencies are installed, run
`ansible/playbooks/rehearsal-seeds.yml` with the explicit public bootstrap
inventory and `rehearsal_seed_directory`. The entry point refuses production,
existing identity databases, shared controller keys and unrestricted SSH CIDRs.
It permits temporary key-only SSH from the controller's single address while
the stack is rebuilt. Run the full pinned rehearsal, enroll the isolated admin
peer, apply the private gate and prove a cold NetBird restart. Then run the
existing security role, verify private SSH, and remove the temporary bootstrap
SSH rule and drop-in. Public SSH must be closed before accepting the final gate.

## Off-host monitoring and private alert delivery

`rehearsal-monitor.py` runs on the trusted controller, not inside the monitored
VPS. The host probe returns stable rule IDs and booleans only. It checks:

- Authentik server/worker health and readiness, PostgreSQL readiness, Nginx,
  NetBird/dashboard, staff app, discovery, protected API behavior and public site.
- Trusted TLS chain/hostname and at least 21 days remaining for the three
  identity/staff/VPN certificates.
- Memory below 80%, filesystem below 75%, inode usage below 70%, and measured
  device utilization below 70%; backup success age below 26 hours.
- Isolation chains/policies and postrouting deny order, separate host age
  recipient, absence of host DNS credentials, and absence of DNS capability
  from host-decryptable SOPS data.

The controller independently checks public Nginx/Auth/NetBird HTTPS, VPN
management/signal/peer connectivity and real admin/reader authorization-code
login through the isolated VPN peer, protected reads, reader write denial,
callback replay and logout. It also checks DNS deny coverage and controller
renewal status. Credentials pass through captured stdin; codes/cookies/tokens
and Docker environment/logs never enter alert payloads.

Two failed samples fire ordinary rules, three fail staff login, and one fails
isolation/secret/DNS-coverage rules. Recovery sends a resolved event. A durable
outbox retries failed deliveries. Samples run 60 seconds after the preceding
cycle completes; actual detection time includes probe duration. Fast manual
drills validate consecutive samples rather than claiming elapsed-minute SLA.

The user selected a private receiver for this rehearsal. It listens only on
controller loopback, writes mode-0600 event receipts in a mode-0700 directory,
and sends no email/chat/page. It proves off-VPS delivery and recovery, not an
operator notification route. Controller loss also takes down this receiver;
production needs an independently operated alert destination/heartbeat.
These checks do not measure aggregate 5xx rate, real-user login rate, continuous
negative-token acceptance, or retention/alerting at an external monitoring SaaS.

Install into a persistent private controller directory, outside the checkout,
with its own pinned SSH configuration, SOPS ciphertext and controller identities.
The installer copies executable code there so switching Git branches does not
break monitoring. User lingering must be enabled on the controller.

```bash
python3 scripts/install-rehearsal-monitor.py \
  --config /home/dev/.local/state/hou1-vps-test-monitor/monitor.json --enable
systemctl --user status hou1-test-monitor.timer hou1-test-tls.timer
systemctl --user start hou1-test-monitor.service
```

TLS renewal runs twice daily on that controller. `rehearsal-controller-tls.py`
accepts only this fixture's four test lineages. It fetches test certificate
state over pinned private SSH, decrypts the DNS credential only locally, runs
the pinned Certbot image and validates/reloads Nginx after transferring issued
certificate state. Dry-run staging state is never installed. DNS credentials
are never transferred; temporary controller files are removed on completion.
The overlap between landing and primary certificate SANs required a 120-second
DNS propagation wait in the successful rehearsal.

## Live assertions and cleanup

Run mutating drills sequentially on a healthy baseline, with the automatic
monitor timer stopped during deliberate outages:

```text
scripts/rehearsal-isolation-drill.py --config <private-monitor.json>
scripts/rehearsal-alert-drills.py --config <private-monitor.json>
scripts/rehearsal-staff-login-drill.py --config <private-monitor.json>
```

The isolation drill attempts TCP connections to private-config production
addresses and an unapproved benchmark address from the host and all seven
workload namespaces. It requires rejection counters or an internal namespace
without a default route. IPv6 namespaces without routes are recorded as such;
the host IPv6 nft denial is tested separately.

Component drills arm local recovery before stopping any container. Nginx and
NetBird outages can sever the VPN/SSH path; recovery must not depend on SSH.
The runner uses a real-time recovery deadline longer than the armed timer.
The staff-login drill temporarily invalidates only the test client ID, verifies
three actual login failures while app/IdP/Nginx remain healthy, then restores it.
Its root-only Auth API helper captures secret-bearing responses; never invoke
it directly in recorded output. Remove that helper and all recovery jobs after
verification; retain only ongoing monitor/TLS timers and the isolation service.

Preserve redacted alert receipts, immutable implementation refs, config/script
hashes and new encrypted recovery-point hashes in both mirrored rollout issues.
Do not reinterpret earlier evidence as proving these later controls. Provider
console recovery, sustained storage load, scheduled offsite retention and final
production runbooks remain separate gates.
