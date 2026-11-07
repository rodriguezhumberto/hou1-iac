# Identity offboarding (#47)

`scripts/offboard-user.py` coordinates denial for one immutable Authentik user
UUID. It uses the existing private, pinned SSH/sudo management path, not a public
HTTP endpoint. Deploy pearland-paws #51 first: the runtime image must advertise
staff-offboarding schema 1 and include persistent subject blocking. Authentik
2025.10.4 and NetBird 0.82.0 are the supported releases.

## Management and secrets

Run as an existing authorized management operator with private SSH access,
noninteractive sudo, and the controller's SOPS/age identity. SSH configuration
must explicitly use `StrictHostKeyChecking yes`, `IdentitiesOnly yes`, and one
known-hosts file containing the configured ED25519 fingerprint. Proxy commands
may use the existing private management route. Do not open public SSH for this
command.

This reuses administrators' existing root authority. It is not a restricted sudo
delegation mechanism; do not grant arbitrary `sudo python3 -c` to additional
users. Root administrators can alter local audit files. Shipping audits to the
future HOU4 receiver is separate work.

Create an operator configuration outside Git, mode 0600, encrypted in full with
SOPS/age. It contains these fields (the names below are illustrative):

```json
{
  "environment": "test",
  "ssh": ["ssh", "-F", "/private/test-ssh-config", "test-management"],
  "ssh_fingerprint": "<confirmed SHA256 ED25519 fingerprint>",
  "staff_issuer": "https://auth-test.example.org/application/o/staff-test/",
  "staff_application": "staff-test",
  "staff_client_id": "staff-test",
  "netbird_issuer": "https://auth-test.example.org/application/o/netbird-test/",
  "netbird_application": "netbird-test",
  "netbird_client_id": "netbird-test",
  "netbird_url": "https://netbird-test.example.org",
  "netbird_subnet": "100.91.0.0/16",
  "netbird_token_file": "/private/netbird-token.enc.yaml",
  "admission_file": "/private/admission.enc.json"
}
```

The encrypted PAT document uses `netbird_management_url` and
`netbird_api_token`. It stays on the controller. Authentik's bootstrap token
stays in its container. No token is passed in shell arguments or returned in the
report. The admission document is the encrypted desired state used by the
admission reconciler (currently PR #46): `environment`, `netbird_url`,
`authentik_issuer`, `google_emails`, `staff_users` with email/role assignments,
and `fixture_users`. Other fields and age recipients are preserved. This
command does not import or merge that prerequisite PR.

The operator environment is `test` or `production`; existing Ansible/admission
configuration calls production `prod`. Test addresses use `100.91.0.0/16`;
production uses `100.90.0.0/16`. A production app must have
`STAFF_ENVIRONMENT=production` and the exact issuer; the webstack playbook
renders that value. Test fixtures alone may use internal `offboard-test-*`
users with `@example.invalid` addresses. Production targets must be external
Google-source users. Service, superuser, recovery, and NetBird owner/admin
identities are refused. Retain independent emergency management access.

## Execution and retry

Obtain the target UUID from Authentik's administrative user API; confirm the
intended person privately. An email is not an acceptable target. Use a change
reference containing no personal data. Generate one UUIDv4 operation ID and
retain it for every retry:

```bash
python3 scripts/offboard-user.py \
  --config /private/operator.enc.json --age-key-file /private/operator-age.key \
  --environment test --target <authentik-user-uuid> \
  --operation-id <operation-uuid-v4> --reason departure --reference ticket-123
```

This is a read-only plan by default. Review the reported target, authenticated
operator and environment, then repeat with `--apply`. Reasons are `departure`,
`role-removal`, `security-incident`, and `test-drill`; each performs full
offboarding. For role changes that retain access, use the existing role
administration workflow instead.

The command binds both OIDC applications to immutable Authentik subjects and
the configured client IDs, and binds NetBird's Dex subject/connector to its
external provider ID. There is no email fallback. Provider, deployment, image,
environment and protected-identity checks precede access changes.

Apply removes encrypted admission entries, blocks staff-session creation and
lookup, disables the Authentik user and clears groups, deletes owned IdP
sessions/codes/tokens, blocks the NetBird user and clears automatic groups, and
deletes only that user's peers. Deleting upstream tokens does not invalidate a
self-contained JWT everywhere; staff persistent blocking and NetBird's user
block provide the application-specific denial.

An operation manifest and audit JSONL live under
`/var/lib/hou1-operations/<environment>/offboarding/`, with root-owned 0700
directories and 0600 files. Intent is flushed before each mutation; results and
partial outcomes share the operation ID. Actor identity comes from server-side
sudo/kernel identity. The app records block/revocation/audit in one SQLite
transaction. Reports and audits omit credentials and human email allowlists.

Exit 0 on apply means all steps and denial readback passed. Any partial result
returns nonzero. Independent denial steps continue while audit writes succeed;
an audit failure stops further changes. Preserve the report, repair the failed
service or audit storage, and repeat the **same operation ID, target, reason,
reference and configuration**. A saved plan allows retry during IdP outages;
initial resolution requires Authentik. Static release/environment checks still
require Docker inspection, even if a service is stopped. If private SSH itself
is unavailable, use the existing emergency-access procedure.

The command never re-enables an identity. Rerun the existing admission
reconciler against the updated ciphertext and verify denial; do not restore an
old admission file. Reactivation requires a separately authorized process.
Requests already authorized before blocking cannot be recalled. Restore of an
older application database/IdP backup can restore access: reapply offboarding
records and reconcile the current admission source before reopening service.

## Verification

Run `python3 -m unittest discover -s tests -p test_offboarding.py -v` and the
repository's existing validation workflow. The staff repository owns its session
and callback tests. In isolated live validation, use disposable identities to
retain two cookies and an in-flight callback; prove cookie and callback denial,
fresh IdP-login denial, blocked NetBird API access and deletion of an owned
peer, unchanged control-user access, durable actor auditing, a nonzero partial
outage, same-operation retry, and admission reconciliation without restored
access. Keep any browser/token artifacts private and remove them after testing.

Signing-key overlap and production promotion are separate gates.
