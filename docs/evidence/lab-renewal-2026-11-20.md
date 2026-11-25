# Lab renewal follow-up — 2026-11-20

At HOU1 `47172ce` and HOU4 `3d0ae0f`, the restored lab had no renewal account,
Alertmanager receiver or enabled renewal schedule. These were setup gaps beyond
the [clean recovery replay](uninterrupted-replay-2026-11-20.md).

## Verified

- The maintained controller's ACME dry-run passed for all three test lineages.
  Deployed certificates and encrypted replay inputs were unchanged.
- `tls-renewal-access.yml` configured a dedicated controller-only SSH key and
  non-administrative account restricted to the controller's exact VPN source.
- `controller-tls.py --deploy` and `--check-config` passed using separate private
  test renewal state, preserving the replay's frozen encrypted source material.
  The no-op delivery verified served certificates without changing their bytes.
- The gateway rejected arbitrary commands, additional arguments and shell syntax.
  A wrong-environment payload was rejected before writes; installed certificate
  material and the prior success receipt stayed unchanged.
- Both-host readiness passed. All twelve container IDs and both boot identities
  matched the pre-check baseline. No production or provider policy was changed.

## Remaining

Automatic renewal remains disabled pending persistent transport and scheduling
acceptance. This run did not rotate a certificate or exercise rollback after a
reload failure. Alertmanager remains disabled until a notification destination
is supplied; historical encrypted references were not decryptable with available
controller identities. External Google login is deferred because the user's
device has no current path to the private lab. No WAN access was opened.

Private receipts: `/home/dev/.ssh/hou-lab/external-acceptance/20261120T091801Z/`.
