# Isolated HOU1 metrics for HOU4 (infrastructure #27)

Run `ansible/playbooks/monitoring.yml` against the established private isolated
inventory, with `hou1_monitoring_enabled: true` and the HOU4 NetBird address in
`hou1_monitoring_peer_ip`. Retain the current encrypted controller PAT and age
identity through the existing `netbird_token_vault_file` and
`netbird_token_age_key_file` inputs. An old pre-rebuild PAT can return 404;
restore the current encrypted source rather than reenrolling peers or
rebootstrapping the account. Refresh approved package destination DNS inputs
in the existing test-isolation gate before installation if they have moved.

The `monitoring_exporter` role binds Debian node exporter to HOU1's NetBird
address on TCP/9100. It adds exact one-member source and target groups and one
unidirectional policy, plus UFW ingress only from the declared monitoring peer
on `wt0`. It refuses public peers, ambiguous membership and operator-owned
policy collisions. It does not change other groups, policies or enrollment.
No database listener or Docker API is published. NetBird remains 0.82.0.

A root-only minutely collector inspects fixed test container running/health
state and performs a fixed PostgreSQL aggregate query under `BEGIN READ ONLY`.
It publishes only numeric host/container/database/backup information through
atomic root:prometheus-readable textfiles. No user rows, emails, passwords,
connection strings or secrets enter metrics. Docker access is privileged even
with fixed read-only commands; scripts/config are root-owned and not writable
by the exporter. The service has a bounded runtime and filesystem sandbox.

Backup metrics cover only local encrypted Authentik archive success markers
whose nonempty archive and checksum file exist. They do not rehash each backup
on scrape, measure offsite freshness or replace restore drills. Missing backup
or failed/stale collection is visible to HOU4's opt-in alert rules. Container
health without an image-defined healthcheck means running only; the separate
healthcheck-defined gauge exposes that distinction.

HOU4 enables `hou4_hou1_metrics_enabled` and declares exactly one `hou1-node`
target at this VPN address/port. Its local Prometheus, Alertmanager and exporters
remain on loopback. HOU4 provisions host/service/PostgreSQL/backup/renewal panels
in Grafana and sends firing/resolved notifications to an encrypted Discord
webhook. Its source checkout and private encrypted inputs remain local-only.
The receiver capability is not copied into HOU1 or this repository.

For rollback, first remove the HOU1 target and disable its HOU4 rule switch.
Stop/disable `hou1-monitoring-collect.timer` and `prometheus-node-exporter` on
HOU1, remove the exact source/target/9100 UFW allowance, and delete only the
marked `hou1-monitoring-test-metrics` policy and its two dedicated groups after
checking they have no other references. Remove the installed collector/config,
units, exporter override and textfiles if retiring this integration. Keep SSH,
NetBird and all shared policies intact. This is an additive test integration;
production needs its own reviewed target, secrets and rollout approval.

See the dated evidence document for live results and known limits. Independent
outage detection for HOU4 is explicitly deferred by the operator.

See [2026-11-08 rehearsal evidence](monitoring-vpn-discord-renewal-2026-11-08.md) for measured results and limits.
