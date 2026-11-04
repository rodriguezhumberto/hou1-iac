# Releases and rollback

Feature branches and PRs target `cms-rollout` in both repositories. Deploy that
combined build to test. Keep `main` as the production fallback until the whole
release passes; then open release PRs from `cms-rollout` into `main`.

Use matching annotated release tags in both repositories. `v0.1.0` is the
owner-confirmed production baseline; `v0.2.0` is the planned CMS release. Use
patch versions for fixes and minor versions for features while pre-1.0.
Never move, delete, or reuse a release tag.

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
In each repository, create an annotated tag at its verified commit:

```bash
git tag -a v0.2.0 VERIFIED_COMMIT_SHA -m "CMS release v0.2.0"
git push origin refs/tags/v0.2.0
```

Record both commit SHAs, backup reference, and deployment result in GitHub release
notes. From a clean infrastructure checkout, fetch tags, switch to `v0.2.0`, and
run the production playbook with `deploy_ref=v0.2.0`. Verify production before
marking that release as the new rollback baseline. Branch protection/deployment
approval is separate; these commands do not deploy automatically.

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
