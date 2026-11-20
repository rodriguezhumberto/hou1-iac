# Evidence

Reports and JSON receipts record a specific revision, fixture and date. They do
not establish current host status or replace [operational guides](../README.md).
Failed attempts remain separate from corrected or continued-host runs. Keep
private logs, plaintext inputs and credentials outside Git.

| Topic | Report |
| --- | --- |
| Unattended lab renewal and application monitoring | [Scheduling, recovery, metrics and periodic login, 2026-11-20](unattended-lab-2026-11-20.md) |
| Lab operations-channel alerts | [Real firing and resolved delivery, 2026-11-20](lab-alert-delivery-2026-11-20.md) |
| Lab renewal setup and delivery | [ACME dry-run, scoped access and delivery, 2026-11-20](lab-renewal-2026-11-20.md) |
| Latest replacement acceptance | [Uninterrupted clean replay, repeat and reboot, 2026-11-20](uninterrupted-replay-2026-11-20.md) |
| Previous corrected replay | [Fresh restore and corrected finalization, 2026-11-20](clean-replay-2026-11-20.md) |
| Replay preparation | [Verified inputs and provisioning handoff, 2026-11-20](clean-replay-preparation-2026-11-20.md) |
| Published restore finalization | [Ordered backup initialization and resume, 2026-11-20](finalization-2026-11-20.md) |
| Replacement operational handoff | [Publication, backup and reboot acceptance, 2026-11-20](publication-2026-11-20.md) |
| Same-address replacement | [Normal restore and DNS follow-up, 2026-11-20](normal-recovery-2026-11-20.md) |
| Reduced-memory lab | [Concurrent guests, backup and reboot checks, 2026-11-19](reduced-memory-2026-11-19.md) |
| Original lab after hypervisor OOM | [Access, key-pin and backup recovery, 2026-11-19](oom-recovery-2026-11-19.md) |
| Fresh replacement VM | [Quarantined restore and reboot, 2026-11-19](quarantined-recovery-2026-11-19.md) |
| Live backup transport interruption | [Retry and offline verification, 2026-11-19](backup-transfer-2026-11-19.md) |
| Clean restore gate | [Disposable PostgreSQL and artifact restore, 2026-11-19](clean-restore-2026-11-19.md) |
| Scheduled capture and deployment | [Coordination acceptance, 2026-11-19](capture-coordination-2026-11-19.md) |
| Fresh HOU1/HOU4 bootstrap | [Rebuild and acceptance, 2026-11-19](lab-bootstrap-2026-11-19.md) |
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
