# VPN metrics, Discord alerts and renewal rehearsal — 2026-11-08 UTC

The isolated HOU1/HOU4 deployment now exports private host/service/database
metrics, sends real firing and resolved notifications to Discord, monitors the
controller-managed Grafana renewal job, and provisions HOU4 Operations in Grafana.
Infrastructure #27 remains open for the complete isolated rollout acceptance.
HOU4 changes stay in its local repository; no production promotion occurred.

[Redacted measured evidence](evidence/monitoring-vpn-discord-renewal-2026-11-08.json)
records source hashes, timestamps, deployment recaps, service/policy readback,
queries and notification counters. Webhook values, personal identities, tokens,
passwords, DNS credentials and age private keys are excluded.

| Check | Result |
| --- | --- |
| HOU4 full suite | 38 passed, no skips; native config, PromQL hold/recovery/absence and delivery fixtures |
| HOU1 full suite after rebasing on main | 136 run; 135 passed, one pre-existing native DNS test skipped (controller dnsmasq unavailable) |
| HOU1 focused tests on final guard/collector/policy | 4 passed; includes public/production/bootstrap refusal before API or host changes |
| Syntax/config validation | HOU1 application and metrics playbooks, HOU4 playbook, native Prometheus and Alertmanager parsers passed |
| HOU1 final Ansible rerun | rehearsal_vps              : ok=31   changed=0    unreachable=0    failed=0    skipped=3    rescued=0    ignored=0 |
| HOU4 final monitoring/renewal Ansible rerun | test_hou4                  : ok=95   changed=0    unreachable=0    failed=0    skipped=25   rescued=0    ignored=0 |
| Prometheus | 8 healthy targets; 3 HTTPS probes pass; 17 inactive rules after recovery |
| HOU1 custom sources | 7 healthy test containers, read-only PostgreSQL query and local Authentik backup freshness verified |
| VPN boundary | HOU4 .22 can scrape HOU1 .10:9100; administrator peer and public source denied |
| Public exposure | HOU1 metrics and HOU4 SSH/HTTP/HTTPS/monitoring ports remain unreachable publicly |
| Grafana HTTPS | Trusted certificate, private administrator login, secure cookie, datasource query, logout and old-cookie rejection passed |
| Dashboard | Provisioned HOU4 Operations, stable UID hou4-operations, 16 panels; every panel query returns data |
| Discord live drill | Existing renewal-failure rule fired after its 2-minute hold; firing and recovery both accepted; notification total 0 → 1 → 2, failures 0 |
| Renewal publication | Normal publisher resumed, both normal timers enabled/active; latest publisher result success |

The Discord drill injected only a bounded numeric failed-last-run status. It did
not fail ACME, change the certificate or stop/restart NetBird. A separate transient
4-minute recovery timer protected the paused publisher. The normal publisher was
restored after firing delivery, and the recovery message arrived under the
unchanged 5-minute Alertmanager group interval. The helper's final cleanup tried
to stop an already expired transient timer and exited 5 after the delivery
assertions passed. Cleanup now checks unit existence; an independent final
check verified the recovery unit absent and both normal timers healthy. No
second fault or additional test notification was needed.

The deployment encountered an obsolete pre-rebuild NetBird PAT and stale
package-repository DNS addresses in the existing isolated egress allowlist.
The controller was pointed to its current encrypted PAT; only freshly resolved
public package-host addresses were added after checking production denials.
Those denials and Google workload exceptions stayed intact. The final metrics
preflight also has native Ansible tests proving nonmember addresses are rejected
when the IP filter returns null. Final deployment reruns changed nothing.

HOU4 retained about 1118 MiB available RAM on the 2 GiB node during the
final audit. The exporter and all monitoring APIs remain loopback-only on HOU4;
Nginx exposes Grafana only at its NetBird address with the existing administrator
/32 ACL. No default allow-all VPN policy was enabled. The HOU1 collector's
systemd service completed successfully under its sandbox and timer.

The general Discord webhook was selected from HOU1's Ansible Vault and imported
into HOU4's SOPS/age input without printing it. The staff intake webhook was not
changed. The sole active Alertmanager route is Discord with resolved delivery,
TLS validation and redirects disabled; its host configuration is root-owned
0640. The original Vault entry remains for the separate secrets-consolidation
issue. Temporary plaintext deployment inputs were removed after the final run;
the encrypted monitoring input and controller renewal state remain available.

Independent HOU4 outage detection is explicitly deferred by the operator.
Loss of HOU4 cannot notify from that node. Local Authentik backup freshness does
not prove offsite freshness or replace restore tests. Discord HTTP acceptance
does not prove a human read the message. This integration does not add metrics
for other backups or authenticated application error rates. No reboot or VPN
restart was performed in this step. The existing renewal schedule remains
controller-managed.
