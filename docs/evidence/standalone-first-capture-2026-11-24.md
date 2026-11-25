# Standalone first-capture acceptance — 2026-11-24

**R11 passed** on the existing restored test hosts. The public
`scripts/deploy-lab.py --phase finalize --recovery-identity ...` invocation
started with no source configuration, no `last-export.json`, no `ready.export`
and a disabled/inactive source timer. No prerequisite capture was performed.
This is an uninitialized backup-source test, not another fresh-OS replay.

## Successful invocation

The corrected attempt ran from **11:05:29 to 11:20:12 UTC**. All 17 recorded
workflow steps passed, including managed inputs, both-host access checks,
reconciliation, source initialization, first capture, HOU4 reconnection,
collection, offline verification, both final checks and schedule enablement.

- Backup preflight observed an absent source configuration. After initialization,
  the first source-status receipt still had no manifest and `last_success=0`.
- Finalization issued **one** `ensure-capture` request. The resulting local
  point was then selected and bound; it was not supplied by a prerequisite job.
- Point: `20261124T111119Z-6c79e9a56123`, **46,951,805 bytes**.
- Ciphertext SHA256:
  `6646514c3f902f2a0db11e687dac2e83e107ed24c971ba3016fd96f820c44032`.
- HOU4 collected that exact manifest/ciphertext. Offline decryption, safe
  extraction, SQLite integrity and application-revision binding passed.
- Read-only observations kept the source timer disabled through initialization,
  capture and verification. The last workflow step enabled/started it only after
  both final readiness checks passed. This did not include a reboot during the
  disabled interval.
- Writer intent and receiver staging were clear. Verification plaintext was
  removed. Both source and receiver timers were enabled/active afterward.

## Restore of the resulting point

Fresh isolated drill `r12-20261124-112301` restored this newly collected point
from **11:23:01 to 11:26:33 UTC**, using the maintained component-drill entry
point and the constrained profile described in the
[R12 acceptance](collected-point-restore-2026-11-24.md).

Empty PostgreSQL imported **187 tables**, all seven restored services ran on
an internal network with no published ports, recovered identity material passed,
and **29 application/access checks passed**. These included fixture OIDC roles,
session invalidation, NetBird API/discovery/JWKS/dashboard, media/private previews,
restart and database/media rollback. The new archive's original NetBird
`secrets.json` was present and validated against its saved configuration.

The drill had no OOM or resource abort; minimum guest available RAM was **2.49
GiB**. Guest sizing was unchanged. Drill containers/network and temporary
plaintext were removed. Independent checks found the eight primary HOU1
containers unchanged and healthy; both hosts passed maintained readiness again.

## Preservation and execution binding

Before each attempt, backup timers were paused, the prior source configuration,
state, helpers and units were checkpointed, and all six retained HOU4 points
were checkpointed outside active retention. Controller copies were hash-verified.
The active source was then made empty by renaming its old state, not deleting it.
A host-local fallback could restore the prior source if the controller was lost;
it was disarmed only after the successful public invocation.

Afterward, old source files remained byte-identical and all six prior receiver
points remained in verified checkpoints and off-VM copies. Normal retention
removed today's older `20261124T020438Z-c513594d3b1f` from the active set after
committing the new point; its preserved copies were verified. All fourteen
previously running container IDs and both boot identities were preserved.
Expected capture writer stop/start cycles changed their start timestamps.

Execution used clean private snapshots HOU1
`098b04817f836fe607ba91b709ac0ca45b12ebd5` and HOU4
`36940525b05fe0d627ae12964573755f08877d49`, binding the reviewed local fixes.
The application revision remained
`463a28bbcac8ad16f461550eba7e549535d7ab1d`. The installed exporter/collector
hashes matched those snapshots. The working repositories were not committed;
the unrelated HOU4 inventory image edit was excluded. Local finalization tests
passed 23 checks; collector tests passed 12 with one optional native promtool
check skipped.

## Earlier failed attempt and limits

The 10:59:30 invocation stopped during admission reconciliation before backup
initialization/capture. Its original forwarded-key SSH configuration could not
authenticate because the controller's SSH agent was empty after reboot. The
prior source and timers were restored. Loading the existing authorized local
key repaired that controller prerequisite without changing accounts, grants or
SSH pins. A fresh empty-source attempt produced the successful results above;
the failed invocation was not resumed or counted as a pass.

The existing guest also required compatible reconstruction of its missing
NetBird secret source from its saved configuration before testing the fixed
exporter. Values were validated by the maintained recovery helper; no key was
generated, rotated or changed in the running configuration. This was not a
backup capture or identity bootstrap.

R11's empty-source evidence gap is closed at these revisions. The follow-on
restore extends R12 to this new point, not every future point. External Google
browser login, personal-client reconnection, production publication, fresh-OS
replacement and independent controller disaster recovery were not exercised.
Recurring restore drills and controller separation remain separate work.

Protected receipts, snapshots, failed/successful logs, checkpoints and reports
are retained under the controller's private `hou1-r11-validation-20261124`
state directory.
