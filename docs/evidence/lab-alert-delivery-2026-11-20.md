# Lab Discord delivery — 2026-11-20

At HOU1 `3f7258c` and HOU4 `3d0ae0f`, the operator authorized the existing
operations channel and supplied its webhook through the secure prompt. A new
scoped SOPS bundle holds the credential; earlier recovery inputs remain unchanged.
No webhook value or notification-error log is included here.

## Configuration and drill

The existing Alertmanager and Prometheus roles configured native Discord delivery
with resolved messages, redirects disabled, a 30-second initial wait and unchanged
five-minute group interval. Activation added Alertmanager and recreated Prometheus;
Grafana, blackbox and node-exporter retained their containers. The baseline had
healthy targets and no active alerts.

A local four-minute restart timer was armed before stopping only HOU4's node
exporter. The existing `Hou4MetricsTargetDown` rule fired after its two-minute
hold. Discord accepted the firing notification 162.4 seconds after the drill
started. The controller restarted the exporter, verified its endpoint and
cancelled the fallback timer. Discord accepted resolution at 461.9 seconds.
The notification group interval was not shortened for the test.

## Accepted result and limits

- Notification counters advanced from zero to two, with zero failures.
- All targets recovered and all alerts became inactive; the fallback timer was
  inactive. Both-host readiness checks passed.
- All eight HOU1 containers and all five HOU4 containers retained their IDs
  through the drill. Both boot identities remained stable.
- Alertmanager remains enabled through declared private inputs. No production,
  provider firewall, WAN access or renewal schedule was changed.

HTTP acknowledgement establishes Discord acceptance, not human visibility or
reading. This drill covers the existing metrics-target rule and notification
path; optional application metrics and synthetic-login collection remain disabled.

Private inputs and receipts:
`/home/dev/.ssh/hou-lab/external-acceptance/20261120T093611Z-alerts/`.
