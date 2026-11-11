# Deploying CI-built application images

Application images are released manually from the staff repository's `main`
branch after its application and image checks pass. Publication does not deploy
to HOU1. See staff issue #57 and infrastructure issue #60. The infrastructure
change depends on the recovery work in PR #59; merge prerequisites first and
rebase before final checks.

Use the workflow's `image-release.json` artifact to supply both values:

```yaml
application_image_reference: ghcr.io/rodriguezhumberto/pearland-paws/app@sha256:<64-hex-digest>
application_image_revision: <40-hex-source-commit>
deploy_ref: <same-40-hex-source-commit>
application_image_registry_secret_source: /private/ghcr-pull.enc.json
application_image_registry_age_key_file: /private/scoped-deployment.agekey
```

For the isolated rehearsal, `rehearsal_staff_ref` must also equal that source
revision. Keep all existing test hostnames, clients, runtime settings, certificates
and SOPS bundles. A release must contain the runtime-configuration changes from
staff issue #57 before deploying it with this role.

The private GHCR package requires a dedicated credential with `read:packages`
and access to this package. Do not reuse the CI publishing token or a broad
repository-management token. Supply `GHCR_USERNAME` and `GHCR_TOKEN` in the
controller's SOPS JSON bundle, encrypted to the operator and scoped deployment
recipients. No credential value belongs in inventory, Git, image build args or
the CI release receipt. Decryption occurs on the controller; Docker authentication
uses a root-only temporary directory on the target. Ansible removes it after the
operation, including ordinary failure paths. A hard controller/process interruption
may require removing its leftover `hou1-ghcr-*` directory from target `/tmp` and
revoking the temporary pull credential.

On an already isolated test node, the registry operation resolves and pins only
`ghcr.io` and `pkg-containers.githubusercontent.com`. It grants the host HTTPS
access to those addresses for at most 30 minutes and removes its DNS pins and
temporary destinations after success or failure. Explicit production denies
still win; workload container egress is not granted. This avoids relying on stale
CDN addresses in the test allowlist. A controller interruption can leave the
root-only `/var/lib/hou1-test-registry-access.json` lease receipt and its marked
`/etc/hosts` block: finish cleanup with the installed helper and receipt token
before retrying. Firewall destination entries expire even if cleanup is interrupted.

Ansible pulls the explicit digest and checks its architecture, source repository,
source revision, unprivileged runtime user and offboarding schema before changing
the application deployment. It keeps the matching source checkout for Compose,
Nginx and landing-page files, replaces the application's build definition with
the verified image, and runs Compose with `--no-build` and existing health checks.
Storage and ingress overrides continue to apply. The successful deployment writes
`application-image.json` alongside Compose.

The manifest digest is the portable artifact identity. The receipt's `image_id`
records the local Docker engine's ID, which can differ between classic and
containerd image stores for the same manifest. Recovery verifies the exact saved
digest and runtime labels, then records the destination engine's ID for local
container and future backup checks.

For an existing node's image-only update, set
`webstack_preserve_runtime_environment: true` to retain its `.env` byte for byte.
This requires an existing regular file; it does not initialize a fresh host.
Select only the existing app/proxy services for the isolated rehearsal
(`webstack_bootstrap_ingress_only: true`), so unrelated optional services are not
started as a side effect. Full provisioning continues to render the declared
runtime settings by default.

To roll back application code, select the previous validated release's digest
and revision together, then run the existing Ansible deployment entry point with
that release's variables. Do not move a tag or rebuild. Database migrations must
remain compatible with the previous release; a code rollback alone does not undo
schema/data changes. Run the issue-specific login, authorization, public-access
and health checks after either direction of deployment.

Backup capture checks the container's actual image against the receipt and saved
source revision while its writer is stopped. The encrypted recovery point includes
the receipt in `recovery.json` and the saved application configuration. Missing or
contradictory digest metadata stops capture/recovery. Neither the registry token
nor the offline operator age identity is added to the backup.

Component and replacement recovery pull and verify the receipt's exact image.
They never substitute a build or a different image when a pull fails. For a new
digest-backed replacement point, the explicit image lock has `astro-app` equal to
the receipt's image instead of a `node` build-base entry; its other six runtime
entries remain required. Keep the matching source bundle for host configuration,
but no npm cache, compiler, Node base image or application rebuild is needed.
Older source-built recovery points retain their existing legacy build path and
are not upgraded or relabeled as CI artifacts.

Keep every release digest referenced by retained backups, plus the current and
previous deployments, in GHCR. Registry availability and independent access to
the encrypted read credential are cold-host recovery prerequisites. Do not delete
untagged images just because a release was rerun or its SHA tag moved. A registry
outage is a known recovery dependency; an offline OCI image mirror is separate work.

Before closing #60: publish from merged `main`, deploy the digest to the existing
test host, exercise rollback, collect a new encrypted recovery point, and run the
component restore against its recorded artifact. Then prepare the clean-OS replay.
Local fixtures do not replace these live gates.

The existing-node rehearsal passed on 2026-11-10: private publication and pull,
digest deployment, rollback to the retained pre-CI image, redeployment and an
isolated digest-backed restore. All 29 recovery checks passed; the primary stack
then passed 23 health checks and seven staff login/role checks, with eight
monitoring targets up and no active alerts. Runtime bytes and primary storage
were preserved. The temporary deployment age identity was removed. See the
[recorded evidence](evidence/ci-image-deployment-2026-11-10.json).

This first migration did not test rollback between two published CI digests.
PR #61 stays dependent on PR #59; merge the prerequisite first and rerun checks
after rebasing.

The next [clean-host preflight](evidence/ci-image-clean-recovery-preflight-2026-11-10.json)
verified a fresh encrypted point, remote image manifests, saved access policy and
controller inputs. It also fixed preparation of backups taken after a prior
restore: NetBird's saved image may use the exact recovery-lock digest. The
subsequent [clean-host run](evidence/hou1-ci-clean-host-recovery-2026-11-10-02.json)
passed: 187 PostgreSQL tables restored, 13 selected authorization tables matched,
29 isolated recovery checks passed, and reboot restored private management and
all 23 host checks. HOU4 reconnected without re-enrollment and collected a fresh
owner-encrypted point. This completes the test recovery gate, not production
promotion.
