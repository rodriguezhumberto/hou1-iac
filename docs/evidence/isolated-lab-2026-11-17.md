# Isolated lab deployment — 2026-11-17

HOU1: `hou1-test`, LAN `192.168.0.151`, VPN `100.91.0.10`.
HOU4: `hou4-test`, LAN `192.168.0.152`, VPN `100.91.0.21`.
Namespace: retained in private lab inputs; separate NetBird account: `100.91.0.0/16`.
Private inputs, inventories, run logs and container snapshots: `/home/dev/.ssh/hou-lab/`.

Infrastructure: HOU1 `35b6cad` (TLS repair), `f553c61` (boot retry); HOU4 `a985105`.
Application revision: `9facf295fb28987a4f1b2cc6b20a02eb031fe4af`.
Published image digest: `sha256:4037850b0170bbcb23eb693c5d13b8dc024569b4f29beb24dd1087b15c4a53a4`.

| Check | Result |
| --- | --- |
| HOU1 full-stack bootstrap and managed deployment | Passed |
| HOU4 monitoring deployment and three HTTPS probes | Passed |
| Fresh pinned private SSH/sudo; VPN-only SSH listeners | Passed on both hosts |
| Unchanged reconciliation, including after reboot | All eight HOU1 and four HOU4 container IDs stable |
| Add/remove Nginx label | Only Nginx recreated |
| Change/restore Prometheus retention | Only Prometheus recreated |
| Stopped application / node exporter repair | Recovered; other container IDs stable |
| Internal reader OIDC | Login, read, write denial and logout revocation passed |
| Component backups | Authentik encrypted archive self-check and CMS snapshot passed |
| Intentional shared-ingress stop | Required independent console recovery; private access restored |
| Tagged webstack repair after recovery | Passed; Uptime Kuma repaired, all eight IDs preserved; final TLS verification passed |
| HOU1 recovery boot | Failed full recovery: Uptime Kuma could not bind its VPN address |
| HOU4 controlled reboot | Passed without repair; private SSH 57.1s, four containers ready 57.5s; three probes passed |
| HOU1 controlled reboot after fix | Passed without repair; private SSH 46.1s, eight containers ready 62.8s; all IDs preserved |

## Fixes and setup decisions

- Create `certbot/www` before fingerprinting its read-only mount on a fresh host.
- Permit identical certificate inputs during explicit bootstrap retries. For
  managed application repair, verify unchanged certificates after activation;
  retain validation before rotation and rollback on failure.
- Use a separate controller VPN namespace and SSH-only lab policy. Existing
  production peers, controller routes and production renewal configuration stayed intact.
- The local provisioning agent corrected Proxmox egress: HOU4 to HOU1 TCP 443,
  plus UDP 3478/51820/51821 between the test VMs. Bootstrap DNS runs locally on
  each VM; Technitium was not changed.
- Lab role claims use the published application's canonical group names in its
  independent IdP. No production users or VPN grants were imported.
- Legacy Authentik backups use the host age identity for their self-check; an
  owner-encrypted host-key envelope is retained for recovery.

## Recovery findings

The ingress-stop drill had no automatic restart timer. The user confirmed local
recovery, and fresh pinned private SSH/sudo subsequently passed on both hosts.
The failed drill remains recorded: shared ingress also carries the lab VPN control
plane. Future ingress drills need a VM-local restart timer and independent console
access.

HOU1 had also rebooted during recovery. Docker attempted to start Uptime Kuma
before `100.91.0.10` existed, failed to bind port 3001 and left the container
exited despite `restart: always`. Tagged webstack reconciliation repaired it
without changing any container IDs; installed/served TLS verification and the
reader login probe passed. This establishes repair, not autonomous boot recovery.

Local commit `f553c61` adds a retryable application startup unit. The existing
Compose helper checks the exact address/interface before any mutation; systemd
retries every ten seconds. Docker starts ingress independently so NetBird can
provide the address. The unit has no stop action, preserving shared ingress when
an operator stops the startup unit. A controlled reboot passed without repair;
the startup unit retried once and succeeded. All eight container IDs were
preserved, and the post-boot reader login and three HOU4 probes passed.
A subsequent tagged webstack reconciliation preserved those eight IDs and passed
final TLS verification again.

HOU4 also passed its controlled reboot without repair. Its existing startup unit
uses Compose `down` at shutdown, so container IDs changed across reboot. The
unchanged-reconciliation checks above concern stable deployments between boots.

Initial local `make check`: 397 tests passed without skips. The subsequent TLS
ordering/tag changes passed 17 focused tests plus syntax/lint; Compose's
11-test module also passed. The initial full run preceded the final TLS changes.

The boot retry change passed syntax/lint and all 14 Compose tests without skips,
including native systemd validation and the disposable Docker fixture.

Google login awaits callback registration verification. Unified off-host backup,
clean-host restore and peer replacement were not exercised.

## Managed workflow acceptance

Ordered reconciliation passed with HOU1 `f6d349a` and HOU4 `782771f`.
Standalone verification passed with HOU1 `d57c00a`, which adds safe private-YAML
error handling. Both phases used private frozen inputs and the maintained
`scripts/lab-network.py` transport. No corrective commands were needed during
the successful reconciliation.

- Both hosts passed fresh pinned SSH/sudo, hostname, overlay and targeting checks.
- All eight HOU1 and four HOU4 containers were ready. Application image/source
  and TLS verification passed; all three declared HTTPS status checks passed.
- Standalone verification preserved the container IDs from reconciliation.
- Live transport checks confirmed operator identity, dropped capabilities,
  separate network/mount namespaces and unchanged controller resolver/routes
  (route comparisons exclude expiring lifetime counters).

The first reconciliation failed before host changes: global SSH overrides also
affected delegated controller tasks. Host-scoped transport inputs fixed it;
the failed receipt remains beside the successful retry under `workflow-runs/`.
Local validation passed 23 workflow/transport tests, syntax/lint and documentation
links. Fresh-bootstrap DNS/firewall ownership and input contracts precede ingress
separation in the [intervention audit](../deployment-interventions.md).
