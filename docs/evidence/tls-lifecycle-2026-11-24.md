# Lab certificate rotation and reload-failure rollback — 2026-11-24

Corrected acceptance passed at HOU1 `77ec898` and HOU4 `3d0ae0f`, using the
existing controller and rebuilt lab guest. The original failed attempt and its
explicit cleanup remain separate from the corrected run.

## Results

- The maintained controller issued new, trusted certificates for the same three
  lab lineages and four served names into separate private encrypted state.
  Every candidate leaf fingerprint differed from the active original. No DNS
  credentials or controller age identities were installed on the guest.
- Read-only preflight bound the installed receiver to its source and verified the
  original served certificates. The drill held controller workflow admission and
  host capture admission, paused only lab renewal deployment, and armed a
  guest-local original-bundle recovery timer before certificate changes.
- A private executable wrapper rejected exactly one receiver reload subprocess.
  The installed receiver entry point returned failure, restored the
  original certificate content and metadata, and ran the real rollback reload.
  Original CA-validated served fingerprints and the controller success state
  matched the baseline. The wrapper affected only that receiver invocation.
- Normal receiver delivery then installed all changed certificates and verified
  trust, names and candidate leaf fingerprints. Restoring the original reviewed
  bundle passed the same live checks. Original content, permissions, ownership
  and served fingerprints matched exactly afterward.
- Receiver configuration was unchanged. All fourteen running container identities,
  the retained dormant container and both host boot/instance/SSH identities stayed
  stable. Access, Google policy, branding, user/VPN grants and secret hashes matched.
  Managed verification passed on both hosts. Temporary private material and the
  transient recovery timer were removed; lab renewal scheduling was restored.
- The ordinary scoped lab renewal service then passed with the original leaf
  fingerprints and fresh success receipts. All seven controller timers were active;
  eight monitoring targets were healthy and all twenty alert rules inactive.
- Production renewal launcher, executor configuration and sudoers hashes matched.
  No production deployment, controller relocation or host reboot was performed.

## Finding and correction

The first live attempt stopped because its intended reload failure did not occur.
Its wrapper was under `/run`, mounted `noexec`, so Docker resolved to the ordinary
executable. Cleanup restored the original valid certificate bytes and served
fingerprints, but the exact comparison exposed `0640` files becoming `0600`.
A guarded repair restored their original permissions and disarmed timed recovery.
The failed invocation and repair receipt were retained.

The receiver's atomic replacements always assigned mode `0600`, losing existing
consumer permissions on rotation and restoration. It now preserves existing file
mode, UID and GID, and restored symlink ownership. New files remain `0600`.
The regression fails on the old implementation and passes on the fix, including
an actual root-run fixture with different file/link owners. All eighteen TLS tests,
fifteen ingress tests, syntax and lint passed. The existing renewal-access playbook
installed the fix with one changed task and no access-grant changes.

The corrected helper used an executable private directory and proved harmless
Docker-command interception before any fault. That separate attempt passed both
the injected failure and successful rotation without a subsequent host repair.
This tests receiver rollback after a controlled reload-command failure, not an
unpredictable Nginx crash. Full controller replacement/reboot remains future work;
see [controller responsibilities](../controller-responsibilities.md).

Private evidence:
`/home/dev/.ssh/hou-lab/operational-acceptance/20261124-tls-lifecycle/`.
`invocation.json` is the failed live attempt; `recovery-after-failed-drill.json`
records cleanup. `attempt02/invocation.json` records corrected acceptance.
The independently issued candidate material remains encrypted on the controller.
