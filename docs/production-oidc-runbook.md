# HOU1 production Authentik and OIDC runbook

The release operator and platform/application owners use this procedure together.
It records the tested workflow and the remaining production gates. Updating this
runbook does not promote the rehearsal host or authorize a production deployment.
Never put passwords, tokens, webhook URLs, allowlists or private keys in a release
record, command argument, PR or evidence report.

## Evidence and current production gates

The merged infrastructure baseline is
`f3edfcf08ea3e444f967adf078bf2899aa36f1d0`. The rehearsal used application revision
`a0e01a9d129eb15c1a260d6eba01cb0e3a2b8d51` and image
`ghcr.io/rodriguezhumberto/pearland-paws/app@sha256:2c481222b96799cebb53c8dd59321d812ccd9b98042500241e2c2a131a93cf42`.
These identify evidence, not an automatic production selection.

[Clean-host evidence](evidence/hou1-ci-clean-host-recovery-2026-11-10-02.json)
records 187 restored PostgreSQL tables, 13 matching authorization tables,
29 isolated recovery checks, 23 host checks, seven staff checks, Google/VPN/
personal SSH access, reboot, eight monitoring targets, Discord outage/resolution
and fresh off-host collection. The selected test point was
`20261110T230028Z-184f657a1db7`; **it must not seed production**. The final rebased
[CI run](https://github.com/rodriguezhumberto/hou1-iac/actions/runs/42939168372)
passed before PR #61 merged. See also [OIDC security acceptance](oidc-security-acceptance-2026-11-07.md)
and [signing-key overlap evidence](jwks-overlap-evidence-2026-11-07.md).

Production approval remains blocked until the following have production inputs
and a reviewed, validated procedure at the selected release ref:

| Gate | Current implementation and required work |
| --- | --- |
| Google admission | [Production identity provisioning](authentik-bootstrap.md#google-admission-and-staffvpn-assignments) supports environment-bound Google source, encrypted allowlist and staff/VPN assignments, with offline security tests. Supply a separate production Google client/secret, validate permitted Google egress, and prove live deny-by-default admission, roles and VPN access before enabling production login. No production validation is claimed. |
| Monitoring | [Environment-bound collectors](environment-monitoring.md) support explicit test/production profiles and Compose service discovery with VPN-only TCP/9100. Offline checks cover both environments; supply production HOU4 targets/probes and synthetic identities, then prove live private scrapes, public denial, login checks and Discord firing/recovery before clearing this gate. |
| Controller TLS | [Controller-managed issuance and renewal](controller-tls.md) supports explicit environment lineages, SOPS-protected ACME state, bootstrap import and a VPN-only renewal account. Offline certificate/rollback/alert tests do not clear this gate: supply the production DNS-only grant, controller ownership and pinned transport, then prove ACME dry-run, served certificates and Discord failure/recovery. |
| Replacement recovery | [Production replacement recovery](production-host-recovery.md) provides separate restore/publication entry points with explicit inventory/backup boundaries and CI image receipts. Offline validation does not clear this gate: supply a complete production recovery point, independent consoles and scoped credentials, then prove restore, access reconciliation and operational handoff. The test entry points retain their safeguards. |
| Release inputs | Confirm production addresses/domains, client IDs/redirects, certificates, secrets, administrative device, owner recovery access and staff/CMS initialization. Existing rehearsal inputs and temporary grants are retired. |

Keep the remaining test-only guards intact. Production identity provisioning
does not enable the other production paths. Independent HOU4 outage monitoring
remains deliberately deferred.

## Release record and access

Record actual production values before the window; no placeholder may remain:

```text
INFRA_REF=<40 lowercase hex commit SHA>
STAFF_REF=<40 lowercase hex application commit SHA>
HOU4_REF=<40 lowercase hex monitoring commit SHA>
APP_IMAGE=<private GHCR repository@sha256:digest>
PREVIOUS_STAFF_REF=<previous validated application commit>
PREVIOUS_APP_IMAGE=<previous retained GHCR digest>
NETBIRD_REF=<official 0.82.0 image digest and configuration revision>
TEST_REHEARSAL=<reviewed evidence path and SHA256>
OIDC_TESTS=<mirrored security evidence path and SHA256>
BACKUP_ID=<production manifest.id>
BACKUP_SHA256=<production manifest.sha256>
RESTORE_DRILL_ID=<successful restore evidence ID>
BREAK_GLASS_CHECK=<console/local-emergency-admin verification time and owner>
WINDOW=<UTC start/end; release operator and escalation owners>
```

Use clean infrastructure and application checkouts at those immutable refs.
Publish the private application image manually from tested `main` in CI, then
select its `image-release.json` receipt. Image publication does not deploy.
`application_image_revision`, `deploy_ref` and `STAFF_REF` must agree; the receipt
must bind `APP_IMAGE` to that revision. HOU1 never builds the application.
Retain every digest referenced by backups, plus the current and previous release.
Cold recovery depends on GHCR and separately recoverable read-only pull access.
Use the exact variable names and receipt checks in [image deployment](application-images.md).

Prepare private inventory, SSH configuration and release inputs outside Git:

For a single public website with a canonical alias redirect, use the
[website routing inputs](website-routing.md): explicitly disable the secondary
site and declare the public hostname list and redirect switch. Keep selected
domains in encrypted release inputs, cover HTTPS aliases in the public
certificate, and override the selected identity profile's issuer hostname when
it differs from the shared default. A routing change requires normal runtime
rendering rather than the image-only preserve mode.

```bash
export INVENTORY=/private/production/inventory.yml
export INPUTS=/private/production/release-inputs.yml
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"
export ANSIBLE_HOST_KEY_CHECKING=True
git status --short
git rev-parse HEAD
```

The checkout SHA must equal `INFRA_REF`. The private inventory retains the
repository's group layout/defaults: `prod_hou1` belongs to `hou1_prod`, `prod_env`
and the dedicated `netbird_control_plane` group. Require pinned ED25519 host
keys, `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes` and explicit known-hosts
files; the repository inventory's permissive SSH defaults are unsuitable here.
Use VPN management after bootstrap. Keep provider-console access to **both**
nodes independent of NetBird and verify the owner's personal SSH key before
retiring any temporary deployment grant. SSH password login policy is separate
from provider-console authentication; do not remove the only console credential.

Required release inputs include `cms_environment: production`,
`authentik_environment: prod`, `deploy_environment: prod`, the production VPN
network (`100.90.0.0/16`), app paths/projects, issuer/audience/redirects, pinned
image and revision, SOPS source/key paths, and unified-backup pairing variables.
Review all deployment inputs privately. Keep the Google allowlist encrypted;
Google verifies identity and Authentik controls access across applications.
Network staff access and application roles remain separate grants.

Use SOPS/age for the new secret bundles. Existing Ansible Vault inputs are still
required where the selected inventory loads them; supply `--ask-vault-pass` in
those runs. Consolidation is separate work, not an accomplished migration.
The owner identity stays offline. A temporary scoped deployment recipient may
be added with the owner retained; retire it after verified owner access and
owner-only handoff. Preserve separate scoped identities for ongoing jobs.

## Backup verification before a change

For a first installation, preserve a verified rollback backup of the existing
production baseline before changing it. The unified capture below requires all
three new components to exist; create and verify its first production point
after provisioning and before production traffic/cutover. An empty host or a
test recovery point cannot satisfy the production backup gate.

The authoritative recovery set is the encrypted HOU1 export collected by HOU4:
`recovery.age`, `manifest.json` and `receipt.json`. Legacy standalone Authentik
archives and success markers do not prove this unified point exists.
HOU1 captures at 02:00 UTC; HOU4 collects at 03:00 UTC, each with up to five
minutes jitter and persistent missed-run handling. Retain seven daily and four
weekly points. A failed capture extends the approximate one-day data-loss window.

Pair the established production VPN peers through
[the backup protocol](hou4-backup-protocol.md), using only the HOU4 transport
**public** key and the verified HOU1 server public key. Then run:

```bash
ansible-playbook -i "$INVENTORY" ansible/playbooks/backups.yml \
  --limit prod_hou1 -e target_hosts=hou1_prod -e "@$INPUTS"
ssh prod_hou1 'sudo systemctl start --no-block hou1-backup-prepare.service'
```

The local job briefly stops writers, including the NetBird server/relay; SSH
may disconnect. Reconnect and require a successful service result and fresh
`/var/lib/hou1-backup/last-export.json`. Keep `hou1_backup_enabled: true` in
future deployments so legacy schedules are not recreated. On HOU4:

```bash
ssh hou4-prod 'sudo systemctl start hou4-backup.service'
ssh hou4-prod 'sudo systemctl show hou4-backup.service -p Result -p ExecMainStatus'
```

Require success and a new committed point under
`/var/lib/hou4-backups/production/hou1/$BACKUP_ID/`. Copy its three files through
the pinned private operator path to `/private/point/`. Verify size/SHA256 against
the manifest and matching receipt, then authenticate decryption offline:

```bash
python3 scripts/verify-recovery.py \
  --archive /private/point/recovery.age --manifest /private/point/manifest.json \
  --identity /private/operator.agekey --output /private/empty-recovery-directory \
  --environment production
```

This validates/extracts; it does **not** restore a service or prove login works.
Output contains plaintext secrets: protect and remove it after validation.
A HOU4 checksum receipt proves transfer integrity, not decryption or application
recoverability. Approval requires successful production recovery validation too.
The normal point uses only the owner's recipient. A separate authorized test
point may add a temporary recipient; do not change nightly recipients for a drill.
HOU4 protects against losing HOU1, not both nodes; it is not immutable storage.

## Deployment order and stop points

After resolving the production gates above, use these entry points on an
**established production host** with existing private management and ingress.
A new-host production bootstrap needs its own approved sequence; the test
recovery entry point cannot provide it.

1. Verify consoles, immutable refs, SOPS/Vault access, production restore point,
   previous image, emergency account and capacity. The placement minimum is two
   vCPUs, 4096 MiB RAM, 20 GiB free and 200,000 free inodes. Check measured memory,
   filesystem, inode and I/O pressure; the rehearsal host had 16 GiB RAM.
2. Run the placement/firewall policy, then Authentik/PostgreSQL and ingress:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/authentik-preflight.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e "@$INPUTS"
   ansible-playbook -i "$INVENTORY" ansible/playbooks/authentik.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e deploy_environment=prod -e "@$INPUTS"
   ```

   The preflight applies UFW policy; it is **not** a read-only probe. Keep console
   access throughout. Authentik stays on HOU1 behind separate container networks,
   resource/host-capacity checks, private management and encrypted recovery.
3. For a genuinely new identity database only:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/authentik-bootstrap.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e deploy_environment=prod -e "@$INPUTS"
   ```

   Finalize production Google
   admission and client/claim configuration through the production procedure
   required above. Never bootstrap over restored identity data.
4. Deploy the selected CI image and matching configuration:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/hou1.yml \
     --limit prod_hou1 -e target_hosts=hou1_prod -e "@$INPUTS" --tags deploy
   ```

   Use `webstack_preserve_runtime_environment: true` only for a reviewed image-only
   update with an existing `.env`. Initial provisioning renders production settings.
   Staff/CMS content currently needs the owner's recorded manual initialization
   source/steps; do not assume a seed command or restore test content. Authentik
   and NetBird state come from provisioning or a verified restore, not CMS seeding.
5. Verify staff login, exact issuer/audience, PKCE, groups/roles, protected APIs,
   private media, logout/revocation and shared-password rejection. Follow the
   [staff runbook](https://github.com/rodriguezhumberto/pearland-paws/blob/main/docs/production-oidc-runbook.md).
6. Run the reversible NetBird cutover with its dedicated target group and the
   approved compatibility result in the private inputs:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/netbird-authentik-cutover.yml \
     --limit prod_hou1 -e "@$INPUTS"
   ```

   For this command, the input file must **omit `target_hosts`**. It must include
   `netbird_authentik_compatibility_passed: true`, the exact production issuer,
   `netbird-prod` client and existing on-host client-secret file. Retain NetBird
   0.82.0; no upgrade decision follows from this deployment.
7. Reconcile production monitoring and backup policies after HOU1 is enrolled.
   HOU1's NetBird control plane precedes HOU4 VPN enrollment and metrics/backup
   pairing; deploy neither as a dependency cycle. Retained HOU4 can reconnect to
   restored state, with its independent console available if it does not.

Stop on a failed playbook or gate, preserve its first error and protected logs,
and record the fix. Never erase failure markers or silently resume a failed
clean-host attempt. An operator-authorized troubleshooting run is a separate
record. No dependent change proceeds while a prerequisite has failed.

## Health, monitoring and renewal

Use the declared production Compose files/projects, never an implicit working
directory. For the default Authentik paths:

```bash
ssh prod_hou1 'sudo docker compose -p hou1-authentik-prod -f /opt/hou1-authentik/prod/compose.yaml ps'
ssh prod_hou1 'sudo docker compose -p hou1-authentik-prod -f /opt/hou1-authentik/prod/compose.yaml exec -T postgresql pg_isready -U authentik -d authentik'
curl --fail --silent --show-error "$AUTH_ORIGIN/-/health/ready/"
curl --fail --silent --show-error "${STAFF_ISSUER}.well-known/openid-configuration"
curl --fail --silent --show-error "${STAFF_ISSUER}jwks/"
curl --fail --silent --show-error "$NETBIRD_ORIGIN/api/instance"
```

Record `AUTH_ORIGIN`, `STAFF_ISSUER` (including its final slash),
`NETBIRD_ORIGIN`, `STAFF_ORIGIN`, production container names and dashboard URL
in the private release inputs. Run `nginx -t` inside the declared Nginx container
before reload. Verify WebSocket/gRPC/CLI callback and actual peer connectivity.
Public staff and Authentik administration must be denied; public SSH, PostgreSQL
and metrics must be inaccessible. Test from a device outside the VPN as well as
from approved staff and administrative peers.

HOU4 runs containerized Prometheus/Grafana/Alertmanager behind private HTTPS.
The dashboard is **HOU1 Operations**; metrics travel over the VPN, with HOU4 the
only metrics source allowed by policy. Discord is the current receiver. Record
actual target/rule IDs and verify firing **and resolved** delivery; HTTP
acknowledgment proves Discord acceptance, not that a person read the message.
Use the optional [environment-bound synthetic reader probe](staff-login-probe.md)
with a separate SOPS credential bundle. Its code and companion HOU4 rules are
checked offline; provisioning the selected identity, enabling private TCP/443,
and proving fresh login plus Discord firing/resolution remain live gates. The
probe covers local Authentik login, reader enforcement and logout; independently
verify a human Google login and VPN connection. Deployment requires a fresh probe
success after container reconciliation. Never use real Google passwords in it.

| Signal | Current rule / stop threshold to validate in production |
| --- | --- |
| Authentik/NetBird/Nginx availability | HTTP probes or metrics down for 2 minutes; failed container health for 2 minutes. Any invalid-token acceptance, issuer/key mismatch or public staff exposure stops immediately. |
| PostgreSQL / collector | `Hou1PostgresqlUnavailable` and `Hou1CollectorUnavailable`, 2-minute hold; collector freshness limit 5 minutes. |
| Nginx / Staff login | 5xx rate or callback failure rate above 1% over 2 minutes with a 2-minute hold; three consecutive synthetic login failures stop immediately. |
| Callback latency | p95 above 5 seconds over 10 minutes, at least 10 callbacks, sustained 5 minutes. Preserve the approved low-volume filter. |
| Capacity | Memory above 80%, disk used above 75%, inode used above 70% or device utilization above 70% blocks progression; inspect the deployed rule holds. |
| Backup | Failed capture/collection or source age above 26 hours; require both local and off-host evidence, with 5-minute alert holds. |
| Renewal | Failed renewal for 2 minutes, last success older than 26 hours for 5 minutes, publication older than 5 minutes for 2 minutes, certificate below 14 days for 10 minutes. |

Renewal is a controller job, not work performed by an agent staying in this
thread. Record the production controller, timer/service names, credential owner
and recovery handoff. The rehearsal's HOU4 publisher recovered using genuine
receipts after the VPN returned; no forced issuance was needed. Inspect receipts,
timer results, VPN/SSH, DNS-01 permissions and served certificate before retrying
the normal renewal/publisher service. Use the recorded controller certificate
rollback if installation fails. Do not fabricate metrics to clear an alert.

## Rollback, disaster recovery and emergency access

For a code rollback, select `PREVIOUS_APP_IMAGE` and its matching
`PREVIOUS_STAFF_REF` in private inputs, then rerun the same tagged application
deployment. Verify the saved digest receipt and all login/role/public-boundary
checks. A code rollback does not undo SQLite/schema/media or identity changes.
The recorded migration rollback used the retained pre-CI image; rollback between
two published CI releases still needs evidence for that release pair.

A failed NetBird cutover health gate rolls configuration back automatically.
For a later rollback, use the exact snapshot recorded at cutover:

```bash
ssh prod_hou1 'sudo /opt/hou1-netbird/authentik-rollback.sh <CUTOVER_SNAPSHOT_PATH>'
```

The helper restores `config.yaml` and `dashboard.env` and restarts the projects;
it does not import the snapshot database. Preserve current SQLite data, encryption
keys, peers, policies and addresses. Re-enrollment and key regeneration are not
ordinary rollback steps. A missing/corrupt snapshot requires an incident decision.

For a lost/corrupt host, keep traffic closed and preserve HOU4. Retrieve and
verify the complete **production** recovery set and independent controller
handoff, including source bundle, image lock, read-only GHCR access, certificate
state and runtime-secret recovery. Follow [production replacement recovery](production-host-recovery.md)
using `restore-production-host.yml` and `publish-production-recovery.yml`, with
this tested ordering:

1. Pin the new SSH identity and establish bounded console/bootstrap access.
2. Recover the saved configuration, database/media pair, keys and exact image
   digest into clean storage; PostgreSQL imports into an empty schema with
   `ON_ERROR_STOP`. No application build, CMS seed or identity re-bootstrap.
3. With writers stopped, revoke backed-up staff and IdP sessions/codes/tokens.
   Reconcile every subsequent offboarding, allowlist, group, device and PAT change
   against the current operator record before opening traffic. Session cleanup
   alone cannot revoke every previously issued external JWT.
4. Review authorization before publication. Replace only the pinned lost HOU1
   peer when needed, retaining group/policy IDs. Start private DNS **before**
   refreshing the dedicated controller transport; prove personal private SSH
   before removing bootstrap access. Preserve HOU4 and verify its reconnection.
5. Update HOU4's source host-key pin through its backup role; reconcile exact
   metrics/backup policies, collect a fresh point, verify identity/JWKS continuity,
   Google/VPN access, revocation, application data/media, alerts and reboot.
6. Remove plaintext staging and temporary grants after owner SSH and recovery
   access are confirmed. Retain protected first-error evidence and encrypted
   owner-only handoff. Record provider/operator waits separately from restore time.

The [clean test-host procedure](clean-host-recovery.md) supplies the rehearsal
commands and reports. Its environment checks must not be disabled for production.
The legacy Authentik-only restore drill is not the unified production restore.

During an Authentik outage, use an already authorized VPN session or provider
console and the separate local Authentik emergency administrator. The NetBird
API PAT grants API access, not dashboard login or staff roles. The installed
`/opt/hou1-netbird/break-glass.sh` requires `BREAK_GLASS_OPERATOR`,
`BREAK_GLASS_REASON`, `NETBIRD_HOST` and a securely loaded PAT in its process
environment. Inspect its prerequisite API/SSH path before invoking it: it tests
SSH before rollback, so a VPN outage requires console-led repair first. On the
console, use the explicit snapshot rollback above when that is the needed repair.
Keep emergency credentials offline, audit actor/reason/start/end/snapshot/result,
and rotate used emergency credentials/PATs after recovery.

Use the authenticated audited [offboarding command](operator-offboarding.md)
for coordinated staff/IdP/VPN denial, then verify denial and durable audit.
Disabling a user only in the GUI does not complete that workflow. Reapply later
offboarding after any restore before accepting traffic.

## Ownership and closeout

The infrastructure owner owns Ansible, restore, NetBird and host access; the
application owner validates staff identity/roles and content; the monitoring owner
owns controller renewal and Discord; the release operator records evidence and
the incident commander can stop the window. Escalate an unacknowledged page at
5 minutes to infrastructure, 10 minutes to incident command and 15 minutes to
the service owner. Name the actual people/contact paths in the change record.

Close after 30 minutes of healthy production probes, correct access boundaries,
verified fresh off-host recovery evidence, recorded rollback/emergency access,
resolved migration failures and retired temporary grants. Link the exact refs,
backup ID, reviewed reports and approval to infrastructure #28 and staff #38.
Passing the isolated rehearsal alone does not clear the production gates above.
