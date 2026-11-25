# Original lab recovery after hypervisor OOM — 2026-11-19

The user confirmed VM 223 was killed by hypervisor memory pressure. VM 225 was
shut down and VM 223 restarted. The original boot disk was retained. Production
was not targeted; the recovery copy stayed off and HOU4's firewall was unchanged.

The controller VPN reconnected automatically. Strict SSH checking then rejected
HOU1's changed host key. The provisioning agent verified the new fingerprint
through Proxmox's guest agent and traced regeneration to a changed cloud-init
instance ID after Terraform rewrote equivalent user-data YAML. It reported key
generation at 01:22:17 CST. This was not a boot-disk replacement.

## Verified recovery

- The presented key matched the independently verified fingerprint. New private
  trust/inventory snapshots replaced the active references; old files were preserved.
- Both hosts passed the maintained readiness/source/TLS checks. All eight HOU1
  and four HOU4 container IDs matched the earlier accepted lab.
- HOU4's public backup role changed only its source host-key pin. No transport
  identity, firewall, service data or retention settings changed.
- Backup timers, metrics, restricted-command denials and inactive backup alerts
  passed. A full encrypted export verified through the new pin, using temporary
  ciphertext staging. Retained points and metrics stayed unchanged; no new source
  capture was requested.
- Reader login, protected read, write denial and logout revocation passed.

Private receipts: `/home/dev/.ssh/hou-lab/host-key-rotation/20261119T075021Z/`.

## Provisioning follow-up

Configure explicit stable cloud-init metadata in the separate Terraform project
before further user-data edits. Preserve the currently effective instance ID on
first adoption; deriving identity from YAML content or formatting would repeat
the problem. The current ID and user-data hash were recorded privately for that
migration. Hypervisor memory capacity remains a separate provisioning concern.
