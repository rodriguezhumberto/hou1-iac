# Automatic client reconnection — 2026-11-24

Lab acceptance passed at HOU1 `baa2e6b` and HOU4 `3d0ae0f`. The owner left the
Mac awake with NetBird connected and confirmed automatic recovery afterward,
without toggling NetBird, clicking reconnect or signing in again. Staff access
and private SSH still worked.

## Interruption and results

- Baseline checks selected the previously accepted Mac peer through its declared
  owner identity and address. Its management connection was active before the
  interruption. Both hosts passed normal verification and access-hash readback.
- Before shutdown, the guest armed an independent local restart of the exact lab
  VPN management container after 90 seconds. A separate local job stopped that
  container three seconds after scheduling. Neither job depended on continuing
  controller VPN access. Source capture was excluded during the interruption and
  its original timer restored by the recovery job.
- The management API timed out during the outage. Guest shutdown and restart jobs
  completed successfully. The controller and both managed-host clients recovered
  their management/signal connections without refresh, re-enrollment, client
  restart or identity reset. Their client processes, public keys and addresses
  matched the baseline.
- The Mac's same peer returned connected with a fresh contact time after restart.
  Peer identity and grants were unchanged; the owner confirmed the automatic
  client behavior and working staff/SSH access.
- Both host boot/instance/SSH identities, fourteen running container identities
  and the retained dormant container were preserved. Access-policy and secret
  hashes matched. Final managed checks passed on both hosts. Eight monitoring
  targets were healthy and all twenty alert rules inactive.
- All seven controller timers were active and enabled. Production controller root
  configuration and user-unit hashes matched. Expired transient guest timers were
  unloaded or disarmed; no controller migration or reboot ran.

## Observer corrections and scope

The initial observer stopped on an unwrapped HTTPS read timeout during the
intentional outage. The original failed receipt was retained. Observation
continued through the existing explicit recovery transport; a normal transport
entry had correctly refused disconnected management. No second shutdown was
submitted and no client refresh was used.

The first continuation then stopped during cleanup because systemd had already
unloaded an expired transient timer. Its successful shutdown/restart service
results were preserved. Corrected cleanup accepted an absent expired timer, and
read-only checks completed against the same recovered system. Both observer
failures remain separate from the final acceptance receipt.

This exercised a management-server interruption. Established peer-to-peer traffic
could continue; it was not a forced data-path outage, client sleep/wake test,
network change or replacement of the management identity. Independent controller
replacement/reboot remains separate acceptance work.

## Controller recovery audit

The historical owner-only encrypted renewal copy matches its original ciphertext
checksum. Its off-controller retention, owner decryption and coverage of current
controller state remain unverified; it predates the rebuilt guest's pins. The
declared scoped lab keys do not match its owner recipient. No owner key was
requested or copied, and this audit did not decrypt that archive.

Before independent controller-loss acceptance, establish a current complete
owner-controlled package and verify its decryption and off-host retention. The
[controller handoff](../controller-responsibilities.md) retains this work for the
later separation from the coding-agent host.

Private evidence:
`/home/dev/.ssh/hou-lab/operational-acceptance/20261124-client-reconnect/acceptance.json`.
The private controller handoff includes the recovery-artifact audit.
