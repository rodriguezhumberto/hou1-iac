# Application lifecycle validation — 2026-11-17

Application source: `b66570a`, branch `cleanup/application-readiness`.
Infrastructure implementation: `656c49b`, branch `cleanup/environment-targeting`.
See the [contract and work order](../application-lifecycle-plan.md).

## Scope and decisions

Separate process liveness from local-storage readiness. Retry startup storage
failures, retain one database connection/job, and preserve schema and authentication.
Keep provider outages outside readiness. Refuse a replaced database to avoid
silently writing to different storage behind an existing connection.

The framework adapter lacks draining, so a thin launcher retains its HTTP server
and gives active requests 25 seconds within Compose's 30-second grace. Source
Compose selects readiness. An optional versioned declaration enables stricter
deployment verification without changing old releases or recovery formats.

At this initial validation point, application code was local and the lab retained
its earlier trusted release. The subsequent publication and rollout are recorded below.

## Results

| Check | Result |
| --- | --- |
| Application unit tests, Node 24.22.2 | 120 passed, no skips |
| Built-application integration | 13 passed, no skips |
| Native Compose parsing | Readiness path and stop grace match source declaration |
| Release-image fixture, no network or published ports | Test/production settings, startup failure, storage recovery, liveness, login restrictions and clean stop passed |
| Infrastructure full suite | 481 tests: 480 passed, one stale fixture error, no skips |
| Corrected fixture recheck | All 15 Compose runtime tests passed; syntax and lint passed again |
| Managed lab verification | Passed on installed legacy release; all 8 HOU1 and 4 HOU4 container IDs unchanged |

The stale fixture selected the first TLS task after a selector had been added.
It now selects the directory task by its declared path. Only this fixture changed
after the full infrastructure run; the full suite was not repeated.

Local HTTP fixtures also verify active-request draining, a stuck-request deadline,
background-work termination and default signal handling for direct adapter imports.

## Published release and lab rollout

Application PR #69 merged as `463a28bbcac8ad16f461550eba7e549535d7ab1d`.
Trusted main workflow `44157536982` passed validation and image fixtures, then
published digest `sha256:8b996a363c06fc95071acbd2183315fab5201340e078ce9393f57544c53edd1f`.
The lab selected its receipt and matching standalone source bundle; production
inputs were not selected.

Infrastructure `f075d46` adds one source-bundle owner shared by bootstrap and
normal webstack updates. This closes the missing VM mirror refresh before Git
checkout; no corrective source command was needed on the host. Four new bundle
tests, six bootstrap tests, three configuration tests and 21 workflow tests passed.
Syntax and lint passed. These focused checks supplement the full-suite result above.

| Lab check | Result |
| --- | --- |
| Pre-deployment database/media snapshot | Created with the installed backup command |
| First maintained reconciliation | Passed on both hosts; only application/Nginx containers replaced |
| Trusted image fixtures on HOU1, no networking | Startup/storage failure and recovery, isolated provider settings and clean shutdown passed |
| Installed application stop and managed repair | Exit 0, readiness recovered, other project containers unchanged |
| Persistence comparison against snapshot | Seven customer/content tables unchanged; media directory empty |
| Repeat deployment and corrected verification | All 12 container IDs stable; source/image/TLS and four HTTPS checks passed |

The repeat's initial verification failed because its newly added probe selected
the namespace root rather than the configured website hostname. That failed run
is retained separately. Correcting the private URL required no DNS or host changes;
the unnecessary validator relaxation was reverted. The example now includes an
application readiness check on its synthetic website hostname.

Private run IDs: first reconciliation `20261117T192932Z-_mbvwr9n`, failed repeat
verification `20261117T194426Z-kkk8z_j1`, corrected standalone verification
`20261117T195727Z-gezjd3qz`.
