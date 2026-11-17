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

New application code is local. The lab retains its existing trusted release;
activation needs approved publication, a trusted CI image/receipt and matching
source bundle before selecting a fresh inventory snapshot.

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
