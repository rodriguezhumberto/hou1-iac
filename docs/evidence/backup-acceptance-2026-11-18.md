# Lab backup acceptance — 2026-11-18

Scope: existing isolated HOU1/HOU4 test VMs. Production was not targeted.
HOU1 correction: `e2a6dab`; HOU4: `183611d`.
The selected application image and matching source stayed unchanged.

| Check | Result |
| --- | --- |
| First source capture | Failed: SQLite could not open its WAL database under the service filesystem restrictions. Writers recovered. |
| Corrected source capture | Passed through the normal backup playbook; the failed first attempt was retried. |
| Restricted HOU4 transfer | Passed; a complete encrypted point was committed. |
| Offline verification | Passed: authenticated age decryption, archive checks, SQLite integrity and matching application revision. Temporary plaintext was removed. |
| Container stability during capture/transfer | All eight HOU1 and four HOU4 container IDs stayed unchanged. |
| Normal two-host reconciliation | Passed. Only Prometheus was recreated to adopt the backup monitoring configuration. |
| Backup source repeated unchanged | Passed: zero changes and no new capture. |
| Full unchanged-input repeat | Passed: identical configuration/input hashes and stable IDs for all twelve containers. The same recovery point was preserved. |
| Scheduling and cleanup | Both timers and source boot recovery enabled. No partial capture or transfer remained. |
| Transport restrictions | The export key rejected other commands and command arguments. |
| Backup monitoring | Success/freshness metrics matched the committed point; both off-host backup alert rules were loaded and inactive. |

## Corrections and decisions

- Capture copies the stopped SQLite database and its recovery journals into
  private staging before opening SQLite. The live source remains read-only;
  committed WAL data is preserved. The archive format is unchanged.
- Initial capture checks the recorded last-success timestamp. A failed first
  attempt is retried; an earlier successful point avoids capture on every deploy.
- Lab inputs pair both endpoints and the HOU4-generated export-only public key.
  A dedicated lab recovery identity stays on the controller, with an
  owner-encrypted copy. Neither VM received that decryption identity.
- Lab limits are 512 MiB per archive and 4 GiB retained ciphertext. The existing
  free-space guards and retention counts remain in force. This point is 28.5 MiB.

The first evidence-copy attempt collided with the workflow receipt filename
after transfer succeeded. Its failed receipt is retained. A separate point
directory fixed the observer; offline verification reused the committed point.

## Validation

Both new regressions failed against the previous code and passed after correction.
All 16 focused backup tests and HOU1 syntax/lint checks passed. HOU4's full check
passed all 97 tests without skips. HOU1's baseline full check passed all 506
tests without skips using the prepared toolchain, collections and native
validators. The corrected backup module and static checks ran separately.

Verification authenticated and inspected the recovery set; no live restore was
attempted.

## Remaining acceptance

1. Bootstrap empty test VMs from frozen inputs.
2. Interrupt a live transfer and verify retry preserves the previous point.
3. Restore on a clean host and verify PostgreSQL, application and identity behavior.
4. Exercise replacement-peer enrollment and access recovery.
