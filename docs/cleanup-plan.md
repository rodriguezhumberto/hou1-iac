# Deployment code cleanup

Make HOU1 and HOU4 easier to navigate, change and validate before changing their
deployment architecture. Work stays local on cleanup branches.

## Execution order

1. Record the existing test results and controller dependencies for each repo.
2. Add consistent local validation commands and concise contributor guidance.
3. Map supported entry points, execution locations and configuration ownership.
4. Extract task phases and templates without changing runtime behavior. Update
   tests that depend on the old file layout while preserving their assertions.
5. Fix the confirmed Authentik ingress-network removal bug with a transition test.
6. Review effective configuration, run the relevant suites and record follow-up
   reliability work.

Independent documentation and tooling changes may run in parallel. Integrate
their local commits before starting work that depends on the new interfaces.
Review every delegated result; use isolated worktrees for implementation.

## Cleanup rules

- Keep public playbook names, variable names and installed paths compatible.
- Preserve task ordering, environment guards, secret handling and recovery data.
- Use named task files for phases; create a role only for a separate owner or
  lifecycle.
- Keep generated runtime files separate from authoritative configuration inputs
  in the documentation. Changing generation behavior requires a focused fix.
- Separate controller programs from files installed on hosts. Extract shared
  Python code only when its execution and delivery contracts are clear.
- Document required inputs and owned resources for roles being changed.
- Keep documentation short. Record consequential design choices with their reason.
- Do not push, open PRs, deploy, decrypt production inputs or change live nodes.

## Validation

Run existing offline suites before refactoring. Distinguish failures, missing-tool
skips and optional integration checks. Use rendered configuration and real Ansible
task fixtures where possible. Preserve tests of SSH cutover and recovery ordering.
Add transition coverage for configuration removals; routine reconciliation should
converge without unnecessary changes.

## Decisions

- Use task files within existing roles first: this improves navigation without
  introducing additional role interfaces.
- Keep scoped encrypted bundles separate: their different access and rotation
  requirements are intentional.
- Preserve historical evidence content and current runtime paths: recovery
  commands and retained procedures may depend on them.
- Treat the network-removal correction as an explicit behavior change, separate
  from file extraction.
- Use matching Make targets and isolated dependencies in each repository: local
  checks and CI should exercise the same commands without a shared tool package.
- Keep current guides in `docs/`, probe and drill procedures in `docs/probes/`,
  dated records in `docs/evidence/` and superseded guidance in `docs/archive/`.
  Public script paths and recorded results stay stable when documentation moves.

## Later reliability work

- Render Compose configuration from declared inputs rather than previous outputs.
- Separate source checkouts from generated deployment configuration.
- Limit reloads and container recreation to affected services and inputs.
- Separate shared ingress from application deployment ownership.
- Separate OS upgrades from routine application reconciliation.
- Reconcile VPN peer replacement and readiness across both hosts.
- Consider shared collections or Python packaging after interfaces are stable.

## Review record

- HOU1 and HOU4 contributor guides identify configuration sources, generated
  outputs and local checks. Historical documentation is marked as historical.
- Both repositories provide `make setup`, `check`, `syntax`, `lint` and `test`.
  Setup installs pinned dependencies; checks do not install them or deploy.
- HOU4 monitoring runtime uses named static phases. Comparing flattened tasks
  against the original found identical definitions and order for all 18 tasks.
- HOU1 removes the managed Authentik ingress network when disabled. Regression
  coverage exercises enabled, disabled, repeated disabled and re-enabled states
  in both CMS/NetBird reconciliation orders, preserving unrelated configuration.
- HOU4 final `make check` passed all syntax/lint checks and 60 tests, with 15
  optional native-tool skips matching its baseline. Isolated setup and dependency
  consistency checks passed.
- Independent review found no material issues in the integrated changes above.
  A separate review of the HOU1 role extraction also found no material issues.
- HOU1 webstack and Authentik now use named static phases. Comparison
  confirmed all 67 original task definitions and their order, except the intended
  copy-to-template conversion. The application template source is unchanged; four
  fixtures cover rendered bytes, private mode and secret-log suppression.
- The rehearsal TLS fixture uses the phase reader without changing its assertions.
  Its 13-test module and static checks passed after the correction.
- HOU1 final `make check` passed all 25 playbook syntax checks, source syntax,
  lint and 333 tests, with one optional `dnsmasq` integration skip. Nginx, SOPS/age
  and branding fixtures ran; the separate browser check also passed.
- Both repositories remain on local `cleanup/readable-deployments` branches.
  No pushes, PRs, deployments, live-node access or production decryption occurred.
