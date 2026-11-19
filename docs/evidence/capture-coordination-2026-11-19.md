# Capture/deployment coordination — 2026-11-19

Scope: the existing isolated HOU1/HOU4 lab. Production and VM provisioning were
not changed. The selected application release stayed at `463a28b`.

## Design

- Reuse `/run/lock/hou1-backup-export.lock`; preserve its inode and existing
  writer-recovery journal. One owner admits capture or managed deployment.
- Hold admission across both hosts and final readiness checks, including the
  failure matrix's intentional outages. Another controller also waits.
- Bound admission at fifteen minutes and retain the original capture budget
  inside the service timeout. No timers are disabled or rescheduled.
- Send ten-second controller keepalives and release after a minute of silence.
  SSH EOF releases immediately. Guard loss cancels local Ansible work; interrupted
  remote operations still rely on maintained recovery and reconciliation.
- Refuse deployment while backup writer intent remains. Capture waits before
  reading configuration or touching writers, so it sees the completed deployment.

## Scope of the guard

The supported managed workflow and failure matrix acquire this lock. Direct
Ansible commands, TLS jobs and reboot observers remain outside that admission
path. Capture still briefly pauses its writers after deployment releases admission.
The guard does not make either operation a distributed transaction.

## Recorded checks

The first implementation (`3939849`) passed the complete local check: 527 tests
without skips, plus syntax/lint. Its live overlap run completed both hosts with
all twelve container IDs stable; queued capture then completed without repair.
The final affected check at `d4e1f0a` passed 84 tests without skips, plus
syntax/lint, including admission retry and disconnected-pipe cleanup.

| Live check | Result |
| --- | --- |
| Capture requested during reconciliation | Passed twice, at `3939849` and `6a0572d`. The normal capture service waited without writer stops; both hosts verified with all twelve IDs stable. |
| Queued capture after release | Passed twice without repair; a new complete encrypted point was published after deployment. |
| Admission retry at `bfce586` | A simulated first SSH exit `255` was followed by a live pinned connection holding admission for 65 seconds. Keepalives stayed active. |
| Abandoned controller hold | Withholding only guard keepalives released the live host lock in 60.5 seconds. Both hosts stayed ready and all twelve IDs were stable. |
| HOU4 collection and offline verification | Passed: authenticated decryption, archive checks, SQLite integrity and matching application revision. Temporary plaintext was removed. |
| Failure matrix at `bfce586` | Application repair, monitoring repair, dependency outage and final verification all passed under the shared guard. |
| Final verification at `d4e1f0a` | Both-host readiness/source/TLS checks, reader login/read/write-denial/logout, backup timers, transport denials and metrics passed. Backup alerts were inactive. |

The reconnect fixture exposed a stopped keepalive event surviving admission retry;
the regression failed before the reset fix. A separate buffered-pipe fixture
exposed cleanup masking the original disconnect error. Both are covered by
real-process regressions.

Private logs, source/receiver results and workflow receipts are retained under
`/home/dev/.ssh/hou-lab/coordination/`.

Next acceptance: interrupt a live backup transfer and verify retry preserves the
previous verified point.
