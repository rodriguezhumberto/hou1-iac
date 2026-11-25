# Fresh lab bootstrap — 2026-11-19

Scope: both isolated HOU1/HOU4 VMs rebuilt from Debian 12 cloud images.
Production was not targeted. HOU1: `0bfc7b5`; HOU4: `6acf783`.
The trusted application image/source selection stayed at `463a28b`.
Frozen inputs, provider bindings, logs and receipts remain private under
`/home/dev/.ssh/hou-lab/rebuilds/20261119T012432Z-attempt02/`.

## Attempts and results

The first preparation attempt failed because Docker's declared resolver was not
running yet. HOU1 preparation now starts opted-in lab DNS before Docker. The
second attempt started from another clean OS disk, with no application or Docker
state; it did not reuse the repaired first host.

| Check | Result |
| --- | --- |
| HOU1 fresh preparation and bootstrap | Passed through maintained playbooks, using encrypted certificate/geolocation seeds and the frozen release. Fresh identity/VPN state was created. |
| Controller enrollment and private SSH handoff | Passed with a new isolated controller peer and fresh pinned host keys. |
| HOU1 managed deployment and ingress cutover | Passed; private access, application readiness, source cleanliness and TLS verified. |
| Explicit test identity seed | Passed; reader login, protected read, write denial and logout revocation passed. Repeat changed nothing. |
| HOU4 fresh preparation and bootstrap | Passed, including resolver setup, provider initialization wait and monitoring startup. |
| Two-host unchanged replay | Passed with identical input/configuration hashes and all twelve container IDs stable. Source backup playbook repeated with zero changes. |
| Fresh and timer-generated backup points | Capture, restricted collection, authenticated offline decryption and SQLite integrity passed. Plaintext was removed. |
| Backup scheduling and monitoring | Both timers enabled; no partial capture/transfer; arbitrary export commands denied; success/freshness metrics matched and both backup alerts were inactive. |
| Automated failure matrix | Application repair, monitoring repair and HOU4 operation during a HOU1 identity outage passed, followed by final two-host verification. The HOU4 advisory playbook changed nothing. |
| HOU4 reboot | Private SSH returned in 56.1 seconds; four containers were ready in 56.5 seconds, without service repair. |
| HOU1 reboot | Private SSH returned in 41.2 seconds; eight active containers were ready in 63.8 seconds, without service repair. |
| Post-reboot acceptance | Two-host readiness/source/TLS verification, reader login/read/write-denial/logout and backup timer/transport/monitoring checks passed. |

The retained pre-rebuild recovery point remains intact on the controller. The
offline recovery identity was not installed on either VM.

## Coordination failures

- A handoff invocation encountered the workflow lock and made no host changes.
  The serialized retry passed; both receipts were retained.
- Scheduled backup capture overlapped the first managed replay and briefly
  stopped the VPN during source transfer. Deployment aborted; capture completed
  and VPN access recovered automatically. An unchanged-input retry passed without
  file or service repair. Capture and deployment still need shared exclusion;
  this run does not establish safe deployment at arbitrary times.

## Validation and follow-up

HOU1's five affected modules passed 61 tests, plus syntax/lint. HOU4's full check
passed 100 tests without skips, plus syntax/lint. Bootstrap transport also passed
a real namespace smoke check.

CPU/RAM, disk capacities, NICs, bridge and provider firewall rules were preserved.
Replacement disks, cloud-init references and HOU4 guest-agent enablement must be
reflected in the separate provisioning project before its next Terraform apply.
Prior disks remain detached for rollback; their later removal needs a deliberate
retention decision because they consume storage.

This was a fresh install with declared seeds and explicit enrollment, not a
database restore or a single-command unattended build. New ACME issuance, Google
callback login, live transfer interruption, clean-host database restore and peer
replacement remain outside this acceptance run.
