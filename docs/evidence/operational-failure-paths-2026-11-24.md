# Operational failure-path acceptance — 2026-11-24

The three remaining targeted operational checks passed on the existing test
hosts. This validates the reviewed fixes at the execution revisions below;
it is not a production deployment or another fresh-host recovery.

## Interrupted TLS activation

The installed receiver and real Nginx passed three actual process-death cases:
between certificate/key writes, after all six files for three lineages were
written but before reload, and during recovery of a pending activation journal.

Each case retained a root-only journal, continued serving the old valid
certificates, and refused pending verification. Uninstrumented receiver
redelivery repaired the files, activated the candidate, verified the served
certificate fingerprints against the trusted names, and removed the journal.
Original certificate bytes, ownership, permissions, link representation and
served fingerprints were restored after every case.

Capture admission and controller coordination locks were held during faults.
Only the lab TLS timer was paused; a host-local fallback protected restoration.
The timer was restored and the fallback disarmed. Receiver code/configuration
and the controller's encrypted ACME/certificate state were unchanged. The test
reused trusted, unexpired candidate certificates; it performed no new issuance.

## Native firewall publication cleanup

UFW 0.36.2 with native IPv4/IPv6 kernel rules passed eight SIGKILL boundaries:
intent, each Compose rewrite, each of two firewall additions, each service
start, and the ingress syntax check. Reentry restored the private Compose
bytes, removed only owned rules, preserved pre-existing SSH/HTTP rules, retained
the first error and refused blind publication resume. No success receipt was
produced. Repeated cleanup preserved the error latch.

The test used isolated network/mount namespaces with private UFW configuration
and locks. Live host UFW file hashes and rule dumps matched before/after;
generated dump timestamps and counters were excluded from comparison.
**Compose service calls were recording substitutes**, so this establishes
native firewall cleanup, not a full Docker publication interruption replay.

## Google-source identity and recovered image digests

The maintained `provision_base_users` producer created disposable internal
users at the exact Google source path, without passwords or invitations.
Their usernames did not qualify for the historical test-fixture exception.

The public offboarding CLI rejected an incorrect image digest on apply before
creating a manifest or changing access. With the exact reviewed recovery image
references, read-only plan, apply and same-operation retry passed. The copied,
encrypted test admission entries were removed; persistent staff blocking,
identity disablement, empty groups and denial readback passed. The operation
recorded the authenticated SSH/sudo actor in durable root audit records.

Control identities and VPN users/peers retained their configuration and grants.
Only `last_login` and VPN `last_seen` observations were excluded from comparison.
Canonical admission ciphertext matched its readiness fingerprint. Two synthetic
users remain inactive with persistent subject blocks for audit. Temporary
encrypted operator, token and admission copies were removed.

These users had no existing sessions, VPN registration or owned peers. This
run therefore **does not establish fresh external Google browser login,
existing-cookie/token revocation or owned-peer deletion**. Those broader paths
are separate from the identity-shape and recovered-image compatibility checks.

## Preservation, checks and evidence

Both hosts passed the maintained workflow's `verify` phase. All eight primary
containers on the first host and six on the second remained healthy, with
container IDs, images, running states and start timestamps preserved. Both boot
IDs were unchanged. TLS activation and writer-intent journals were absent;
backup timer state was preserved and the controller TLS timer was active.
Guest namespace workspaces, TLS fault harness/original bundle and fallback
timers were removed.

Local suites passed **51 tests**: TLS 20, publication interruption 7 and
offboarding 24. The Makefile test entry point was used for each suite.

Execution used clean private snapshots:

- First host: `098b04817f836fe607ba91b709ac0ca45b12ebd5`.
- Second host: `36940525b05fe0d627ae12964573755f08877d49`.

The private controller harness owns the generated acceptance artifacts under
`~/.local/state/hou1-failure-paths-20261124/`; `acceptance.json` binds reports,
harness hashes, cleanup and limits. The working repositories remain uncommitted.
No production services, grants, provider resources or global policies were
changed. Controller separation and independent controller recovery remain
separate work.
