# Failure matrix — 2026-11-18

The isolated HOU1/HOU4 lab used the maintained network wrapper, pinned
repositories and private inventories. No production hosts were involved.

| Scenario | Expected behavior | Result |
| --- | --- | --- |
| Stop the HOU1 application backend, then reconcile that service | The backend returns healthy without recreating unrelated services | Passed |
| Stop HOU4 Prometheus, then reconcile monitoring | Prometheus returns ready and the other monitoring services remain available | Passed |
| Stop a HOU1 identity service, then run the normal HOU4 monitoring playbook | HOU4 stays locally ready; the remote probe records failure without aborting HOU4 | Passed. The affected probe reported `0`; HOU4 Prometheus remained ready. |
| Interrupt the HOU4 backup transfer | A failed transfer leaves the previous verified state intact and can be retried | Offline backup tests passed. The lab backup timer is disabled, so no live transfer was interrupted. |
| Reboot HOU4 on the current revision | SSH and all declared monitoring services recover without repair | Passed: SSH in 51.1 seconds; four containers ready in 51.5 seconds. |
| Reboot HOU1 on the current revision | SSH and all active services recover without repair | Passed after correcting the observer: SSH in 41.1 seconds; eight active containers ready in 58.2 seconds; application readiness passed. |

The first direct Ansible verification attempt omitted the pinned collections and
failed before host changes. Repeating it through the maintained lab wrapper
supplied the repository environment and passed. This is a controller invocation
requirement, not a host recovery failure.

All intentionally stopped services were restored and final HOU1 application and
HOU4 monitoring readiness checks passed.

The first HOU1 reboot observation exposed the retained rollback container as a
test-observer false failure. The corrected observer was rerun successfully.

The reboot observer now counts only active containers and still rejects a missing
or unexpected running service. Retained stopped rollback containers therefore do
not produce a false failure.
