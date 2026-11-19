# Evidence

Reports and JSON receipts record a specific revision, fixture and date. They do
not establish current host status or replace [operational guides](../README.md).
Failed attempts remain separate from corrected or continued-host runs. Keep
private logs, plaintext inputs and credentials outside Git.

| Topic | Report |
| --- | --- |
| Lab backup capture and collection | [Acceptance and unchanged repeat, 2026-11-18](backup-acceptance-2026-11-18.md) |
| Service interruption and dependency failure | [Failure matrix, 2026-11-18](failure-matrix-2026-11-18.md) |
| Application lifecycle | [Local fixtures and legacy lab compatibility, 2026-11-17](application-lifecycle-2026-11-17.md) |
| Isolated HOU1/HOU4 lab | [Deployment and acceptance, 2026-11-17](isolated-lab-2026-11-17.md) |
| Initial fixtures | [Homelab](homelab-test-evidence-2026-11-06.md), [VPS](vps-test-evidence-2026-11-06.md) |
| OIDC security | [Initial checks](live-security-evidence-2026-11-06.md), [follow-up](live-security-followup-2026-11-07.md), [acceptance](oidc-security-acceptance-2026-11-07.md) |
| JWKS cache overlap | [Measured limits](jwks-overlap-evidence-2026-11-07.md) |
| User lifecycle | [Admission](netbird-admission-evidence-2026-11-07.md), [offboarding](operator-offboarding-evidence-2026-11-07.md) |
| Monitoring | [Isolation](monitoring-isolation-evidence-2026-11-06.md), [VPN/Discord/renewal](monitoring-vpn-discord-renewal-2026-11-08.md) |
| Component recovery | [Baseline and corrected drill](component-recovery-drill-2026-11-10.md) |
| Rehearsal journals | [Fresh host](fresh-host-rehearsal.md), [isolation](rehearsal-monitoring-isolation.md), [VPN metrics](rehearsal-vpn-monitoring.md) |

Machine-readable reports are kept alongside these summaries. Production release,
recovery, monitoring, TLS and migration receipts are linked from their current
guides. Preserve report contents and immutable artifact references when adding
new results; do not relabel an earlier attempt as a new pass.
