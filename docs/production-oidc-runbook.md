# HOU1 production Authentik and OIDC runbook

This is the change window procedure for the Authentik, staff OIDC, and NetBird
migration. It is executed by the release operator with the platform owner
present. Values in angle brackets are recorded before the window; never paste a
password, PAT, age identity, or client secret into this document or an issue.

## Release record and approval gates

The release record must contain immutable refs and the evidence identifiers:

```text
INFRA_REF=<40 lowercase hex commit SHA>
STAFF_REF=<40 lowercase hex pearland-paws commit SHA>
NETBIRD_REF=<40 lowercase hex NetBird image/config SHA or 0.82.0 release ref>
TEST_REHEARSAL=<issue-27 rollout-rehearsal.json SHA256>
OIDC_TESTS=<issue-26/37 evidence report SHA256>
AUTHENTIK_BACKUP_ID=<off-host archive basename and .success marker>
RESTORE_DRILL_ID=<issue-22 restore-drill evidence identifier>
BREAK_GLASS_CHECK=<issue-25 evidence identifier and last verification time>
WINDOW=<UTC start/end>
```

The change is approved only when the test rehearsal and mirrored OIDC tests
are passed, the backup has a checksum and `.success` marker on the off-host
mount, the offline age recovery identity is available, and break-glass access
has been tested. The release operator confirms the staff migration owner,
infrastructure owner, and incident commander in the change record.

Before touching a host, verify the refs and clean checkouts. The infrastructure
checkout must remain at `INFRA_REF`; the staff checkout is separate:

```bash
git fetch origin "$INFRA_REF" && git checkout --detach "$INFRA_REF"
git -C ../pearland-paws fetch origin "$STAFF_REF"
git -C ../pearland-paws checkout --detach "$STAFF_REF"
git -C ../pearland-paws status --short
git rev-parse HEAD
git -C ../pearland-paws rev-parse HEAD
```

The final two commands must equal the recorded refs. A dirty checkout, mutable
branch, missing evidence hash, or production value in the test evidence aborts
the window.

## Preflight and deployment order

Run the capacity, inode, I/O, Docker, firewall, and isolation gate against the
production Authentik host. This is a read/check phase and must finish cleanly:

```bash
ansible-playbook -i ansible/inventory/inventory.yml \
  ansible/playbooks/authentik-preflight.yml \
  --limit hou1_prod -e target_hosts=hou1_prod -e deploy_environment=prod
```

Verify the selected backup before deployment. The archive, adjacent checksum,
`.success` marker, off-host copy, and offline age identity must all be present:

```bash
ssh prod_hou1 'test -f /mnt/offhost-backups/hou1-authentik/prod/<AUTHENTIK_BACKUP_ID>.success'
ssh prod_hou1 'sha256sum -c /mnt/offhost-backups/hou1-authentik/prod/<AUTHENTIK_BACKUP_ID>.sha256'
```

Deploy in this order, waiting for each health gate: Authentik/PostgreSQL,
Nginx ingress, Authentik groups/claims/clients, staff application, then the
reversible NetBird cutover. Every command is limited to `hou1_prod` except the
dedicated control-plane playbook, which has its own production target guard:

```bash
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/authentik.yml \
  --limit hou1_prod -e target_hosts=hou1_prod -e deploy_environment=prod \
  -e rollout_ref="$INFRA_REF"
ansible-playbook -i ansible/inventory/inventory.yml ansible/playbooks/authentik-bootstrap.yml \
  --limit hou1_prod -e target_hosts=hou1_prod -e deploy_environment=prod
```

Run the staff release's documented migration command at `STAFF_REF` using the
test-proven migration sequence. Confirm shared-password login is disabled only
after the first staff OIDC login and role mapping succeed. Record the number of
migrated users and failures, never passwords or tokens.

## Health and monitoring gates

The release operator records each check before proceeding. A failure blocks the
next step and opens an incident.

```bash
ssh prod_hou1 'docker compose -p hou1-authentik-prod ps --status running'
ssh prod_hou1 'docker compose -p hou1-authentik-prod exec -T postgresql pg_isready -U authentik'
curl --fail --silent https://auth.example.com/-/health/ready/
curl --fail --silent https://auth.example.com/application/o/pearland-paws-staff/.well-known/openid-configuration
curl --fail --silent https://auth.example.com/application/o/pearland-paws-staff/jwks/
curl --fail --silent https://netbird.example.com/api/instance
```

Confirm the monitoring board and alert route for all of the following:

| Component | Healthy signal | Page/abort condition |
| --- | --- | --- |
| Authentik | readiness 200, login and discovery succeed | two failed probes or any 5xx for 2 minutes |
| PostgreSQL | `pg_isready`, Compose health is healthy | unhealthy state or connection errors for 1 minute |
| Nginx | TLS certificate valid, OIDC routes and WebSocket upgrade pass | TLS/route failure or 5xx rate over 1% for 2 minutes |
| NetBird | `/api/instance`, dashboard, gRPC/WebSocket, and peer check pass | API/peer outage for 2 minutes or key mismatch |
| Staff login | code+PKCE callback, claims, group role, logout/revocation pass | any invalid-token acceptance or 3 consecutive login failures |

Capture alert rule IDs, probe timestamps, and dashboard links in the release
record. Do not attach bearer tokens, cookies, PATs, or private keys.

## Cutover, abort, and rollback

After all health checks pass, run the guarded cutover. It snapshots NetBird
state and keys before swapping the OIDC issuer and performs an immediate health
check:

```bash
ansible-playbook -i ansible/inventory/inventory.yml \
  ansible/playbooks/netbird-authentik-cutover.yml
```

Abort before cutover for any failed preflight, missing backup marker, failed
test evidence, or unresolved isolation finding. Abort during cutover for any
health condition in the table above, a staff role mismatch, a JWKS/signature
failure, or more than 1% failed logins in two minutes. The incident commander
may abort immediately for data loss, exposed production credentials, or an
unverified restore point.

Rollback is automatic when the cutover health gate fails. For a later incident,
run the saved snapshot rollback from the NetBird host and verify the embedded
issuer and peers:

```bash
ssh prod_hou1 '/opt/hou1-netbird/authentik-rollback.sh <CUTOVER_SNAPSHOT>'
curl --fail --silent https://netbird.example.com/api/instance
```

Do not re-enroll peers or generate new NetBird keys. Preserve the SQLite state,
policies, addresses, and snapshot for the incident record.

## Restore and break-glass

If Authentik data is damaged or PostgreSQL cannot recover, stop the affected
project and restore the verified archive on a clean host. The restore verifies
the checksum and starts a health-gated Compose project:

```bash
ssh prod_hou1 '/opt/hou1-authentik/prod/restore-drill.sh \
  /mnt/offhost-backups/hou1-authentik/prod/<AUTHENTIK_BACKUP_ID>.tar.gz.age \
  https://auth.example.com/application/o/pearland-paws-staff/'
```

Record restore duration, login, discovery, JWKS, and signing-key continuity.
If Authentik is unavailable, an approved operator uses NetBird SSH or the VPS
host console and runs the break-glass procedure with the API PAT from the
encrypted controller vault. API PAT access is for API recovery and does not
bypass dashboard/group authorization:

```bash
ssh prod_hou1 '/opt/hou1-netbird/break-glass.sh'
```

Record only operator, reason, UTC timestamps, pass/fail, and the snapshot used.
Rotate the PAT and close the emergency access record after service recovery.

## Ownership and closeout

The infrastructure owner executes Ansible, backup, restore, and NetBird steps;
the staff owner validates PKCE, claims, roles, logout, and migration; the
release operator owns the change record and evidence; the incident commander
can stop or roll back the window. Escalate an unacknowledged page after 5
minutes to the infrastructure owner, after 10 minutes to the incident
commander, and after 15 minutes to the service owner/on-call manager.

Close the window only after the production health board is green for 30 minutes,
all migration failures are resolved or assigned an owner and due date, the
backup and restore identifiers are attached, and rollback/break-glass evidence
is recorded. Link this runbook, the test rehearsal report, and the OIDC test
report to issues #28 and #38.
