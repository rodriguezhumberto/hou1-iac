# Reduced-memory lab validation — 2026-11-19

The original HOU1 VM and quarantined recovery VM ran together with the user's
selected 5 GiB balloon minimum, 6 GiB maximum and 2 GiB guest swap. HOU4 remained
at 4 GiB. Guest measurements showed changing usable allocations of 4.798–5.798 GiB
on both HOU1 VMs; Proxmox settings and physical-host headroom were not independently
read. Production was not targeted.

Revisions: HOU1 `1e7088e`, HOU4 `3d0ae0f`, application `463a28b`.

## Workload results

| Check | Result |
| --- | --- |
| Unchanged reconciliation | Both hosts passed; all eight HOU1 and four HOU4 container IDs stayed unchanged. |
| Backup interruption | Fresh encrypted capture, interrupted transfer, same-process retry and offline decryption/integrity passed. Prior point preserved; partial transfer never published; temporary plaintext removed. |
| Recovery VM | Recovered credentials, TLS/readiness, access denials and reader OIDC passed before and after reboot. SSH returned in 33.2 seconds; seven services ready in 63.3 seconds without repair. |
| Original HOU1 reboot | Private SSH returned in 41.0 seconds; eight services ready in 63.5 seconds without repair. Maintained HOU1/HOU4 verification then passed. |
| Boot identity | Both SSH keys and cloud-init instance IDs stayed unchanged; boot IDs changed. Persistent swap and balloon devices remained present. |
| Final checks | Reader login, protected read, write denial, logout revocation, backup timers, restricted-command denials, collection metrics and inactive backup alerts passed. |

## Memory observations

Sampling covered 09:03:32–09:19:48 UTC, including both reboots.

| Measurement | Original HOU1 | Recovery VM |
| --- | --- | --- |
| Successful samples | 104 | 108 |
| Lowest available RAM | 2.857 GiB | 3.209 GiB |
| Largest estimated used RAM (`MemTotal − MemAvailable`) | 1.941 GiB | 1.645 GiB |
| Maximum swap occupied | 256 KiB | 256 KiB |
| Swap-in/out counter increases within each sampled boot | 0 | 0 |
| Maximum sampled memory PSI `some`/`full` ten-second average | 0 / 0 | 0 / 0 |
| Observed guest/container OOM kills | 0 | 0 |

Five HOU1 observations and one recovery observation were unavailable during
capture/reboot transitions; these are gaps, not memory measurements. The first
login observer failed before host access because its private SSH-config path did
not exist. Selecting the existing pinned config fixed the observer without host
changes; the failed log was retained.

The 5–6 GiB allocation is suitable for this measured lab workload. This was not
a production load test, simultaneous cold boot or new cold import; the original
[cold restore](quarantined-recovery-2026-11-19.md) ran at 8 GiB. Guest swap does not
establish physical hypervisor capacity. The recovery VM remains quarantined and
unpublished; HOU4 connectivity to it and peer handoff remain deferred.

Private samples and receipts: `/home/dev/.ssh/hou-lab/memory-validation/20261119T090231Z/`.
Backup matrix: `workflow-runs/20261119T091434Z-kh5ls3wb`; final verification:
`workflow-runs/20261119T091818Z-atvt6zh9`, under the same private lab root.
