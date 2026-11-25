# Clean replay preparation — 2026-11-20

A new controller attempt, `clean-20261120-041302`, is prepared for the next empty
HOU1 rehearsal. No reinstall, cold restore or publication has been invoked.
The capture used HOU1 `db27c97` and HOU4 `3d0ae0f`.

## Verified inputs

- Fresh point: `20261120T041357Z-cde1c8403c33`.
  SHA-256: `b197eaffd44eced49e28afd519cdf8d3ef45680560063af9af73c39bcdb64a32`.
  Capture, interrupted collection, automatic retry and both-host readiness passed.
- Off-host ciphertext, offline decryption and SQLite integrity passed. The source
  bundle, saved application image, runtime-key path and current server peer match.
- Scoped registry authentication and seven immutable image selections passed;
  temporary registry credentials were removed.
- A disposable PostgreSQL container imported 187 tables into an empty database.
  Five recovered NetBird databases passed integrity checks. Session cleanup was
  verified; the container and controller plaintext were removed.
- Restore/publication draft syntax and bootstrap/recovery transport declarations
  passed. The drafts deliberately omit the future installation's SSH pin and
  retain false provider-verification, console and access-review assertions.

## Provisioning handoff

Private inputs, hashes and the reviewed-scope provisioning brief are under
`/home/dev/.ssh/hou-lab/handoff-preparation/20261120T041302Z/`, outside VM225.
The Mac provisioning environment is not linked. The provisioning agent must
return the VM225 reinstall plan, VM223 fence and independent recovery checks
before interruption. Point age and post-capture access changes need review.

The planned reinstall affects VM225 only at the existing endpoint. Preserve
VM223's disks/fence and HOU4's data, identity and firewall policy. After reinstall,
verify the new SSH fingerprint and applied provider policy before claiming the
fresh restore attempt. Follow the [handoff](../probes/lab-peer-handoff.md) and
[finalization workflow](../deployment-workflow.md#finalize-a-published-restore).
