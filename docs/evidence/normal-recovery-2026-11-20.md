# Same-address normal recovery — 2026-11-20

VM225 restored the selected test point on fresh Debian 12 at the original HOU1
LAN address. The provisioning agent independently verified VM223 stopped,
NIC-fenced and excluded from autostart, preserving its original and rollback
disks. HOU4 stayed unchanged; its guest-agent recovery path is available.
Production was not targeted. Publication and peer replacement remain pending.

Cold restore revision: `af5d92c`; DNS ordering correction: `db0b8ae`.
Application revision: `463a28b`. Point: `20261119T231622Z-d296266bd865`.
Recovery ID: `handoff-20261119-231455`.

## Results

| Check | Result |
| --- | --- |
| Rebuilt identity | Presented SSH key matched the independently supplied fingerprint. MAC, LAN address, Debian version, instance ID and sudo matched. Bootstrap and later private SSH use the replacement pin; old snapshots remain intact. |
| Target preparation | Empty application destinations/Docker state, 5–6 GiB memory configuration, persistent 2 GiB swap, disk headroom and bootstrap DNS/egress passed. Existing opt-in lab DNS inputs were selected before Docker preparation. |
| Provider boundary | Normal restore used the independently verified applied-policy digest bound to the target LAN address/MAC. The controller did not alter Proxmox rules or HOU4's deferred distinct-address exception. |
| Cold restore | First invocation passed: 187 PostgreSQL tables imported; restore phase took 148.4 seconds, excluding preparation. No identity bootstrap or application seed ran. |
| Session handling | Restored identity/application sessions revoked and stale VPN connection flags cleared before writers started. |
| Credential checks | Reader password hash, emergency-admin password, both OAuth client secrets and signing private keys passed. |
| Continued-host checks | After the DNS correction below, TLS/readiness, staff/admin/setup denials, reader login, protected read, write denial and logout revocation passed. |
| Sealed state | Seven services running; published-port bindings remain loopback-only. No publication checkpoint, real VPN enrollment or connected copied peers. Saved HOU1 peer identity matches the point. |
| Cleanup | Remote payload/source staging, temporary registry credentials and temporary probe client/network removed. Controller verification plaintext removed. |

## DNS correction and acceptance limits

The first login probe timed out after credential/TLS checks. A temporary internal
bridge alone did not fix it. Application-container DNS also timed out: cold
recovery reset UFW and created new bridges after preparation's DNS allowances.
The maintained `lab-dns.yml` playbook repaired one owned allowance. The follow-up
probe passed using a temporary internal bridge for issuer access while real
listeners remained on loopback. Probe attachments were removed afterward.

Cold recovery now creates only its seven selected services without starting or
pulling, resolves their actual network names and reconciles opted-in lab DNS
before starting writers. Production, quarantine and unmanaged DNS paths retain
their behavior. Syntax/lint and 51 affected tests passed without skips; three
focused ordering tests also passed after tightening the startup-failure assertion.

The successful original restore, failed probes and continued-host repair have
separate private receipts. This run does not establish an uninterrupted cold
restore with the corrected ordering; that needs another empty-host attempt.
Real controller/HOU4 VPN transport, publication, off-host backup from the
replacement and reboot acceptance remain later checks.

The user confirmed no access or credential changes between capture and fencing.
Post-capture source writes were observed and remain excluded from the selected
point. Keep this assertion and the later publication review separate from
synthetic login/session activity introduced by these probes.

Private inputs and receipts:
`/home/dev/.ssh/hou-lab/handoff-preparation/20261119T231455Z/`.
