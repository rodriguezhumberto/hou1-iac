# Quarantined fresh-VM recovery — 2026-11-19

VM 225 restored the verified test point on fresh Debian 12, alongside the existing
lab. It has 4 vCPU, 8 GiB RAM and a 40 GiB disk. Cold restore revision: `9a02dea`;
readiness verification follow-up: `c519c2a`. The selected application stayed at
`463a28b`. Production was not targeted.

This accepts an unpublished recovery copy. Peer replacement, public listener
handoff and end-to-end HOU4 TCP/UDP connectivity were not attempted. HOU4's
firewall intentionally remains unchanged; its exception for the new VM is deferred.

| Check | Result |
| --- | --- |
| Fresh target and access | Debian 12, expected MAC, empty Docker/application state and pinned SSH/sudo passed. User explicitly authorized first-use host-key trust and confirmed independent recovery access. |
| Frozen recovery inputs | Authenticated point, standalone source bundle and seven immutable image selections prepared before restore. The offline recovery identity stayed on the controller. |
| Cold restore | First host attempt passed through `restore-test-host.yml`; 187 PostgreSQL tables imported. Restore phase took 145.5 seconds, excluding OS preparation. |
| Session handling | IdP sessions/tokens and staff sessions revoked before restored writers started; no identity bootstrap, credential reset or CMS seed ran. |
| Isolation | All seven containers run on Docker-internal networks; published-port declarations specify loopback. Quarantined requests cannot publish through the recovery driver. |
| Recovered credentials | Reader password hash, emergency-admin password, both OAuth client secrets and signing private keys verified. |
| Local HTTPS | TLS validation, application readiness and expected staff/admin/setup denials passed through the private bridge. |
| Reader OIDC | Login, protected read, write denial and logout revocation passed from a temporary internal test client; the client/network were removed. |
| NetBird state | Peer IDs, keys, addresses, user references, last-seen fields, groups and policies match the point. Copied peers remain disconnected. |
| Reboot | SSH returned in 32.3 seconds; all seven services ready in 63.0 seconds without repair. Quarantine and OIDC checks passed again. |
| Cleanup/headroom | Controller plaintext, transferred payload/bundle, temporary registry credentials and probe resources removed. Replacement had 31 GiB free disk and 6.3 GiB available RAM afterward. |

## Corrections and limits

- Controller preparation initially rejected the current host runtime-key path and
  saved independent-ingress override. Compatibility fixes preceded the fresh-host
  invocation. Recovery renders the integrated cold listener without editing the
  archived configuration or source bundle.
- The first HTTP observer expected loopback forwarding on Docker-internal
  networks and failed. A bridge-address observer passed without service changes.
  The temporary OIDC client used an internal test-overlay subnet to exercise the
  saved access rules; it was not a real NetBird enrollment or transport test.
- All 66 affected local tests passed without skips, plus syntax/lint. Quarantine
  checks reject stopped/unhealthy services, external/shared networks and public
  bindings. Existing production recovery fixtures passed too.
- Final verification of the original lab stopped at the controller transport:
  its VPN reported disconnected, and both original LAN addresses were unreachable.
  No original-host or firewall changes were made; their current health needs an
  independent power/network check.

Private inputs, failed observations and receipts are retained under
`/home/dev/.ssh/hou-lab/replacement-recovery/20261119T064742Z/`.
