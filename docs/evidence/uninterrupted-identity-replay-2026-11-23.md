# Fresh-host identity recovery replay — 2026-11-23

Attempt `clean-20261123-181803` passed functional acceptance at HOU1 `53822ad`
and HOU4 `3d0ae0f`. Cold restore, sealed checks, initial publication, declared
prerequisites, normal finalization, receipt-based repeat and planned guest reboot
passed their first invocations. No deployment resume or host repair was needed.

## Starting boundary

The controller verified the provider's fresh VM225 volume, trusted new SSH key,
complete isolation policy and empty Debian installation before restore. Boot
volume GUID `14846708563228296387` replaced `13136385653040997369`; the new SSH
fingerprint was `SHA256:ZbO9iGwXz733D34gsuIv7gBkhY+8j+wFQY7lTib5HpE`.
Policy SHA-256 remained
`a595944411970b783d56ce82df8b7d26d7e38faafaaa4c7c68976e92a799f8d6`.
Managed SSH, encrypted admission transport and the authoritative receiver source
key were bound before managed input validation and execution freezing.
Credentials and assignments were preserved.

Restore selected point `20261123T181903Z-d8f7f9686711`, SHA-256
`77ecb37e9c4c759d09dc4c26448eed5750b7253585884d6db0d80fbd6e51e91e`.
It was captured at 18:19:03 UTC and was 3h 6m 23s old at interruption release.
The owner confirmed no subsequent access or credential changes. Later writes
were excluded; this was not a write freeze.

## Results

- Cold restore recovered 187 PostgreSQL tables in 141.462 seconds for the restore
  operation. Identity bootstrap and application seeding did not run.
- Sealed credential/signing-key checks, approved base admin, Google S256 and
  branding passed, alongside verified TLS, denied writes and reader login/logout.
- Initial publication retained the required monitoring, backup and renewal grants.
  HOU4 and the controller reconnected without resetting HOU4's identity.
- The declared order configured normal runtime and backup, then renewal access
  and TLS status, then initial capture and source monitoring. This exercised the
  renewal comparison and backup observer fixes from the preceding corrected run.
- First normal finalization collected and offline-verified point
  `20261123T221408Z-63e7a7d035e3`, 45,140,475 bytes, SHA-256
  `9c92a1c11f2131bc4c42a1e83ad8326eb7d0631b0f9492d5f016cfcf0973b6f4`.
  Verification plaintext was removed and scheduled source capture was enabled.
- The unchanged receipt-based repeat preserved the complete binding and point,
  all fourteen container IDs and both hosts' boot, instance and SSH identities.
- VM225 reboot recovered pinned private SSH in 38.1 seconds and all eight
  containers in 62.5 seconds without repair. Container IDs, instance ID, SSH key
  and persistent 2 GiB swap survived; HOU4's boot identity and containers stayed
  unchanged. Normal verification passed on both hosts after reboot.
- Google policy, branding, user grants and runtime-secret hashes were unchanged.
  All eight monitoring targets were healthy and all twenty alert rules inactive
  after the transient reboot alert cleared. Discord delivery failure count was
  zero; this was not a new external delivery test.

## Observer correction and limits

An extra read-only identity helper selected the historical SSH pin and was
rejected before remote execution. Its original stderr was retained. The current
frozen-pin observer then passed before and after reboot. This observer correction
was separate from the successful primary recovery and deployment invocations.

Fresh external human Google sign-in and Mac staff, NetBird and private SSH checks
were pending when this report was written. Earlier confirmation applied to the
previous guest. Controller replacement/reboot and certificate rotation/rollback
were not exercised here. Production and VM223 were untouched by controller
actions; VM223's provider fence and disks were retained.

Private receipts, immutable inputs and logs:
`/home/dev/.ssh/hou-lab/handoff-preparation/20261123T181803Z/`.
