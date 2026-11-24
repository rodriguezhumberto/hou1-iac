# Reproducible isolated recovery drill

CI-backed recovery points use their saved image digest and temporary registry
access; see [Application images](../application-images.md). Cached build dependencies
and application builds described below apply only to legacy source-built points.

`ansible/playbooks/rehearse-recovery.yml` runs the `recovery_drill` role's
preparation, restore, identity, application and cleanup phases.

This restores the combined HOU4 recovery archive into seven private containers
on an isolated test HOU1. It requires a running test stack, its matching source
checkout, cached base images, Git, OpenSSL and Docker Compose. It does not
replace the primary services, change their databases, expose ports, enroll VPN
peers or change DNS. Clean-OS recovery and production traffic cutover remain
separate work. Do not use this test playbook as a production restore command.
Use the [clean-host procedure](clean-host-recovery.md) for the separate fresh-OS
gate; it restores the original projects and host management instead of another
isolated Compose project.

The drill-owned proxy uses Nginx `1.30.7-alpine` pinned to
`sha256:caf4e1e032a20f9c3c5e28fc7f25ffd768ad8677b4975c54193075997b3a97e3`,
matching the application security update. Cache that exact image before the
drill. CI's Nginx parser and disposable Compose fixtures use the same pin.
Application deployment continues to take its ingress image from the selected
application release; merge and release that repository's security update too.

## Controller inputs

Use the exact infrastructure commit under review and the staff commit recorded
in the selected backup. Use [contributor setup](../../CONTRIBUTING.md) and install `age`. Pin the new test host's SSH identity in its private inventory. Reserve at least 4 GiB available RAM and 10 GiB free disk
before the drill; allow additional room for application builds and backup growth.

For an explicitly requested attempt without resizing the VM, set private input
`recovery_drill_resource_profile: constrained`. The default `standard` profile
retains its 4 GiB available-RAM requirement. Constrained admission requires
2.5 GiB available RAM, the same 10 GiB free disk, cgroup-v2 RAM/swap enforcement
and a saved CI image; source builds are refused. Preparation owns the generated
per-service limits: 2 GiB total RAM and 512 MiB additional swap across all seven
containers. The driver monitors host headroom/pressure, enforced container limits
and OOM flags; failure stops only the drill project before normal cleanup.
`resource-report.json` records observations. No existing service limits or VM
allocation change. A constrained OOM is a failed capacity experiment, not proof
that the recovery point is corrupt; preserve diagnostics and use a fresh attempt
after adding capacity.

Keep these files in a mode-0700 operator directory:

- The selected `recovery.age`, `manifest.json` and transport `receipt.json`.
- An identity that can decrypt that point, retained only on the controller.
  An explicitly authorized temporary recovery recipient can be used instead of
  the offline identity. Keep the original receipt separate if the operator
  re-encrypts the archive: derivative ciphertext needs its own size/SHA256
  manifest and must retain a reference to the original point.
- A public reference JWKS JSON captured from the running **test** staff issuer.
- Private Ansible input YAML, including the two existing internal test fixture
  passwords. Decrypt the matching SOPS bundle locally with `sops --decrypt` into
  this private directory; never commit the resulting file. Google user passwords
  are not needed, and this drill makes no external Google requests.

Example input structure (replace all placeholders; no credentials belong here):

```yaml
recovery_drill_id: rehearsal-20261109-01  # new ID on every attempt
recovery_drill_test_suffix: vps-test.example.com
recovery_drill_staff_ref: 0123456789abcdef0123456789abcdef01234567
recovery_drill_app_source: /opt/pearlandpaws-test
recovery_drill_archive: /private/recovery/point/recovery.age
recovery_drill_manifest: /private/recovery/point/manifest.json
recovery_drill_age_identity: /private/recovery/offline.agekey
recovery_drill_reference_jwks: /private/recovery/test-jwks.json
recovery_drill_evidence_directory: /private/recovery/evidence
recovery_drill_probe_secrets:
  TEST_ADMIN_PASSWORD: <from matching SOPS test bundle>
  TEST_READER_PASSWORD: <from matching SOPS test bundle>
```

The default fixture usernames are `vps-staff-admin` and `vps-staff-reader`;
override `recovery_drill_admin_username` / `recovery_drill_reader_username` only
with existing internal test identities whose reader/admin roles are already in
the backup. Maintained `lab-staff-*` fixture names are also supported; recovered
password hashes must match the supplied protected probe inputs.
The original CMS snapshot must have empty pet content, as in the
rehearsal fixture. The test adds content/media to the restored copy, checks
persistence, rolls back to that empty snapshot and checks fresh sign-in.

If container names differ, declare `recovery_drill_primary_containers` in this
order: Authentik server, Authentik worker, PostgreSQL, NetBird server, dashboard,
staff app, Nginx. Names must identify distinct test containers. The role records
their actual image IDs and protects their container IDs/start times.

## Run and collect evidence

```bash
ansible-playbook -i /private/recovery/test-inventory.yml \
  ansible/playbooks/rehearse-recovery.yml \
  --limit YOUR_TEST_HOST \
  -e @/private/recovery/inputs.yml
```

The target must belong to `isolated_test_vps`, have `cms_environment: test`,
and not belong to `prod_env`. `--check` is refused because it cannot prove a
restore. The role refuses existing remote roots and controller evidence IDs.
Use a single target per run and a fresh `recovery_drill_id` after a failure;
never delete a failure marker and continue the same attempt.

The recovery sequence is:

1. Authenticate age decryption, verify the environment and SHA256, safely
   extract the archive, and validate SQLite on the controller. The offline
   decryption identity never goes into inventory or onto either VPS; recovered
   host runtime keys remain inside the private drill payload.
2. Prepare an internal Docker network and private binds. Resolve saved
   certificate lineages by SAN coverage, matching key and validity. Preserve
   the saved app environment and HTTP healthcheck, including `HOST=0.0.0.0`.
3. Pull and verify the saved CI application digest, or build the saved revision
   with public build arguments for a legacy point. Other services use image IDs
   from the existing test stack. Record the source revision and image ID; retain
   the saved NetBird version.
4. Start empty PostgreSQL. Require a successful query against the actual
   database over TCP, rather than accepting the temporary initialization socket.
   Refuse a nonempty schema and import with `ON_ERROR_STOP`.
   NetBird SQLite integrity/count checks first allow SQLite to recover any hot
   rollback journal in the private restored payload. They refuse live/outside
   paths and do not discard journals or open databases with immutable mode.
5. With restored writers stopped, clear backed-up IdP sessions/codes/token
   records and revoke staff sessions. Start only the restored services and wait
   for real PostgreSQL, Authentik and app health checks.
6. Verify recovered signing keys, emergency administrator, Google source/client
   secret, OIDC client secrets and test identities without re-bootstrap. Test
   real fixture OIDC login, role denials, logout/session invalidation, JWKS,
   NetBird API/dashboard, media/private previews, restart and snapshot rollback.
7. Stop only the drill Compose project and verify the unchanged primary
   baseline. Fetch reports before removing remote plaintext. Remove controller
   plaintext staging even if remote teardown fails. A teardown failure leaves
   protected remote diagnostics for investigation; do not erase them blindly.

Reports are saved below `recovery_drill_evidence_directory/recovery_drill_id`.
The directory is private. Share only reviewed/redacted `recovery-report.json`,
`identity-report.json`, `cleanup-report.json` and phase result JSON as issue
evidence. `*-private.log` and `private-*.log` are protected diagnostics and must
not be published. The role does not delete your original encrypted backup,
offline identity, input YAML or public JWKS; remove temporary decrypted input
YAML and retire temporary grants after the operator handoff.

Reconcile later offboarding/access changes before any real restored system
accepts traffic. Clearing sessions does not replay those changes or revoke
every JWT held outside the restored system.

## Recorded results

The [execution notes](../evidence/component-recovery-drill-2026-11-10.md) retain
the baseline, failed attempt and corrected run. Later host-replacement results
are linked from the [production runbook](../production-oidc-runbook.md).
