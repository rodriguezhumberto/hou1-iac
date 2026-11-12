# Private HOU1 monitoring (follow-up to infrastructure #19)

The same collector role supports test and production. It requires one declared
private inventory target, the existing identity profile, and a distinct enrolled
HOU4 peer in the selected VPN account. No production deployment or live alert
validation is implied by offline tests.

Select these public inputs explicitly:

| Input | Purpose |
| --- | --- |
| `hou1_monitoring_inventory_group` | One-host private inventory group, such as `hou1_prod` or the isolated rehearsal group. The playbook has no test/prod target default. |
| `hou1_monitoring_identity_profile` | Existing `authentik_identity_profiles` key (`test` or `prod`). Must agree with `cms_environment`, subnet and inventory classification. |
| `hou1_monitoring_enabled` | Boolean `true`; collection is otherwise refused. |
| `hou1_monitoring_peer_ip` | HOU4's enrolled VPN IPv4 address, different from HOU1. |
| `hou1_request_metrics_enabled` | Boolean opt-in for the existing credential-free Nginx request schema. |
| `hou1_staff_probe_enabled` | Boolean opt-in for HOU4-to-HOU1 TCP/443 using the exact existing metrics peer groups; see [staff-login-probe.md](staff-login-probe.md). |

Keep organization-specific deployment roots, domains, allowlists, receiver URLs
and credentials in encrypted inputs. `app_directory` supplies the application
root; no organization name or generated application container name is embedded
in this role. Use a scoped controller grant to read the existing encrypted
NetBird API token. Retire the grant after the release checks.

Run `ansible/playbooks/monitoring.yml` with the selected inventory and the inputs
above. It must connect through `netbird_ip`, with bootstrap disabled. Check mode,
multiple target hosts, public management and mismatched environments fail before
API writes or package installation. The existing isolated package lease remains
in effect during rehearsals; production uses ordinary package management.

Compose working-directory and service labels identify each of the seven expected
services. Missing, duplicate, one-off or mismatched containers fail collection.
Container names and project-generated suffixes are irrelevant. The collector
runs a fixed read-only PostgreSQL aggregate query, emits the existing numeric
metrics, and uses the selected environment's local backup directory or existing
backup-source receipt. It does not expose rows, configuration labels or tokens.
The minutely collector remains a protected host service; this change does not
change the container architecture.

The exporter listens only on HOU1's VPN address at TCP/9100. UFW permits only the
declared HOU4 peer on the VPN interface. NetBird adds exactly two single-peer
groups and one unidirectional TCP/9100 policy; existing test policy names stay
stable. Production uses `hou1-monitoring-production`, `hou1-metrics-production`
and `hou1-monitoring-production-metrics`. Unexpected membership and unmanaged
policy collisions require operator review.

In HOU4, set the matching `hou4_environment` and `netbird_subnet`, enable
`hou4_hou1_metrics_enabled`, and declare one `hou1-node` job targeting HOU1's VPN
address at port 9100. The target must be a distinct VPN peer. Enable
`hou4_request_metrics_enabled` together with request collection on HOU1. HTTPS
probe domains must be explicit allowlisted encrypted inputs for the selected
environment. Keep the Discord destination in its encrypted receiver input.
The companion HOU4 change adds environment labels to target samples and validates
alert firing, hold time, missing data and recovery in both environments. Callback
latency retains the existing 10-minute window, minimum 10 samples, 5-second p95
threshold and 5-minute hold.

Before clearing the production monitoring gate, record the selected HOU1/HOU4
refs and live evidence: successful private scrapes and source collection;
public and unrelated-peer denial; service failure and recovery notifications;
backup freshness; and request/login metrics. Configure separate production
synthetic identities and enable the [environment-bound login probe](staff-login-probe.md)
before relying on end-to-end login monitoring. Production provisioning, fresh
login and owner-confirmed Discord delivery are recorded in
[production operations](evidence/production-operations-2026-11-11.json).
[Replacement handoff](evidence/production-host-recovery-2026-11-12.json) rechecked
collection/probe health, but did not repeat that delivery drill. Repeat affected
checks after routing/receiver changes. Keep rehearsal-only helper guards intact.

For rollback, remove the HOU1 scrape job and disable its HOU4 rule switches,
stop the collector timer/exporter, then remove only the exact source-IP UFW
allowance and the marked environment metrics policy and its two dedicated
groups after checking references. Preserve SSH, enrollment and shared policies.
