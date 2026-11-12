# Deploying CI-built application images

Publish manually from tested application `main`, then deploy explicitly through
Ansible. Publication does not deploy. Supply the workflow's `image-release.json`
receipt and matching private inputs:

```yaml
application_image_reference: ghcr.io/OWNER/APP@sha256:<64-hex-digest>
application_image_revision: <40-hex-source-commit>
deploy_ref: <same-40-hex-source-commit>
application_image_registry_secret_source: /private/ghcr-pull.enc.json
application_image_registry_age_key_file: /private/scoped-deployment.agekey
```

For isolated rehearsals, `rehearsal_staff_ref` must also equal that revision.
Keep source, receipt and runtime configuration compatible. Ansible checks image
architecture, source repository/revision, unprivileged runtime user and offboarding
schema before deployment. It retains the matching source for Compose/Nginx and
optional static content, replaces the build definition, then runs Compose with
`--no-build`. `application-image.json` beside Compose records the result.

## Registry credentials

Use dedicated `read:packages` access to the private package, with `GHCR_USERNAME`
and `GHCR_TOKEN` in the controller's SOPS JSON bundle. Keep owner recovery access;
never reuse the CI publishing token or put credentials in inventory, build args
or release receipts. Controller-side decryption feeds a root-only temporary Docker
auth directory on the target. Ordinary failures clean it up. After a killed
controller/process, inspect leftover `hou1-ghcr-*` directories under target `/tmp`
and revoke temporary credentials as appropriate.

The isolated test registry helper pins `ghcr.io` and
`pkg-containers.githubusercontent.com`, opening host-only HTTPS for at most
30 minutes. Production denies still win; workloads gain no egress. An interruption
may leave `/var/lib/hou1-test-registry-access.json` and marked hosts-file entries.
Use the installed helper and receipt token to finish cleanup; firewall entries
expire even if that cleanup is interrupted.

## Updates and rollback

An image-only update may set `webstack_preserve_runtime_environment: true` to keep
an existing regular `.env` unchanged. It cannot initialize a host or apply new
configuration. For an isolated ingress-only run,
`webstack_bootstrap_ingress_only: true` limits the selected services; ordinary
provisioning renders declared configuration.

To roll back, select the previous validated digest and matching source revision,
then repeat deployment and acceptance. Check database compatibility first; code
rollback does not reverse data/schema changes. See [release policy](releases.md).

## Backup and recovery

Capture verifies the stopped app's actual image against its receipt and source.
The encrypted point includes that receipt; contradictions stop capture/recovery.
It excludes registry credentials and the offline owner key. The manifest digest
is portable; local Docker `image_id` values may differ across storage engines.

Recovery pulls the saved digest and checks runtime labels, never substituting a
build or another image on failure. Digest-backed image locks use `astro-app`
instead of the legacy `node` build-base entry; the other six runtime entries
remain required. Matching source is still needed for host configuration.
Retained source-built points keep their legacy restore path and must not be
relabeled as CI artifacts.

Retain every digest referenced by backups plus current/previous deployments.
Do not prune an untagged image solely because a tag moved. Cold recovery depends
on GHCR and independently recoverable pull access; an offline OCI mirror is not
implemented.

[Existing-node evidence](evidence/ci-image-deployment-2026-11-10.json) records
publication, deployment, migration rollback and 29 isolated recovery checks.
[Clean-host evidence](evidence/hou1-ci-clean-host-recovery-2026-11-10-02.json)
records a digest-backed restore and reboot. That migration rollback used a
pre-CI image; it does not prove rollback compatibility between future CI releases.
See [production recovery status](production-host-recovery.md#latest-live-result).
