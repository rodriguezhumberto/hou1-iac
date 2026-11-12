# Releases and rollback

PRs target `main`. Application image publication is a manual CI action from tested
`main`; deployment is a separate explicit Ansible action. The former `cms-rollout`
integration-branch process is not the current release workflow.

## Release record

Pin the infrastructure commit, application commit and CI image digest together.
`deploy_ref` and `application_image_revision` must equal the image receipt's source
revision; a branch or tag alone is insufficient. Use the variable contract in
[application images](application-images.md) and the acceptance checklist in the
[production runbook](production-oidc-runbook.md).

Tags are optional human-readable labels, independent in each repository. Never
move or reuse them. Record the tested refs/digest, previous compatible release,
backup ID, validation and approval before deployment. A merged PR or published
image does not change a running node.

## Deployment and rollback

From the pinned infrastructure checkout, run the established-host deployment with
the reviewed private release inputs. Verify health, login/roles, public denial and
monitoring before accepting it. Keep the current and previous image and all images
referenced by retained backups available in the registry.

For code rollback, select the previous digest **and its matching revision**, then
repeat deployment and acceptance. Do not rebuild or rely on a mutable image tag.
Check schema/data compatibility first: source rollback does not undo migrations,
CMS writes, uploads, sessions or identity changes.

For lost storage, use [production replacement recovery](production-host-recovery.md)
with a complete encrypted point. For website traffic rollback, reconcile writes
made after cutover before reopening the old site. Historical release tags are
records, not a standing instruction to deploy an obsolete baseline.
