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
- Keep HOU4 local. Separate code changes from deployment; acceptance runs use
  explicitly authorized test hosts and inputs.

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
were outside that run.

The [intervention audit](deployment-interventions.md) assigns the lab's corrective
steps to maintained code, declared inputs or external prerequisites. The
[managed lab workflow](deployment-workflow.md) passed ordered reconciliation and
standalone verification on both hosts using maintained controller transport.
Opt-in lab DNS/firewall ownership is implemented. Adoption, unchanged repeat and
standalone verification passed on both hosts; container IDs, DNS files, network
allocations and owned firewall rules stayed stable. Controller hostname pins now
apply inside the wrapper's private mount view without changing global files.
Version 2 adds [input contracts](lab-inputs.md): release receipt/bundle checks,
identity-role/client checks and backup key/endpoint checks before host access.
Both repositories' refs, inventories and referenced capabilities are rechecked
before later commands. Version 1 remains compatible with its original contracts.
Generated inventory adoption preserved all 163 evaluated variables and test-group
membership across both hosts. Fresh VPN handoff checks passed before selecting
the new managed profiles; SSH configuration stayed with the existing security
roles. Reviewed private overrides remain explicit; repository shared variables
are referenced rather than copied. See [inventory generation](lab-inventory.md).

The service interruption and dependency-outage matrix is recorded in the
[2026-11-18 failure evidence](evidence/failure-matrix-2026-11-18.md). HOU1
service repair, HOU4 local monitoring repair and HOU4 operation while an HOU1
identity endpoint is unavailable all passed. Backup interruption remains an
offline-only check; the lab timer was disabled for that run. Current-revision reboot
checks recovered both hosts; the HOU1 observer now ignores its retained stopped
rollback container rather than treating it as an active service.
The guarded matrix runner now reproduces those interruptions and cleanup checks
with private evidence and bounded verification retries.

The [backup acceptance run](evidence/backup-acceptance-2026-11-18.md) verified
encrypted HOU1 capture, HOU4 collection and offline decryption/integrity. It
corrected SQLite capture under read-only service restrictions and retry of a
failed first capture. A full unchanged-input repeat preserved all twelve
container IDs and the recovery point; the source backup playbook repeated with
zero changes. Backup timers, restricted transport and monitoring checks passed.

Fresh-host bootstrap from frozen inputs passed on both rebuilt VMs at HOU1
`0bfc7b5` and HOU4 `6acf783`. The first preparation attempt exposed a DNS ordering
bug; the corrected run started again from clean disks. The
[rebuild evidence](evidence/lab-bootstrap-2026-11-19.md) separates those attempts,
unchanged replay and recovery checks.

Capture and managed deployment now share the host capture lock across both hosts
and final checks. Two live overlaps completed without interruption, preserved all
twelve container IDs and produced a new recovery point after admission released.
Bounded controller keepalives prevent an abandoned hold; the failure matrix uses
the same guard. See [coordination acceptance](evidence/capture-coordination-2026-11-19.md).

Direct Ansible commands and reboot observers remain outside this guard. Updated
TLS gateway delivery shares host admission; its optional controller lock also
serializes certificate-bundle updates. The [live transfer drill](evidence/backup-transfer-2026-11-19.md) now passed
alone and in the complete matrix, including automatic retry and offline recovery
checks. Its first combined attempt exposed delayed companion access after capture;
bounded read-only admission fixed that without replaying service-start requests.
The [disposable restore gate](evidence/clean-restore-2026-11-19.md) then imported
PostgreSQL into an empty database and checked recovered SQLite/NetBird state,
session cleanup and network isolation. It does not replace the fresh-VM wipe
checkpoint. A [dedicated replacement VM](evidence/quarantined-recovery-2026-11-19.md)
then passed fresh restore, recovered-identity/OIDC checks and reboot in quarantine.
It remains unpublished; HOU4 connectivity to it is explicitly deferred.
The [original-lab follow-up](evidence/oom-recovery-2026-11-19.md) accepted recovery
after hypervisor OOM and a separately verified cloud-init SSH-key change. Before
further provisioning edits, stabilize cloud-init instance metadata and review
hypervisor memory capacity. The user subsequently reported stable cloud-init
metadata. Both HOU1 VMs then passed [reduced-memory checks](evidence/reduced-memory-2026-11-19.md)
with persistent boot identities, reconciliation, backup retry, login and reboot
recovery. The measured 5–6 GiB configuration supports this lab workload; physical
host capacity remains a provisioning concern. Peer handoff still needs the
firewall, endpoint and identity transition reviewed with the existing recovery
checkpoints.

The [lab handoff plan](probes/lab-peer-handoff.md) now separates endpoint choice,
independent transport preparation, fresh normal restore, publication and rollback.
Quarantined publication is rejected before failure cleanup; test recovery accepts
one RFC1918 controller `/32` while retaining normal isolation inputs. Production's
public-source requirement remains unchanged. The selected handoff preserves
HOU1's existing LAN address after fencing the original VM. Controller reconnect
now uses the existing private transport config and preserves its container,
namespace and saved enrollment. Offline guards and read-only controller preflight
passed; live reconnect and independent companion recovery remain handoff checks.

Test recovery can now retain provider-owned isolation instead of installing the
legacy host gate. Its explicit applied-policy assertion is bound to the actual
replacement LAN address/MAC and retained policy digest; publication/resume refuse
ownership changes. Existing host recovery and production behavior remain the
default. This avoids two owners for the lab boundary. Provider reachability must
include the dedicated controller as well as the companion before interruption.

The [same-address normal restore](evidence/normal-recovery-2026-11-20.md) passed
on the freshly installed replacement while the original remained fenced.
Pre-publication login exposed container DNS allowances lost during firewall/network
setup. A maintained DNS reconciliation repaired the host; cold recovery now
prepares those allowances before writers start. The corrected cold sequence has
offline coverage but has not had a new empty-host run. Publication and real peer
transport remain pending.

The [publication follow-up](evidence/publication-2026-11-20.md) passed replacement
enrollment, controller/private SSH cutover, automatic companion reconnection,
normal runtime migration, fresh verified off-host backup, unchanged container IDs
and replacement reboot. It retains the failed guards and continued-host
corrections separately. A new empty-host run remains the next reproducibility
acceptance step; external Google sign-in, real alert delivery and renewal were
not exercised by this run.

The [finalization workflow](evidence/finalization-2026-11-20.md) now makes normal
runtime migration, backup initialization and verified off-host collection one
controller phase. Two full lab runs preserved the source point and all container
IDs. Resume binds frozen inputs and the selected point; collection lets an older
scheduled transfer finish within the same deadline. Capture runs outside admission
because it needs the same host lock.

The [previous fresh replay](evidence/clean-replay-2026-11-20.md) exposed a stale
SSH check and recovery activation competing for capture admission. Both received
fixes and regressions; its failed and continued invocations remain separate.

The subsequent [uninterrupted replay](evidence/uninterrupted-replay-2026-11-20.md)
closed that acceptance gap at HOU1 `b81c3c1` and HOU4 `3d0ae0f`: empty-host restore,
publication, first verified off-host capture, full unchanged finalization and
reboot passed without corrections. All twelve container IDs survived repeat and
reboot; HOU4 retained its identity and VM223 stayed fenced. External Google
sign-in, actual alert delivery and TLS renewal remain separate checks.

The [renewal follow-up](evidence/lab-renewal-2026-11-20.md) passed ACME dry-run,
scoped account provisioning, verified unchanged delivery and rejection checks.
The subsequent [Discord drill](evidence/lab-alert-delivery-2026-11-20.md) enabled Alertmanager and
verified real firing/resolved delivery through an existing Prometheus rule, with
timed recovery and zero notification failures.

[Unattended lab acceptance](evidence/unattended-lab-2026-11-20.md) then enabled scoped
renewal/status timers, HOU1 service/database/TLS metrics and HOU4's periodic reader
probe. Scheduled execution, transport restart, failure reporting/retry, exact
access boundaries and verified backup passed. All fourteen service IDs survived
normal finalization. Actual certificate rotation, controller-VM reboot/replacement
and human Google login remain separate checks; request-specific metrics stay off.
No WAN publication was added.

The [identity follow-up and matrix](evidence/identity-failure-matrix-2026-11-23.md)
records owner-confirmed Google/staff login and SSH, declared base permissions,
and all four automated interruption scenarios on the updated identity revision.
Fresh empty-host replay with those final inputs is the next recovery acceptance
step; controller replacement and certificate rotation/rollback remain separate.

The [accepted identity replay](evidence/uninterrupted-identity-replay-2026-11-23.md)
closed fresh-host acceptance with the final identity inputs and owner-confirmed
external access. Subsequent [scheduled identity outage acceptance](evidence/scheduled-identity-outage-2026-11-24.md)
and [TLS lifecycle acceptance](evidence/tls-lifecycle-2026-11-24.md) exercised normal
timer retry, changed certificates and injected reload-failure rollback. The latter
retains its failed attempt and certificate-metadata fix separately. Controller
replacement/reboot and human-client automatic reconnection remain distinct checks.
[Controller responsibilities](controller-responsibilities.md) record the later
separation from the coding-agent host; current recovery continues on that host.
