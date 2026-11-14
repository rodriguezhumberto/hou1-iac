# Production operations and releases

Use this runbook for established-host changes. For a lost host, use
[production replacement recovery](production-host-recovery.md); for first-time
identity setup, use [bootstrap](authentik-bootstrap.md). A documentation update
does not authorize deployment, a wipe or a traffic change. Keep secrets, account
lists and customer data out of commands, Git and evidence reports.

## Production status

Current production evidence and remaining handoff limits:

| Area | Accepted result / remaining limit |
| --- | --- |
| Application and access | Owner confirmed public content/images, canonical redirect, Google/VPN/personal SSH, staff/Grafana sign-in/out, dashboard data and Authentik overview. [Cutover evidence](evidence/website-data-migration-2026-11-12.json): 217/217 public checks over 30 minutes. |
| Recovery | [Clean-OS production recovery](evidence/production-host-recovery-2026-11-12.json) and operational handoff passed with corrections. An uninterrupted run remains unproven: [#78](https://github.com/rodriguezhumberto/hou1-iac/issues/78). |
| Backup access | Owner [authenticated decryption](evidence/owner-recovery-access-2026-11-12.json) of the post-import backup and controller bundle locally, without uploading the key or writing plaintext. This did not execute another service restore. |
| Monitoring and renewal | Private collection, reboot recovery, synthetic login, backups and TLS status passed. [Earlier alert-delivery evidence](evidence/production-operations-2026-11-11.json) includes owner-confirmed firing/resolution; that delivery drill was not repeated after replacement. |
| Controller recovery | Scoped renewal jobs operate after temporary admin access retirement. Clean replacement-controller execution remains untested: [#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77). |

Secret consolidation remains [#45](https://github.com/rodriguezhumberto/hou1-iac/issues/45).
Independent HOU4 outage monitoring is deliberately deferred. These accepted
follow-ups do not reopen the completed rollout.

The [tested production release](evidence/production-host-recovery-2026-11-12.json),
[isolated recovery](evidence/hou1-ci-clean-host-recovery-2026-11-10-02.json),
[OIDC acceptance](oidc-security-acceptance-2026-11-07.md) and
[signing-key overlap](jwks-overlap-evidence-2026-11-07.md) retain exact refs and
results. They do not select the next release. Keep test-only recovery guards intact.

The legacy website remains stopped. Retain its node and encrypted copies.
**Reconcile replacement writes before any traffic rollback.**

## Release record and access

Record reviewed values outside Git; no placeholder may remain at execution:

```text
INFRA_REF=<40 lowercase hex commit SHA>
STAFF_REF=<40 lowercase hex application commit SHA>
HOU4_REF=<40 lowercase hex monitoring commit SHA>
APP_IMAGE=<private GHCR repository@sha256:digest>
PREVIOUS_STAFF_REF=<previous validated application commit>
PREVIOUS_APP_IMAGE=<previous retained GHCR digest>
NETBIRD_REF=<official 0.82.0 image digest and configuration revision>
TEST_REHEARSAL=<reviewed evidence path and SHA256>
OIDC_TESTS=<reviewed security evidence path and SHA256>
BACKUP_ID=<production manifest.id>
BACKUP_SHA256=<production manifest.sha256>
RESTORE_DRILL_ID=<successful restore evidence ID>
BREAK_GLASS_CHECK=<console/emergency-admin verification time and owner>
WINDOW=<UTC start/end, operator and escalation contacts>
```

Use clean checkouts at these refs. Manually publish the application from tested
`main`, then select its `image-release.json` receipt. `application_image_revision`,
`deploy_ref` and `STAFF_REF` must agree. HOU1 pulls the digest and never builds the
application. Retain current/previous images and every digest referenced by backups.
See [image inputs and registry access](application-images.md).

```bash
export INVENTORY=/private/production/inventory.yml
export INPUTS=/private/production/release-inputs.yml
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"
export ANSIBLE_HOST_KEY_CHECKING=True
git status --short
git rev-parse HEAD
```

Require a private inventory with pinned ED25519 keys, `StrictHostKeyChecking=yes`,
`IdentitiesOnly=yes` and explicit known-hosts files. The repository inventory's
permissive SSH defaults are unsuitable for production. Retain its group layout:
`prod_hou1` belongs to `hou1_prod`, `prod_env` and `netbird_control_plane`.
Use VPN management after bootstrap and verify independent provider-console access
to both nodes. SSH password policy does not disable console authentication.

Private inputs declare `cms_environment: production`, `authentik_environment: prod`,
`deploy_environment: prod`, VPN network, paths/projects, exact issuers/audiences,
callbacks, image receipt, SOPS paths and backup pairing. Set website hostnames and
optional-site/redirect choices through [routing inputs](website-routing.md).
Network groups, staff roles and monitoring roles are separate grants.

Keep the owner age key offline. Use scoped job identities and temporary deployment
grants; retire temporary access after verifying owner access. Use the
[SOPS/age guide](sops-age-secrets.md) to select controller and component identities.

## Recovery point before a change

The authoritative set is `recovery.age`, `manifest.json` and `receipt.json` on
HOU4. [The protocol](hou4-backup-protocol.md) captures at 02:00 UTC and collects
at 03:00 UTC, with up to five minutes of jitter. Retention is seven daily and
four weekly recovery points. One failed night extends the approximate one-day
loss window. HOU4 does not protect against simultaneous loss of both nodes.

For first installation, preserve the old site's rollback copy before provisioning;
create the first unified point after all components exist and before cutover.
Do not substitute a test point or a legacy component-only success marker.

With verified VPN peers, transport public keys and the pinned source host key:

```bash
ansible-playbook -i "$INVENTORY" ansible/playbooks/backups.yml \
  --limit prod_hou1 -e target_hosts=hou1_prod -e "@$INPUTS"
ssh prod_hou1 'sudo systemctl start --no-block hou1-backup-prepare.service'
```

Capture briefly stops writers and the NetBird server/relay; SSH may disconnect.
Reconnect and require successful service status and fresh
`/var/lib/hou1-backup/last-export.json`, then collect:

```bash
ssh hou4-prod 'sudo systemctl start hou4-backup.service'
ssh hou4-prod 'sudo systemctl show hou4-backup.service -p Result -p ExecMainStatus'
```

Require a new committed point at `/var/lib/hou4-backups/production/hou1/$BACKUP_ID/`.
Copy all three files through pinned operator SSH, compare size/checksum/receipt,
and authenticate decryption locally. For extraction into protected empty storage:

```bash
python3 scripts/verify-recovery.py \
  --archive /private/point/recovery.age --manifest /private/point/manifest.json \
  --identity /private/operator.agekey --output /private/empty-recovery-directory \
  --environment production
```

This writes plaintext and validates the archive; it does not restore services.
Protect/remove output afterward. Transfer checksums alone do not prove decryption
or recoverability. Nightly points use only the owner recipient; a separately
approved drill point may add a temporary recipient. Keep `hou1_backup_enabled: true`
in later deployments to avoid recreating legacy backup cron jobs.

## Established-host deployment

1. Verify refs, console/emergency access, secrets, backup and previous image.
   Placement requires at least two vCPUs, 4096 MiB RAM, 20 GiB free and 200,000
   free inodes; also review measured pressure. Production was tested with 16 GiB RAM.
2. For identity/runtime changes, apply the placement gate and Authentik:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/authentik-preflight.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e "@$INPUTS"
   ansible-playbook -i "$INVENTORY" ansible/playbooks/authentik.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e deploy_environment=prod -e "@$INPUTS"
   ```

   Preflight changes UFW; it is not read-only. Never run
   `authentik-bootstrap.yml` over restored identity data. For reviewed account or
   role changes, follow [admission reconciliation](authentik-bootstrap.md#google-admission-and-staffvpn-assignments).
3. Deploy the selected CI image and matching configuration:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/hou1.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e "@$INPUTS" --tags deploy
   ```

   `webstack_preserve_runtime_environment: true` is for an image-only update with
   an existing `.env`; configuration changes require normal rendering. Existing
   production CMS data came from migration. A genuinely new site needs its own
   initialization source; replacement recovery restores saved data without seeding.
4. Verify staff issuer/audience, PKCE, roles, protected APIs/media, logout/revocation
   and shared-password rejection using the application runbook. Confirm human
   Google login, VPN reconnection and personal-key SSH independently.
5. Reconcile monitoring and backups where affected. HOU1's NetBird control plane
   precedes HOU4 enrollment/pairing. Preserve HOU4 and its independent access.

Do not run the legacy `netbird-authentik-cutover.yml` as a routine production step:
its direct-issuer template conflicts with the supported external-provider contract.
See [the discrepancy](netbird-authentik-cutover.md). NetBird stays pinned at 0.82.0.

Stop on a failed prerequisite; preserve the first error and protected logs.
Record explicitly authorized troubleshooting separately. Do not erase failure
markers or silently resume a failed clean-host attempt.

## Health, monitoring and renewal

Use declared Compose projects/files. For default Authentik paths:

```bash
ssh prod_hou1 'sudo docker compose -p hou1-authentik-prod -f /opt/hou1-authentik/prod/compose.yaml ps'
ssh prod_hou1 'sudo docker compose -p hou1-authentik-prod -f /opt/hou1-authentik/prod/compose.yaml exec -T postgresql pg_isready -U authentik -d authentik'
curl --fail --silent --show-error "$AUTH_ORIGIN/-/health/ready/"
curl --fail --silent --show-error "${STAFF_ISSUER}.well-known/openid-configuration"
curl --fail --silent --show-error "${STAFF_ISSUER}jwks/"
curl --fail --silent --show-error "$NETBIRD_ORIGIN/api/instance"
```

Define these origins privately; `STAFF_ISSUER` includes its trailing slash.
Validate Nginx before reload. Test actual VPN peers, WebSocket/gRPC, protected
staff APIs and public denial of SSH, staff, administration, PostgreSQL and metrics.

HOU4 scrapes over VPN. Grafana SSO uses separate monitoring groups and should open
**HOU1 Operations** with data. Verify sign-in/out and retained emergency login.
Test Discord firing and resolution after receiver/routing changes. The
[synthetic reader probe](staff-login-probe.md) tests local Authentik login, reader
enforcement and logout; it does not test the human Google journey.

| Signal | Operational threshold |
| --- | --- |
| Availability | HTTP/container unavailable for 2 minutes. Invalid-token acceptance, issuer/key mismatch or public staff exposure stops immediately. |
| PostgreSQL / collector | Unavailable for 2 minutes; collector freshness limit 5 minutes. |
| Nginx / Staff login | 5xx or callback failure rate above 1% over 2 minutes, held 2 minutes; three consecutive synthetic login failures stop progression. |
| Callback latency | p95 above 5 seconds over 10 minutes, at least 10 callbacks, held 5 minutes. |
| Capacity | Memory above 80%, disk above 75%, inodes above 70% or device utilization above 70% blocks progression; inspect deployed rule holds. |
| Backup | Failed capture/collection or source age above 26 hours, with 5-minute holds. |
| Renewal | Failed run for 2 minutes; no success within 26 hours for 5 minutes; publication older than 5 minutes for 2 minutes; certificate below 14 days for 10 minutes. |

The Authentik overview needs private API access although sign-in is public.
If it loops to login, compare system DNS with the NetBird proxy before changing
server rules. The owner verified a network-specific macOS **Limit IP Address
Tracking** pause resolved Private Relay interference. This does not establish
that Grafana requires Private Relay to be disabled globally.

[Renewal](controller-tls.md) runs through systemd on the controller, independently
of this thread. Keep the separate scoped renewal peer; never use it for admin work.
Inspect receipts, timer results, VPN/SSH, DNS permissions and served certificates
on failure. Do not fabricate status metrics to clear alerts.

## Rollback and recovery

For application rollback, select `PREVIOUS_APP_IMAGE` and matching
`PREVIOUS_STAFF_REF`, then repeat the tagged deployment and acceptance checks.
Code rollback does not reverse schema, data or identity changes. Historical
migration evidence does not prove compatibility for a new release pair.

For lost-host recovery, preserve HOU4 and follow the separate
[restore/review/publish procedure](production-host-recovery.md). PostgreSQL imports
into empty storage with `ON_ERROR_STOP`; revoke backed-up sessions before writers,
reconcile subsequent access changes and verify HOU4 reconnection, host-key pins,
renewal, monitoring and a fresh off-host point. Keep test-only entry points guarded.

For an identity/VPN incident, follow [break-glass recovery](netbird-break-glass.md).
`break-glass.sh` requires working API and SSH paths; a PAT is not a dashboard login.
The legacy `authentik-rollback.sh` restores configuration only, not the database or
external-provider registrations. Review an exact snapshot before using it.
For user removal, use [audited offboarding](operator-offboarding.md); GUI disable
alone does not invalidate every service's sessions or devices.

## Window closeout

Name infrastructure, application, monitoring and incident owners in the change
record. Escalate unacknowledged pages at 5 minutes, 10 minutes and 15 minutes to
infrastructure, incident command and the service owner respectively.

Close after 30 minutes of healthy probes, correct boundaries, verified fresh
recovery evidence, recorded emergency access and retired temporary grants. Record
exact refs, backup ID, approval, failures and accepted follow-ups. For a future
website migration, use [the cutover checklist](website-routing.md#content-cutover).
