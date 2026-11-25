# Uninterrupted clean recovery replay — 2026-11-20

Attempt `clean-20261120-064437` passed at HOU1 `b81c3c1` and HOU4 `3d0ae0f`.
No deployment invocation failed; no code, frozen input or host repair was needed
between the initial invocation and completed acceptance.

## Starting boundary

The provisioning agent freshly reinstalled VM225, verified its new volume and
guest-agent SSH fingerprint, and retained the reviewed isolation policy. The
controller matched that key and checked Debian, resources and empty application,
Docker and VPN state. VM223 stayed fenced with its disks preserved; HOU4 retained
its installation. The receiver's authoritative source-key input was prepared
before restore, and managed `--phase inputs` passed before execution was frozen.

Restore used point `20261120T061915Z-dc55589d51e4`, SHA-256
`dd6b4a00bceaedcc8b90c3ed5dfdc5d46c4017de85eab51e29c84b1614142ae7`.
The user confirmed no subsequent access or credential changes; later background
writes were excluded from this selected point.

## Results

- Cold restore passed its first invocation: 187 PostgreSQL tables, 135.887 seconds
  for the restore operation. No identity bootstrap or CMS seed was needed.
- Sealed credential/signing-key checks, TLS, access denials and reader login/logout
  passed before publication. Initial publication passed without resume; controller
  cutover and HOU4 reconnection needed no guest-agent reset.
- First finalization migrated the normal runtime, initialized backup from absent
  source files and verified point `20261120T085006Z-5b0e229db652` off-host, including
  offline decryption and integrity, before enabling scheduled capture.
- A complete receipt-based finalization repeat preserved the full point manifest,
  all eight HOU1/four HOU4 container IDs and both host identities. All 75 frozen
  input hashes matched; verification plaintext was removed after both runs.
- Replacement reboot restored pinned private SSH in 37.6 seconds and all eight
  containers in 59.4 seconds without repair. Its SSH key, instance ID and container
  IDs stayed stable; HOU4's boot identity and containers were unchanged.
- Post-reboot checks passed on both hosts: readiness, reader login/logout, denied
  writes, backup timers, restricted transfer commands, Prometheus backup metrics
  and inactive backup alerts. Controller and HOU4 reported direct P2P transport
  to the replacement endpoint.

This closes the uninterrupted empty-host replay gap left by the
[previous corrected run](clean-replay-2026-11-20.md). External Google sign-in,
actual external alert delivery and TLS renewal remain unexercised here.
Production and hypervisor configuration were untouched by the controller.

Private receipts, frozen inputs and logs:
`/home/dev/.ssh/hou-lab/handoff-preparation/20261120T064437Z/`.
