# Production replacement-host recovery

The production entry points share the test restore engine but require an explicit
production contract. Keep test-only restore, publication and component-drill guards
intact. This procedure does not authorize a wipe or website traffic cutover.

## Latest live result

The [November 12 recovery](evidence/production-host-recovery-2026-11-12.json)
completed a clean-OS restore and operational handoff with corrections. An
uninterrupted run remains unproven: [#78](https://github.com/rodriguezhumberto/hou1-iac/issues/78).
The recorded run needed these manual corrections:

- Correct controller bootstrap/host-key settings and resume the security role.
- Replace HOU1's membership in the renewal destination group.
- Reconnect HOU4 through independent access to discard the old HOU1 WireGuard key.

[Owner-local authenticated decryption](evidence/owner-recovery-access-2026-11-12.json)
verified the updated recovery materials, without executing a replacement controller.
That execution test remains [#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77).
Exact refs, counts, timings, failures and cleanup are retained in the linked evidence.

## Before the replacement

Use pinned infrastructure/application refs and a complete **production** recovery
point: authenticated encrypted archive and manifest, exact source Git bundle,
runtime image lock, saved CI image receipt and separately recoverable read-only
GHCR pull access. A test recovery point cannot seed production. Keep the owner's
age identity off both servers; use owner-local decryption or an explicitly scoped
temporary grant on the controller. Nightly backups retain the owner's recipient.

Confirm independent provider-console access to both nodes before losing the
NetBird control plane. HOU4 remains intact and will need VPN reconnection after
the restored control plane starts. Preserve its SSH host key and the owner's
personal SSH key. Never depend on the controller's VPN session for emergency
access. A different replacement public IP also requires reviewed DNS changes;
the playbooks do not change provider DNS. HOU4 refresh requires the explicit
independent-access configuration below.

Prepare a fresh Debian 12 amd64 replacement with the existing capacity gates and
empty Docker/container stores. Add **only its init inventory name** to a private
`production_recovery` group; it must also belong to `prod_env` and
`hou1_prod_init`, and must not belong to `test_env`. Use strict pinned SSH host
keys. `node_bootstrap: true` and `ansible_host: '{{ prod_vps_public_ip }}'` select
the independent bootstrap path. Keep alerts enabled unless the owner separately
authorizes a maintenance silence.

## Private recovery inputs

Keep inputs outside Git. Set the existing inventory's production domains, roots,
projects and networks, `cms_environment: production`,
`authentik_environment: prod`, `netbird_server_group: prod-servers`, immutable
`deploy_ref`, and `hou1_controller_managed_tls: true`. Supply the controller-only
`application_image_registry_secret_source` and
`application_image_registry_age_key_file` together.

The common recovery inputs match [the clean-host workflow](probes/clean-host-recovery.md):
`host_recovery_archive`, `host_recovery_manifest`, `host_recovery_identity`,
`host_recovery_source_bundle`, `host_recovery_image_lock`, a private absolute
`host_recovery_evidence_directory`, an unused `host_recovery_id`, and an actual
`host_recovery_console_confirmed: true` for both nodes. The root is always
`/var/lib/hou1-host-recovery/<id>`; a failed cold restore requires a fresh host
and ID. Publication transport recovery can resume only after its successful
checkpoint, as described below.

Production also requires `host_recovery_request`. This example describes the
schema; replace every example with the selected private inventory values:

```yaml
host_recovery_request:
  id: replacement-YYYYMMDD-01
  environment: production
  staff_revision: <exact deploy_ref and saved 40-character revision>
  netbird_subnet: 100.90.0.0/16
  server_ip: 100.90.0.10
  bootstrap_cidr: <one controller public IPv4 /32>
  runtime_age_path: /root/.config/sops/age/runtime-prod.key
  origins:
    auth: https://identity.example.invalid
    staff: https://staff.example.invalid
    public: https://www.example.invalid
    netbird: https://vpn.example.invalid
  hostnames:  # complete saved ingress/certificate allowlist
    - identity.example.invalid
    - staff.example.invalid
    - www.example.invalid
    - vpn.example.invalid
  deployment:
    paths:
      app: /opt/site-production
      auth: /opt/hou1-authentik/prod
      auth_data: /var/lib/hou1-authentik/prod
      netbird: /opt/hou1-netbird
      cms: /var/lib/site/production/cms
    projects:
      app: site-production
      auth: hou1-authentik-prod
      netbird: hou1-netbird
    networks:
      auth: hou1-authentik-prod
      netbird: hou1-netbird-ingress
      subnet: 172.30.80.0/29
      dynamic_range: 172.30.80.4/30
      proxy_ip: 172.30.80.2
```

Origins are exact HTTPS origins without paths or trailing slashes. The saved
issuer/callback hosts, public site, VPN subnet/address, certificate hostnames,
Compose paths/projects and external networks must agree. Every root must be a
dedicated canonical nonoverlapping directory; mounts cannot escape them. Only
the declared ingress networks may be external. Production requires the saved CI
artifact receipt and pinned runtime images; no application build runs on HOU1.
The runtime age path must be the host-only identity recorded in the archive,
never the owner's/controller's personal identity.

## Restore, review, publish

Run from the selected infrastructure checkout with private pinned SSH settings:

```bash
ansible-playbook -i "$INVENTORY" ansible/playbooks/restore-production-host.yml \
  -e @"$INPUTS"
```

The playbook waits for cloud-init only if installed, validates capacity, installs
the existing host/runtime prerequisites, verifies the encrypted point, and
performs the cold restore. It imports PostgreSQL only into an empty schema,
revokes restored IdP/staff sessions before writers start, and clears stale VPN
connection flags without replacing identities, keys or group memberships. It
never runs fresh identity bootstrap or CMS seeds. NetBird retains its saved
geolocation updater policy; production can use its normal outbound connection
for updates. Isolated test recovery still requires offline geolocation startup.
Public HTTP/HTTPS and STUN stay bound to loopback; bootstrap SSH is limited to
the declared controller /32.

Review the protected restore report, certificate report and diagnostics. Saved
certificate/key pairs must still cover the declared hostnames and be unexpired;
missing/expired material stops preparation, rather than weakening TLS checks.
Production reinstalls only `live` and `archive` certificate lineages; controller
ACME accounts, DNS credentials and renewal hooks are not reinstalled as runtime
configuration. Reestablish [controller TLS renewal](controller-tls.md) separately.

Reconcile every authorization change since the recovery point: offboarded or
disabled identities, Google allowlist, application roles, VPN groups/devices,
tokens and emergency administrators. Session invalidation does not replay these
administrative changes. While ingress is held on loopback, use the provider
console or a source-restricted SSH tunnel to perform that review.

Only after review set `host_recovery_access_reviewed: true` and declare the exact
`host_recovery_lost_peer_id` and `host_recovery_lost_peer_name`. Publish:

```bash
ansible-playbook -i "$INVENTORY" ansible/playbooks/publish-production-recovery.yml \
  -e @"$INPUTS"
```

Publication requires the same retained restore contract and a successful
production checkpoint. It opens only the existing ingress/STUN ports, verifies
that the pinned lost peer is disconnected and matches its saved name/address,
then replaces only that peer. It preserves the exact existing managed metrics
and backup group IDs/policies and binds only their new HOU1 member. Foreign
members, resource grants or intervening policy changes stop the handoff. Private
DNS starts before the dedicated controller reconnect command, optional HOU4
refresh and VPN SSH verification. The existing security role verifies VPN access before retiring
public bootstrap SSH. A restore/publication-engine error latches diagnostics and
stops restored ingress and writers; use a fresh replacement after correction.
Later peer, group or transport errors preserve their checkpoints for explicit
publication resume.

## Optional transport handoffs

Keep these inputs in the private recovery inventory:

```yaml
# Existing credential-free command for one dedicated controller transport.
# Omit it when no controller reconnect is needed.
host_recovery_management_refresh_argv: [/private/bin/reconnect-dedicated-vpn]

# Separate inventory alias with ansible_host set to this public IPv4.
# Preserve HOU4's admin user/key/port and pin its host key in this file.
host_recovery_companion:
  inventory_host: hou4-recovery
  public_ip: <HOU4 public IPv4>
  known_hosts_file: /private/recovery/known_hosts

# Reuse the exact hou1_tls_peer_config from tls-renewal-peer.yml.
host_recovery_renewal_handoff: true
host_recovery_renewal_peer_id: <existing dedicated renewal peer ID>
```

Each handoff is optional. Before publication, the companion check uses direct
SSH with strict host-key checking, no proxy and no reused connection, and verifies
its existing VPN address/management endpoint. After DNS/controller refresh it
rechecks HOU4, restarts only its `netbird` service, and waits for the same address
to reconnect. No peer identity or persistent monitoring/backup data is removed.
The independent public SSH path must already work; this workflow does not open it.

The renewal handoff pins the existing policy and exact source/receiver groups
before retiring the lost peer. It swaps only HOU1's receiver membership, preserving
other receivers and all policy/group IDs. Changed grants or members stop before
reassignment. Repeating the scoped group-binding step accepts already-bound
members without rewriting them. Publication resume uses the retained handoff
plan; it never guesses a lost identity or deletes another peer.

When either handoff is omitted, complete it manually through independent access.
These choices avoid restarting an operator's VPN or inferring authorization from
an environment name. Historical live evidence predates this automation.

## Resume a published peer handoff

For an interruption after successful ingress publication, keep the same recovery
ID, inputs and controller evidence directory. Inspect the failure, then rerun the
same publication playbook with `host_recovery_resume: true`. Initial runs keep
that option `false`. A latched `first-error.json`, missing/foreign publication
checkpoint or changed request still stops recovery; resume does not bypass the
restore engine's failure protections.

Before deletion, `peer-handoff.json` pins the lost ID/name/address, environment,
management URL and exact group/policy plan. A missing old peer is accepted only
with that plan. If enrollment was interrupted before its receipt, the declared
address must be vacant or match the replacement host's observed local enrollment
and endpoint. The existing client role then verifies enrollment. The private
`replacement-peer.json` pins its exact ID/name/address before groups change.
Conflicting occupants, receipt changes, group members or policies stop recovery.

Before SSH cutover, resume through the original independent bootstrap path. After
cutover, explicitly select `node_bootstrap: false`, `ansible_host: <HOU1 VPN IP>`
and `ansible_user: <existing admin>` with the pinned replacement host key. Both
paths retain the publication playbook's environment and single-host guards. A
repeat verifies the same replacement and repairs the pinned groups, DNS and
selected transports; it does not re-publish ports or delete an inferred peer.

## Operational handoff

After publication, confirm HOU4 connectivity and the renewal receiver membership.
Reapply the selected production TLS renewal access and
schedule, VPN-only monitoring targets/probes, export-only backup transport and
HOU4 collection configuration. Confirm the owner's Google sign-in, VPN and
personal SSH key, deny-by-default admission/roles, public staff/management/metrics
denial, reboot persistence, served certificate checks, and Discord alert firing
and resolution. Collect and verify a fresh off-host point. Retire temporary
controller credentials/grants only after owner access works.

Record restore duration, restored database/authorization counts, matching signing
keys/JWKS and client/discovery settings, negative/session checks, monitoring and
backup evidence, any manual authorization reconciliation, and remaining gaps.
Protect evidence: private logs may contain service details. Never commit secrets
or decrypted payloads. A fresh initial production deployment still needs its
separate staff/CMS initialization; replacement recovery restores saved content.
