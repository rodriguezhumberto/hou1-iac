# Releases and rollback

Feature branches and PRs target `cms-rollout` in both repositories. Deploy that
combined build to test. Keep `main` as the production fallback until the whole
release passes; then open release PRs from `cms-rollout` into `main`.

Version tags are independent in each repository. `v0.1.0` is the
owner-confirmed production baseline in both repositories; `v0.2.0` is the
planned coordinated CMS release. Use patch versions for fixes and minor
versions for features while pre-1.0. Never move, delete, or reuse a release
tag. An app-only change does not require an infrastructure tag, and an
infrastructure-only change does not require an app tag.

## Test the integration build

From a clean `hou1-iac` checkout:

```bash
git fetch origin --tags
git switch cms-rollout
git pull --ff-only origin cms-rollout
cd ansible
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_test deploy_ref=cms-rollout" --ask-vault-pass
```

`deploy_ref` selects the **application** revision. The local Git checkout selects
the **infrastructure** revision. Legacy `deploy_branch` remains supported;
`deploy_ref` takes precedence. Without either, the app revision defaults to `main`.

## Release

Verify the exact app/infrastructure commit pair on test, including backup/restore
and migration rollback. Merge release PRs without changing the tested content.
Create an annotated tag only in repositories that changed:

```bash
git tag -a APP_TAG VERIFIED_APP_SHA -m "Application release APP_TAG"
git push origin refs/tags/APP_TAG
# When infrastructure changed, repeat in hou1-iac with INFRA_TAG and VERIFIED_INFRA_SHA.
```

Record both deployed refs and commit SHAs, the backup reference, and deployment
result in GitHub release notes. From a clean infrastructure checkout, fetch the
infrastructure ref (for example `v0.2.0`), then run the production playbook with
the tested application ref (for example `deploy_ref=v0.2.1`). Verify production
before marking that exact pair as the new rollback baseline. Branch
protection/deployment approval is separate; these commands do not deploy
automatically.

## Roll back to the initial baseline

From a clean infrastructure checkout:

```bash
git fetch origin --tags
git switch --detach v0.1.0
cd ansible
ansible-playbook playbooks/hou1.yml -e "target_hosts=hou1_prod deploy_branch=v0.1.0" --ask-vault-pass
```

The baseline playbook predates `deploy_ref`, so use `deploy_branch` here. Restore
compatible database/media backups when required; a Git rollback does not undo
migrations or remove newer containers automatically. Tags pin source, not Docker
image contents or data. Pin images and retain backups for reproducible rollback.
