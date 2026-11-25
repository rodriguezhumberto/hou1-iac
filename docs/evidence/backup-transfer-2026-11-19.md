# Live backup transfer interruption — 2026-11-19

Scope: existing isolated HOU1/HOU4 VMs; production and provisioning were untouched.
HOU1 implementation: `fb8ac3e`; ordering coverage: `55b817a`.
HOU4 receiver: `9bb4562`. The application release remained `463a28b`.
Companion-access readiness correction: `15a3486`.

## Automated acceptance

The optional `backup` matrix declaration keeps the offline recovery identity on
the controller. Its fingerprint is recorded and its public recipient is matched
before capture. The drill creates a new source point outside the capture guard,
then holds admission during transfer, interruption and verification.

| Check | Result |
| --- | --- |
| Receiver runtime upgrade | Passed through `hou4.yml --tags backup`; the job now has the intended 1,900-second runtime limit. |
| Backup-only matrix | Passed at `fb8ac3e`. The 29,321,966-byte transfer was interrupted after 1,048,576 bytes reached staging. |
| Signal target | Only the collector's pinned export SSH child was killed, using a pidfd after checking its parent and command. |
| Previous point | Ciphertext and metadata stayed intact at interruption; no partial point was published. |
| Automatic retry | The same collector PID retried and committed the exact source point, with one service-start request and no repair. |
| Cleanup and offline recovery checks | Staging was removed. Controller-side age authentication, archive checks, SQLite integrity and source revision passed; plaintext was removed. |
| Corrected combined matrix | All four scenarios passed at `15a3486`. The second interrupted transfer had 180,224 partial bytes; the same collector recovered, offline verification passed and both hosts were ready. |
| Final integration checks | Backup timers, restricted export denials and success/freshness metrics passed; both backup alerts were inactive. Reader login, protected read, write denial and logout passed. |

Private inputs, observations and receipts remain under
`/home/dev/.ssh/hou-lab/transfer-matrix/20261119T045336Z/` and the workflow run
directories. Receipts list the selected scenarios so a backup-only result cannot
be mistaken for a complete matrix run.

## Validation and decisions

- HOU1's final affected check passed 73 tests without skips, plus syntax/lint.
- HOU4's full check passed 101 tests without skips, plus syntax/lint. A real SSH
  child fixture verifies retry and prior-point preservation without mocked pipes.
- The initial HOU4 full invocation selected a Docker parser for a native Nginx
  server fixture. The corrected native-binary invocation passed; no host repair
  was involved.
- `RuntimeMaxSec` bounds the running `Type=exec` job; `TimeoutStartSec` alone
  only bounded startup. The existing three-attempt transport retry is unchanged.
- A missed partial-stream window fails the drill. Ciphertext must exceed three
  MiB. Killing the collector itself remains outside this transport scenario.

The first combined run completed its service cases and source capture, then
failed before starting transfer: HOU1 SSH had recovered but HOU4 returned
`No route to host`. A read-only observation confirmed recovery without repair.
The correction waits up to three minutes for both private paths before starting
collection once. Only access observations retry; target/input drift still fails
immediately. The original failed receipt is retained.

Next acceptance: restore the verified recovery set on a clean test host, including
PostgreSQL import and application/identity recovery gates.
