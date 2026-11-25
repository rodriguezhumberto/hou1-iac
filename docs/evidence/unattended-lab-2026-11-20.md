# Unattended lab renewal and monitoring — 2026-11-20

Lab acceptance passed at HOU1 `e295742` and HOU4 `3d0ae0f`. The provisioning API
pin follow-up at `8dbe8e4` also passed native TLS tests and live peer reconciliation.
Production launcher/configuration/sudoers digests were unchanged; no provider or
WAN policy was changed.

## Implemented and verified

- A dedicated persistent VPN peer permits only HOU1 TCP/22 through the overlay.
  The named `lab` executor has separate launcher, configuration and sudoers files.
  Private-origin pins cover provisioning and container DNS while preserving TLS
  hostname/CA checks. Other jobs, arbitrary commands and unexpected pins are refused.
- Test renewal and minutely status timers are enabled with user-manager lingering.
  A native one-off timer triggered the normal renewal service successfully. After
  restarting its VPN container, the same peer identity delivered and verified
  certificates again. The regular twice-daily calendar was unchanged.
- Controller bundle updates share the managed workflow lock; gateway delivery
  shares capture admission and refuses interrupted writers. Ansible imports retain
  their existing admission path. Real-process lock tests cover contention, timeout,
  symlink refusal and avoiding a second lock during admitted imports.
- A temporary pre-execution service fault published failed renewal status without
  changing certificate bytes or advancing success. After removing the fault,
  systemd's bounded retry recovered without a manual recovery start/reset.
- HOU1 now collects seven service states, PostgreSQL aggregates and renewal status.
  HOU4 scrapes them and runs the dedicated reader login/read/write-denial/logout
  probe. Two successful samples showed the probe advancing on its normal cycle.
  All eight scrape targets were healthy and all 20 alert rules inactive.
- Live boundary tests allowed HOU4's scrape and denied the renewal peer HOU1
  HTTPS/metrics and HOU4 SSH. Reader and human identity grants were unchanged;
  only the declared machine transport policies were added.

## Recovery and reconciliation

Fresh point `20261120T104901Z-01ff6ed9e6f1`, SHA-256
`1df02c66bff02769bed5cb3fe2c8477690284a58a1f10f07e0a8cfd755a009cd`, captured
the updated lab state. Both private paths recovered automatically after capture.
Normal finalization reconciled both hosts, collected that exact point on HOU4,
verified offline decryption/integrity and removed verification plaintext.
All eight HOU1/six HOU4 container IDs and both boot identities survived the full
reconciliation. The earlier clean replay's 75 frozen inputs remain unchanged.

An owner-only encrypted copy of renewal state was retained on this controller;
no plaintext archive was written. Off-controller retention and replacement-controller
restore were not tested.

## Corrections and limits

The first peer invocation enrolled successfully but its root installer was blocked
by the administrative wrapper's deliberate `NoNewPrivileges`. The controller-local
playbook then reused the peer outside that wrapper. A monitoring invocation skipped
all hosts because its selector was only host-scoped; a source observer caught the
missing collector before fault injection. The selector now has an explicit
extra-vars owner, and unmatched patterns fail in managed commands. Failed/skipped
records remain separate from corrected runs.

Validation passed 96 focused tests, all playbook/source syntax checks and lint.
This run did not reboot the controller VM, force certificate rotation, exercise
reload-failure rollback or perform upstream Google login. Request-specific metrics
remain disabled. The [previous Discord drill](lab-alert-delivery-2026-11-20.md)
establishes firing/resolved delivery; this run required no additional test messages.

Private inputs and receipts:
`/home/dev/.ssh/hou-lab/external-acceptance/20261120T100358Z-unattended/`.
