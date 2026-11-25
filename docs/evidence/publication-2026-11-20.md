# Replacement publication and acceptance — 2026-11-20

The user authorized continuing the [normal restore](normal-recovery-2026-11-20.md).
The replacement now owns the original lab endpoint and VPN address. The original
VM remains fenced with its disks preserved. Production was not targeted; HOU4's
distinct-address firewall exception remains deferred.

## Accepted behavior

- Publication, replacement enrollment and private SSH cutover passed through
  `publish-test-recovery.yml` with explicit resume and retained checkpoints.
  Only the pinned lost peer was retired. The existing backup source group and
  policy IDs/grants were preserved and rebound to the replacement.
- The dedicated controller reconnected without replacing its container or
  enrollment. HOU4 reconnected automatically with its existing identity; its
  pinned SSH/sudo check passed, so no guest-agent refresh was needed.
- Both peers report direct P2P UDP transport to the replacement on port 51821.
  The controller's observed NAT endpoint retained that port; the scoped provider
  allowances were sufficient for this run.
- Both hosts passed managed runtime reconciliation and readiness/TLS checks.
  The unchanged repeat preserved all eight HOU1 and four HOU4 container IDs.
  HOU4's IDs also match its pre-replacement baseline.
- Reader login, protected read, write denial and logout revocation passed from
  HOU1 through pinned VPN SSH. A direct controller HTTPS attempt was denied by
  its existing SSH-only VPN group; no grants were broadened for that observer.
- Fresh replacement capture, interrupted transfer, same-process retry, off-host
  collection and offline decryption/integrity passed. Partial output was not
  published; verification plaintext was removed. Backup timers, restricted-command
  denials, Prometheus metrics and inactive backup alerts passed.
- The replacement rebooted without repair: private SSH returned in 49.6 seconds,
  all eight active containers were ready in 60.5 seconds, and its SSH key and
  cloud-init instance ID stayed unchanged. Both-host verification and reader
  login/logout passed again afterward.

## Corrections preserved separately

1. The disconnected controller needed explicit `transport: recovery` to enter
   its retained namespace. The ordinary connected-peer requirement stays intact.
2. Initial publication opened the guest listeners, then stopped before retirement
   because the standalone inputs omitted the backup receiver address. Resume used
   the original declared backup inputs; a new prepublication guard checks it.
3. The next guard found no exporter groups or metrics/staff-probe policy in the
   saved point. Explicit test-only backup scope now requires complete monitoring
   resource absence, keeps exact backup ownership checks and pins the omission
   through resume. Production and legacy scopes keep their monitoring requirement.
4. Cold rendering had dropped Compose ownership metadata. Exact retained-rendering
   comparison preceded metadata-only adoption on this host; the original bytes
   were preserved. Cold restore now writes the standard header/fingerprint.
5. Backup capture first failed before the separate managed source checkout existed.
   Normal runtime reconciliation completed the source/storage migration; backup
   initialization and subsequent capture then passed. No database copying or
   ownership-check bypass was used.

Local commits: `c48f6c1` (recovery transport), `e3e417c` (receiver preflight),
`ce2fd27` (test backup scope), `2d526e6` (Compose ownership). Their affected local
tests and syntax/lint checks passed; the backup-only scope includes interrupted
and repeated-resume coverage. No push or production deployment occurred.

This accepts a corrected, continued-host handoff. A new empty-host run is still
needed to establish uninterrupted recovery with all corrections. External Google
sign-in, real alert delivery and TLS renewal were outside this acceptance run.

Private receipts:
`/home/dev/.ssh/hou-lab/handoff-preparation/20261119T231455Z/`.
