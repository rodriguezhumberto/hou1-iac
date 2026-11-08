# Credential-free HTTP and staff login monitoring

Infrastructure #27 and staff #39 add HTTP/login coverage to the existing private
HOU1 exporter and HOU4 Prometheus integration. No new public API, logging service,
identity database or external metrics service is introduced. NetBird stays
0.82.0 and independent HOU4-loss detection remains deferred.

The staff OIDC callback sends a fixed `X-Staff-Login-Result` upstream response
header: success only after token validation and session creation, failure after
any caught callback error. Nginx consumes and hides this header. It never trusts
a request header for the result. Callback success/failure remains a generic 302
redirect with private/no-store response handling. The marker is an observation
of the application decision, not independent proof that token validation is
correct; the security suite remains required for that proof.

Nginx writes a separate JSONL metrics stream containing schema version, time,
fixed service/route labels, status, duration and a fixed outcome. It never adds
request paths, URLs, query strings, codes, cookies, tokens, email, subjects,
client IPs or raw headers to that stream. Existing operational logging is
preserved and has its existing custody requirements. Only proxied callbacks
count as login attempts; public VPN denials are HTTP status metrics and cannot
create login failures. Unexpected/missing application outcome is recorded as
unknown and alerts, rather than being classified as success.

The HOU1 `hou1_request_metrics_enabled` switch is off by default. Enable only
after deploying the application marker and Nginx schema. The fixed test path is
`/opt/pearlandpaws-test/nginx-logs/metrics.jsonl`. Root-only collector state is
`/var/lib/hou1-monitoring/requests.json`; numeric textfiles retain their existing
root:prometheus 0640 boundary. The collector records counters, callback duration
histogram and the consecutive callback failure count. It does not open another
listener. Its existing minutely systemd sandbox gains write access only to its
private numeric state directory. A missing/bad/oversized stream produces a
failed request-source metric and overall failed collection; it does not silently
skip records. The last good numeric state is retained on disk. Request counters are omitted
from a failed collection and resume from that state after recovery.

Ingestion is bounded to 16 MiB and 1024 bytes per record per collection. A
partial current record waits for the next pass. The collector follows retained
inode offsets across rename rotation, including final writes made while Nginx
reopens the old file. Root logrotate runs hourly, rotating daily or when the file
exceeds 10 MiB, retaining three uncompressed files and reopening Nginx logs.
This is an hourly size check, not a hard disk quota. Lost unread rotations,
truncation, invalid schema or symlink paths fail visibly. Counter state deletion
resets counters and loses the unread history; investigate and save evidence
before performing a deliberate reset. Restore/rollback should retain this state
or explicitly record the counter reset. A strict ordering of callback completion
within the same timestamp is limited by Nginx log order.

Enable `hou4_request_metrics_enabled: true` only with the HOU1 metrics source.
The six additional rules are:

| Signal | Alert threshold |
| --- | --- |
| Request collector | Failed/missing for 2 minutes |
| Nginx server errors per fixed service | More than 1% over 2 minutes, held for 2 minutes |
| Staff login failures | 3 consecutive completed callback failures/unknown outcomes, or 3 initiation failures within 2 minutes |
| Staff callback failure rate | More than 1% failure/unknown over 2 minutes, held for 2 minutes |
| Callback processing p95 | More than 2 seconds over 5 minutes, held for 2 minutes |
| Callback outcome missing | Any unknown callback outcome within 5 minutes |

The existing no-data/target/stale collector alerts remain active. Rates and
histogram quantiles need samples and traffic; an idle interval is not presented
as measured zero latency. Callback duration is server processing through Nginx,
not the time a person spends completing Google sign-in. Initiation failure
counts cover an app/provider discovery error, not a user denied at the VPN edge.
Application-denied, expired or replayed callbacks are expected to count as
failures; security drills may therefore page. Routine synthetic health checks
perform login, protected reads, reader write denial and logout revocation without
intentional callback replay. The maintained HOU1 source is
`scripts/rehearsal-staff-health.py`; the controller installer preserves a copy
next to its monitor config. Deploy that source to the script path in the private
`staff_probe_command` before enabling this coverage. Dedicated security probes
continue to test replay explicitly. Consecutive failures clear after
a successful callback, not merely after an idle interval. Rate windows clear as
failures age out. Dashboard panels are added to HOU1 Operations, preserving its
UID and existing private access boundaries. Alert thresholds use the stricter
infrastructure login-rate gate; production abort thresholds still require the
operator's release decision and measured health checks.

Deployment order: application callback and Nginx schema; HOU1 collector with
source enabled; HOU4 rules/panels with source enabled. Confirm the request source
is successful, issue a valid isolated test login and an invalid callback, then
verify counter changes and the successful callback/session behavior. Test
Nginx/log schema with sentinel credentials and verify none appear in metrics.
Record native PromQL firing/hold/recovery evidence and at least one actual
end-to-end login-failure/recovery notification. Use only test clients/users.

For rollback, disable HOU4 request rules first, then disable the HOU1 request
source and rerun its role (removes its metrics rotation cron). Restore the
previous application image and Nginx template, validate Nginx before reload,
and check login, public denial and VPN metrics. Preserve private numeric state
and avoid appending a different schema to the same stream. Existing host,
PostgreSQL, container, backup and renewal monitoring continue separately.

Production support for the test-only HOU1 role and actual production identities,
secrets and recovery custody remain separate rollout work.
