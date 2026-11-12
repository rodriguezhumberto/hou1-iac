# HOU1 recovery sets on HOU4

HOU1 prepares an owner-encrypted recovery set; HOU4 collects it over a restricted
VPN/SSH path. The authoritative point contains `recovery.age`, `manifest.json`
and `receipt.json`. It is not a container-image archive or a controller backup.

## Schedule, retention and limits

| Setting | Policy |
| --- | --- |
| HOU1 capture | 02:00 UTC |
| HOU4 collection | 03:00 UTC |
| Timers | Up to five minutes jitter; missed-run handling enabled |
| Retention | Newest point from seven UTC dates and four ISO weeks; tiers may share a point |
| Receiver limits | Default 60 GiB budget, 10 GiB maximum incoming archive, 15 GiB minimum free headroom |

One nightly point permits approximately a day of lost changes; a failed night
extends that window. Capacity must fit retained points plus one incoming set,
monitoring compaction, images and OS. 4 GiB RAM/100 GB disk is a starting size,
not a guarantee as media grows. HOU1 reserves operational headroom plus three
estimated snapshot sizes before capture.

HOU4 survives HOU1 loss, but not loss of both nodes. Storage is not immutable;
no extra object-storage copy is implemented. Independent HOU4 outage monitoring
remains deliberately deferred.

## Capture and trust boundaries

The point contains PostgreSQL logical SQL and Authentik files, NetBird state/keys,
staff SQLite/media, configuration/certificates, source revision and CI image
receipt. It also contains the declared host runtime age identity inside the
encrypted archive. It excludes the offline owner key, registry pull credential,
controller DNS/ACME state and container layers. Recover those dependencies separately.

Capture stops Authentik writers around SQL/file capture, staff around SQLite/media
capture and NetBird management around its state copy. PostgreSQL stays running;
live PGDATA is never copied. NetBird's integrated relay also stops temporarily,
so capture is a local systemd job independent of SSH. Ingress and the host VPN
client remain running. These are consistent component snapshots, not a distributed
transaction; schedule identity/policy edits outside the capture window.

Before each stop, a root-only fsynced `restart-intent.json` records the fixed
writer group. Normal cleanup, `ExecStopPost` and the enabled boot recovery service
restart interrupted writers. SIGKILL or power loss cannot run Python cleanup;
boot recovery handles recorded intent. Invalid intent or failed restart remains
for investigation. A failed attempt removes partial files and preserves the
previous `ready.export` and last-success timestamp.

HOU1 encrypts to `hou1_backup_recipient`; neither VPS receives the owner's private
key. HOU4's locally generated transport key cannot decrypt backups. Its source-bound
SSH grant permits only `export-v1`, with no shell, PTY or forwarding. The fixed
reader checks freshness (six hours), size and checksum without service mutations.
NetBird permits only the dedicated receiver peer TCP22 to the source peer.
Neither peer needs `administrators` membership.

HOU4 checks the pinned SSH identity/exit status, bounded manifest, environment,
age header, exact byte count and SHA256 before atomically committing a point.
Partial, stale, corrupt and replayed transfers fail. Retention runs only after a
successful commit and does not delete retained points merely to fit an incoming
archive. HOU1 has no HOU4 deletion credential. **Transfer integrity does not prove
decryption or service recoverability.**

## Pairing established nodes

Keep independent provider access to both nodes. HOU1's control plane must exist
before HOU4 VPN enrollment. Do not wipe an established HOU4 to update pairing.
The earlier native-to-container migration required fresh Debian and discarded
only test monitoring data; that was a one-time migration.

1. Configure HOU4's containerized receiver with `hou4_backup_enabled: true`, HOU1's
   VPN address and full pinned ED25519 public host-key line. Read
   `/var/lib/hou4-backup/transport_ed25519.pub`; its private key stays on HOU4.
2. Persist private HOU1 inputs: `hou1_backup_enabled: true`,
   `hou1_backup_receiver_ip`, `hou1_backup_transport_public_key`,
   `hou1_backup_recipient` and the exact `hou1_backup_app_project`.
   Supply the endpoint-matched encrypted NetBird token for policy reconciliation.
3. Apply the source role through the established VPN inventory:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/backups.yml \
     -e target_hosts=hou1_prod -e "@$INPUTS"
   ```

   Keep `hou1_backup_enabled` in future full deployments. It removes legacy
   Authentik/CMS cron jobs; old manual helpers remain for earlier archive formats.
   Reconcile monitoring with the same release inputs so it reads export status.
4. On HOU1 start `sudo systemctl start --no-block hou1-backup-prepare.service`.
   Reconnect if necessary and require successful status and fresh
   `/var/lib/hou1-backup/last-export.json`.
5. On HOU4 start `sudo systemctl start hou4-backup.service`. Require successful
   status, a new committed receipt, source timestamp, dashboard freshness and
   resolved backup alerts. Verify `hou1-backup-prepare.timer`,
   `hou1-backup-recover.service` and `hou4-backup.timer` are enabled.

Disabling either side stops its timer and preserves data. A failed run retries
on the next nightly schedule; after repair an operator may start it immediately.
Keep transport keys, host keys and receiver destinations in the recovery record;
a replaced HOU1 needs a reviewed host-key update.

## Interrupted capture or transfer

Inspect protected results for `hou1-backup-prepare.service` and
`hou1-backup-recover.service`. The fixed
`sudo /usr/local/libexec/hou1-backup-prepare --recover` replays recorded writer
intent under the capture lock; it does not create a new recovery point. Correct
invalid intent/restart failure before retrying; never delete intent or publish
partial files as a shortcut. Check HOU4 receipts for collection success rather
than relying on HOU1's local capture timestamp.

Verify that transport access cannot obtain a shell, forward ports, read arbitrary
files or impersonate another source. After access/policy changes, test public and
unrelated-peer denial as well as collection.

## Verify and restore

Copy a complete point through pinned operator SSH or independent provider access.
With the offline identity, authenticate/extract into protected empty storage:

```bash
python3 scripts/verify-recovery.py \
  --archive /private/point/recovery.age --manifest /private/point/manifest.json \
  --identity /private/offline.agekey --output /private/empty-recovery-directory \
  --environment production
```

The validator checks ciphertext, authenticated decryption, safe archive paths,
required components and SQLite integrity. It writes plaintext secrets; protect
and remove output afterward. It does not change services or prove a live restore.

Use the maintained [production replacement playbooks](production-host-recovery.md),
[clean test-host workflow](clean-host-recovery.md) or
[test-only isolated component drill](reproducible-recovery-drill.md) for execution.
Do not manually seed or bootstrap over restored databases. Revoke backed-up
sessions and reconcile all later user/role/device/token changes before publication.
Retain the matching source bundle, pinned images and separately recoverable GHCR
credential. Follow their explicit review/publication and owner-access gates.

## Evidence and open limits

[Capture/interruption evidence](evidence/hou1-backup-interruption-2026-11-08.json)
records writer SIGTERM/SIGKILL and reboot recovery. Receiver process-kill/retry
checks were local; a live interrupted HOU4 transfer and provider-level power cut
were not tested. [Original component restore](evidence/hou1-recovery-restore-2026-11-08.json),
[later clean test restore](evidence/hou1-ci-clean-host-recovery-2026-11-10-02.json),
[production recovery](evidence/production-host-recovery-2026-11-12.json) and
[owner decryption](evidence/owner-recovery-access-2026-11-12.json) are separate
results. The production run needed corrections; uninterrupted automation remains
[#78](https://github.com/rodriguezhumberto/hou1-iac/issues/78).
