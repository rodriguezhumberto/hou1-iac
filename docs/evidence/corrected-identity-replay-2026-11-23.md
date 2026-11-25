# Corrected fresh-host identity recovery — 2026-11-23

Attempt `clean-20261123-160454` restored fresh VM225 once, then completed
publication and finalization with corrections. This is a corrected recovery,
not an uninterrupted replay. Functional finalization passed at HOU1 `5271719`
and HOU4 `3d0ae0f`; the later renewal comparison fix has fixture coverage only.

## Results

- Independent provisioning evidence and strict SSH verified the new host key,
  fresh volume, Debian installation and unchanged provider isolation policy.
  Managed input checks included the authoritative backup receiver source-key pin.
- Cold restore passed its first invocation: 187 PostgreSQL tables and 140.505
  seconds for the restore operation. Identity bootstrap and CMS seeding did not run.
- Sealed checks recovered credentials, signing keys, the approved base admin,
  Google S256 configuration and custom branding. Reader login/logout, denied
  writes, verified TLS and loopback-only publication bindings passed.
- Publication resumed from its successful ingress checkpoint; HOU4 reconnected
  without a reset. Existing monitoring, backup and renewal grants were retained
  while binding the replacement peer.
- Corrected finalization collected point `20261123T174334Z-307a501db3a5` on HOU4
  and passed offline decryption and integrity checks. All fourteen container IDs,
  both boot/instance identities and both SSH keys survived reconciliation.
  Google policy, branding, user-grant and runtime-secret hashes were unchanged.
- Source monitoring and renewal access passed their owning playbooks. The
  dedicated renewal peer passed configuration and status delivery checks.

## Corrections and limits

The first publication invocation correctly refused a private input that omitted
monitoring despite existing managed groups. Its failure was preserved; a separate
supported resume required those groups. An omitted optional renewal handoff was
repaired separately, using frozen pre-deletion ownership evidence and the existing
recovery tasks' exact policy, source and membership guards. The original handoff
receipt was not rewritten. Monitoring's first collection also stopped until the
fresh TLS status receiver was configured and populated; the corrected order passed.

First finalization stopped when retention removed an enumerated backup directory.
The collector committed successfully. The observer now tolerates a wholly removed
point while still rejecting incomplete retained files, corruption and symlinks.
A separate fixture reproduced NetBird changing a renewal policy's displayed peer
count during replacement. Its fix compares semantic grants and pinned identities,
preserving historical receipt bodies and rejection of intervening access changes.

Earlier failures and corrected runs have separate private receipts. Production
and VM223 were untouched by controller actions. No new reboot, personal-client
reconnection or external human Google login was performed. Another fresh-host
run is needed to establish uninterrupted recovery with the final inputs and fixes.

Private evidence: `/home/dev/.ssh/hou-lab/handoff-preparation/20261123T160454Z/`.
