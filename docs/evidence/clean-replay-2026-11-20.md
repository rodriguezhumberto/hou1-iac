# Fresh restore and continued finalization — 2026-11-20

VM225 was freshly reinstalled at the existing lab endpoint. Its presented SSH
key matched the trusted guest-agent fingerprint. Independent recovery, persistent
swap, ballooning and provider isolation were verified by the provisioning agent;
the controller checked actual identity, resources and empty application/Docker
state. VM223 remained fenced with its disks preserved; HOU4 was retained.

## Accepted results

- Cold restore and initial publication passed their first invocations at HOU1
  `395bee6`, using point `20261120T041357Z-cde1c8403c33`. Restore took 134.993
  seconds and imported 187 PostgreSQL tables. No identity bootstrap, CMS seed,
  manual DNS repair or Compose ownership adoption was needed.
- Sealed credential/signing-key checks, TLS, expected access denials and reader
  login/logout passed before publication. Controller cutover passed; HOU4
  reconnected without a guest-agent reset.
- Corrected finalization at `e2c8962` created the host's first point,
  `20261120T061915Z-dc55589d51e4`, and verified collection, offline decryption and
  integrity before activating its source timer. HOU4 remained at `3d0ae0f`.
- A full receipt-based repeat preserved that exact point and all eight HOU1/four
  HOU4 service-to-container-ID mappings. Verification plaintext was removed.
- Reboot recovered private SSH in 42.6 seconds and all eight HOU1 containers in
  59 seconds without repair. SSH key, instance ID and container IDs stayed stable.
- Post-reboot readiness, reader login/logout, timer/transport restrictions,
  Prometheus backup metrics and inactive backup alerts passed. Both peers
  established direct P2P transport to the replacement LAN endpoint.

## Corrections and scope

The entire sequence was not uninterrupted. A local input check first rejected
an older receiver host-key override; its private owner was updated and a new
snapshot rendered. Finalization then exposed two failures:

1. A delegated security check used separate stale SSH options. `8d778d0` binds
   that check to the workflow's verified transport, including over extra-vars.
2. Fresh backup configuration started writer recovery while admission held the
   same lock. `e2c8962` defers activation until release and starts new scheduling
   after verification, while preserving existing active timers.

Regression tests failed before each fix. Affected validation passed 77 and 85
tests respectively, plus syntax/lint. Failed invocations and corrected runs remain
separate. Read-only observer corrections are noted privately. Another empty-host
run is required to claim uninterrupted recovery with these final changes;
external Google login, actual alert delivery and TLS renewal were not exercised.

Private receipts and inputs:
`/home/dev/.ssh/hou-lab/handoff-preparation/20261120T041302Z/`.
