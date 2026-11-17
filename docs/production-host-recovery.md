# Production replacement-host recovery

The production entry points share the test restore engine but require an explicit
production contract. Keep test-only restore, publication and component-drill guards
intact. This procedure does not authorize a wipe or website traffic cutover.

## Latest live result

The [November 12 recovery](evidence/production-host-recovery-2026-11-12.json)
completed a clean-OS restore and operational handoff with corrections. An
uninterrupted run remains unproven: [#78](https://github.com/rodriguezhumberto/hou1-iac/issues/78).
Preserve these manual handoff steps:

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
the playbooks do not change provider DNS or HOU4 automatically.

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
`/var/lib/hou1-host-recovery/<id>`; failed/completed IDs cannot be resumed.

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
DNS starts before the dedicated controller reconnect command and VPN SSH
verification. The existing security role verifies VPN access before retiring
public bootstrap SSH. Failure latches diagnostics and stops restored ingress and
writers; correct the code and use a fresh replacement rather than resuming.

## Operational handoff

After publication, reconnect HOU4 through its independent access path. Reapply
the selected production TLS renewal access and
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
