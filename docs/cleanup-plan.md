# Deployment code cleanup

Make HOU1 and HOU4 easier to navigate, change and validate before changing their
deployment architecture. HOU1 can be pushed for PR review; HOU4 stays local.

## Initial cleanup order

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
Review every delegated result. Use isolated worktrees for overlapping changes;
independent owners can work in the same checkout.

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
- Keep HOU4 local. HOU1 may be pushed for PR review; do not deploy, decrypt
  production inputs or change live nodes.

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
- Keep shared paths/topology in inventory and component knobs in role defaults:
  standalone recovery and backup entry points need the shared values too.
- Treat generated Compose fingerprints as ownership checks, never desired input.
  Explicit extensions preserve custom settings without carrying stale output forward.
- Retain `app_directory` as the runtime root and Compose identity. A separate Git
  checkout avoids modifying tracked configuration; regular installed assets keep
  backups compatible. Cold restore retains its old layout until reconciliation.
- Advance applied-input state only after successful activation. This retries
  interrupted changes even when Ansible has already written the new files.
- Require explicit companion transports and recovery resume. Pin the original
  handoff and replacement rather than guessing identities from a desired address.

## Reliability implementation

Implemented locally in the order below.

1. Move component settings to their owning role defaults; keep shared topology,
   paths and identity inputs in inventory. Verify actual Ansible precedence.
2. Move OS upgrades into explicit maintenance playbooks. Routine reconciliation
   still refreshes package metadata and installs required packages.
3. Give the application Compose override one owner. Generate desired state from
   declared inputs and refuse unexplained additions during migration.
4. Prepare shared ingress resources before application or backend startup.
   Authentik requires an explicit environment-matched network declaration.
5. Keep the application runtime directory and project identity stable; use a
   separate source checkout and regular installed assets. Preserve older backup
   configuration and recovery archives through source-discovery fallbacks.
6. Track successfully applied service inputs. Recreate affected consumers, repair
   stopped/missing services and retry failed activation on subsequent runs.
7. Add an explicitly scoped companion/renewal handoff to peer replacement;
   retain lost-peer identity checks and independent recovery access.
8. Review integrated changes and run both complete local check suites.

Shared packaging remains deferred: controller and host programs have different
delivery contracts, and these changes do not yet establish a useful shared API.

## Initial cleanup review

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
- At the initial cleanup checkpoint, both repositories were on local
  `cleanup/readable-deployments` branches. No pushes, PRs, deployments, live-node
  access or production decryption had occurred.

## Reliability review

- Effective defaults stayed unchanged across 100 HOU1 and 77 HOU4 shared inputs.
  Real Ansible fixtures verify role visibility and inventory/extra-var precedence.
- Review corrected unchanged HOU4 deployments skipping repair, legacy standalone
  backup configuration assuming migration, and interrupted asset installs blocking
  retry. Full and component-tag runs now repair containers; security-only tags do
  not apply monitoring.
- Disposable Docker tests verify stable container IDs on no-op, consumer-only
  recreation after atomic file replacement and repair of stopped/deleted services.
- Recovery fixtures cover deletion, enrollment, saved receipts and partial group
  assignment, plus repeated resumes and refusal of changed identities or grants.
- Independent review found no remaining material issues. Runtime paths, archive
  schemas and historical evidence remain compatible.
- HOU1's full pre-PR `make check` passed syntax, lint and 377 tests, with one
  optional DNS skip. The branded-login browser check also passed.
- HOU4's full check passed 78 tests with 15 optional native-tool skips. Component
  tag repair coverage and final syntax/lint checks passed in both repositories.
- Documentation links resolve and all 62 historical JSON evidence files are
  unchanged. No deployments, live-node access or production decryption.

## Environment targeting review

- Test group names and defaults remain supported, but their default host lists
  are empty because the former test deployment is production.
- Managed-host playbooks validate inventory environment, identity inputs and
  connection address before fact gathering or controller changes. Shared guards
  run under component tags; existing recovery gates remain in place.
- HOU1 now checks SSH host keys, matching HOU4. New and replacement keys need
  independent verification before they enter the selected known-hosts file.
- Direct-SSH rehearsal tools now share explicit address and ED25519 fingerprint
  checks before remote commands or decryption. Monitor drills validate both the
  server and auxiliary probe host; installed programs include the same helper.
  Admission binds SSH to its independently selected inventory profile before
  decrypting the NetBird token. See the [target contract](probes/rehearsal-targets.md).
- Private inventories and encrypted endpoint values were not inspected. Fixed
  fixture domains, containers and paths need review for the new lab.

Validation: the Ansible audit's full checks passed 382 HOU1 tests without skips
and 82 HOU4 tests with 14 optional native-tool skips. The subsequent SSH changes
passed 58 affected tests without skips, plus syntax and lint. Coverage includes
retargeted aliases, host-key changes, tagged bootstrap rejection and HOU4 renewal
suspension. Documentation links pass and all 62 historical JSON evidence files
are unchanged. No live-node access or production decryption occurred.

## Next: test-host acceptance

Separate HOU1/HOU4 test VMs are provisioned. The authorized lab deployment and
current acceptance results are recorded in the
[2026-11-17 lab evidence](evidence/isolated-lab-2026-11-17.md).
Keep the following acceptance sequence for subsequent deployment changes.
Record the HOU1/HOU4 infrastructure refs, selected application image/revision and
a verified recovery point using the [release record](production-oidc-runbook.md#release-record-and-access).

1. Run each host's normal playbook with its test inventory and declared inputs.
   Record container IDs, source/runtime paths, private access and probe results.
2. Repeat unchanged inputs. Container IDs must stay stable and the application
   source checkout must remain clean.
3. Add a temporary Nginx label through `app_compose_extensions`, reconcile, then
   remove it. Only the Nginx consumer should be recreated for each change.
4. Stop a test application container and reconcile unchanged inputs. The service
   must recover without recreating unrelated containers.
5. Change one service-specific HOU4 monitoring input, restore it, then repeat
   unchanged inputs. Verify its consumers update, the rest stay stable and probes
   recover.
6. Capture and verify a fresh encrypted backup using the separate source layout.

Clean-host recovery and peer replacement remain a later run with independent
console access and the explicit
[recovery checkpoints](probes/clean-host-recovery.md#before-wiping-hou1).

## Lab recovery findings

- HOU1's VPN-bound Uptime Kuma can remain stopped after boot because Docker
  starts it before the VPN address exists. A retryable boot unit now checks address
  readiness before application activation. Docker and ingress remain independent
  of the VPN to avoid a control-plane dependency cycle. A controlled reboot
  recovered all eight containers without manual repair; the unit retried once.
- Stopping shared ingress also disconnects administrative VPN access. Ingress
  drills require a VM-local recovery timer and independent console access.

The [lab evidence](evidence/isolated-lab-2026-11-17.md) distinguishes successful
reconciliation, the initial boot failure and corrected reboot recovery. Google
callback verification, off-host backup, clean-host restore and peer replacement
remain separate acceptance checks.
