# HOU1 recovery sets on HOU4

This implements the off-host collection needed for the recovery rehearsal in
#27. It does not establish production readiness until a fresh-node restore has
passed. Existing production data is never used as a test seed.

The [live HOU4 rehearsal](evidence/hou4-backup-live-2026-11-08.json) collected a
32.4 MB encrypted point, verified transfer integrity, and passed Discord recovery
and HOU4 reboot checks. The [interruption rehearsal](evidence/hou1-backup-interruption-2026-11-08.json)
then passed live writer SIGTERM/SIGKILL recovery and durable-intent recovery
after reboot, preserving the completed ciphertext. The receiver also passed
local process-kill/retry checks during transfer and before commit. Actual offline
decryption, a clean full-stack restore, and a live interrupted HOU4 transfer
remain open gates. The reboot used a hard-killed capture followed by normal OS
reboot; no provider-level power cut was tested.

HOU1 prepares one encrypted recovery point locally at 02:00 UTC. HOU4 collects
the completed file at 03:00 UTC; both timers use up to five minutes of jitter
and persistent missed-run handling. HOU4 retains the newest point from each of seven distinct UTC dates
and four distinct ISO weeks; a point can satisfy both tiers. There is one copy
of each retained point, not daily/weekly duplicates. Nightly capture permits
up to approximately a day of lost changes; an unsuccessful night increases that
window and raises an alert.

## Trust and contents

HOU1 briefly stops Authentik server/worker writers, uses `pg_dump` against the
running PostgreSQL service and copies Authentik files. It separately stops the
staff application while SQLite's backup API and the media copy run, then stops
NetBird management while copying its state. Compression runs after NetBird
restarts. Its integrated relay also stops during capture, so preparation runs
as a local systemd job, independently of SSH. An SSH transfer never stops
NetBird. Each stop has a restart in a
`finally` block, including failed stops and snapshot errors. Before stopping a
writer, preparation fsyncs a root-only `restart-intent.json` containing only its
fixed component name. `ExecStopPost` runs the same helper with `--recover` after
termination, including SIGKILL. The enabled `hou1-backup-recover.service` replays
an outstanding intent after Docker starts at boot, before another preparation.
Recovery selects service names and project paths from fixed code/root-owned
configuration, verifies they are running, reports the interrupted attempt as
failed, and removes partial plaintext and ciphertext. It never replaces the
previous `ready.export` and preserves the last successful capture timestamp.
An invalid intent or failed restart leaves the intent for investigation.

Ingress and the host NetBird peer remain running during capture. A process
killed with SIGKILL or a host power loss cannot execute Python cleanup; durable
intent, service cleanup and boot recovery cover those interruption boundaries.
These are consistent component snapshots
within one recovery set, not a distributed database transaction. Schedule
operator identity/policy changes outside the capture window.

The set contains:

- Authentik logical SQL, media/templates/certs/config, Compose, runtime env,
  OIDC client env and SOPS ciphertext. Live PGDATA is never copied.
- NetBird Compose, configuration, dashboard env, keys and account/database state.
- Staff forms/CMS SQLite and media, runtime Compose/.env and certificate lineage.
- The immutable staff Git revision and the declared host runtime age identity,
  itself inside the encrypted archive, for clean-host secret recovery.

HOU1 encrypts with the **offline operator age recipient**. Supply the public
recipient through `hou1_backup_recipient`; neither VPS receives its private
identity. HOU4 uses its own locally generated SSH transport identity, which
cannot decrypt backups and has no general administrative access. Its public
key is restricted to HOU4's VPN address, an argument-free forced command, no
PTY/forwarding, and an exact sudo helper. NetBird grants only that enrolled
receiver peer TCP22 to the enrolled source peer. It does not add HOU4 to the
administrators group or allow staff application access.

The source atomically replaces one root-only `ready.export` ciphertext file;
a failed preparation preserves the previous completed point. The fixed SSH
reader checks freshness (six hours), size and checksum before sending data and
performs no service mutations. The source records local preparation status for
the existing HOU1 collector and synthetic freshness check. That status proves local capture,
not off-host retention; HOU4 receipts and alerts remain authoritative.

The helper accepts only `export-v1`. A bounded JSON manifest followed by exactly
`size` ciphertext bytes makes partial, wrong-environment, stale, oversized,
corrupt and replayed transfers fail. HOU4 checks the pinned SSH server identity,
SSH exit status, age file header, byte count and SHA256 before atomically
committing a private directory with manifest and receipt. **A verified transfer
is not proof of decryption or a successful application restore.** The offline
operator drill supplies those checks.

HOU1 has no HOU4 credentials or deletion API. Retention runs on HOU4 only after
a successful commit. The default receiver budget is 60 GiB, maximum incoming
archive 10 GiB, with at least 15 GiB of filesystem headroom. It refuses collection
rather than deleting retained points to fit a new archive. Budget for the
retained data **plus one incoming set**, Prometheus compaction, container images
and the OS. A 100 GB HOU4 disk/4 GiB RAM is the suggested starting size, not a
promise of capacity for arbitrary media growth. Measure the actual set size and
adjust limits before production. HOU1 also reserves operational headroom plus
three estimated snapshot sizes before starting.

Backups on HOU4 survive loss of HOU1 but not loss of both nodes. HOU4 is not an
immutable storage destination. An additional provider/object-storage copy can
be added later; it is not part of this protocol. Independent HOU4 outage
monitoring remains deferred.

## Pairing and deployment

Keep encrypted deployment inputs in SOPS/age bundles and decrypt them only in a
private temporary directory for Ansible. Never put webhook URLs, private keys,
runtime credentials or allowlists in inventory or shell arguments. The backup
recipient, public transport key and server public host key are public inputs.

1. Reinstall HOU4 with fresh Debian; do not run the container playbook over the
   native test installation. Preserve its encrypted Grafana bootstrap password,
   Discord destination, TLS state and controller renewal configuration. Old
   Prometheus/Grafana/Alertmanager test data is intentionally discarded.
2. Deploy HOU4's container playbook and configure its backup receiver with
   `hou4_backup_enabled: true`, HOU1 VPN IP and the verified full ED25519 host
   public-key line. Read only `/var/lib/hou4-backup/transport_ed25519.pub` from
   HOU4 for the source pairing. The private transport key stays on HOU4.
3. Persist the source selection in the appropriate private environment vars:
   `hou1_backup_enabled: true`, `hou1_backup_receiver_ip`,
   `hou1_backup_transport_public_key`, `hou1_backup_recipient`, and
   `hou1_backup_app_project`. Confirm the exact app Compose project name;
   the defaults are `pearlandpaws-test` / `pearlandpaws`. The source role derives
   the Authentik and NetBird projects from their existing environment inputs.
4. Run the source role against the **established VPN inventory**, supplying the
   existing endpoint-matched encrypted NetBird API token for policy reconciliation:

   ```bash
   cd /home/dev/hou1-iac/ansible
   ansible-playbook -i inventory/inventory.yml playbooks/backups.yml \
     -e target_hosts=YOUR_ESTABLISHED_HOST -e @/private/backup-inputs.yml
   ```

   The role removes the legacy Authentik and plaintext CMS cron schedules.
   Keep `hou1_backup_enabled` in future full-deployment inputs so they are not
   recreated. Old manual backup/restore commands are retained for earlier
   archive formats; they are not used by this transport.
   Refresh the existing HOU1 monitoring and rehearsal-gate roles from the same
   revision so their local checks read the new export status. Preserve their
   existing private deployment inputs.
5. On HOU1 start `sudo systemctl start --no-block hou1-backup-prepare.service`.
   Its NetBird restart can briefly disconnect relay-based SSH/VPN sessions;
   reconnect and check its result and `/var/lib/hou1-backup/last-export.json`.
   Confirm `hou1-backup-prepare.timer` is enabled. Then on HOU4 start
   `sudo systemctl start hou4-backup.service` and confirm its result,
   committed receipt, source timestamp, off-host backup dashboard panels, and
   resolved backup-freshness alert. No backup succeeds until pairing is complete.
   The receiver timer is `hou4-backup.timer`; disabling the receiver stops the
   timer and retains all data. Disabling HOU1 preparation stops its timer and
   preserves its completed ciphertext. A failed run retries on the next nightly schedule;
   after correcting the fault, an operator can start the service immediately.

For an interrupted capture, inspect the result of `hou1-backup-prepare.service`
and `hou1-backup-recover.service` privately. The latter must be enabled at boot.
`sudo /usr/local/libexec/hou1-backup-prepare --recover` resumes only a recorded
writer group and clears partial files under the same preparation lock; it
does not create or acknowledge a new recovery point. Correct an invalid intent
or failed restart before retrying preparation. Do not delete the intent or
publish a partial archive as a manual shortcut.

The transport uses TCP22 on HOU1. Review the exact NetBird policy readback and
prove that the backup identity cannot start a shell, forward a port, read an
arbitrary file, alter retained data on HOU4, or impersonate another VPN source.
Keep provider-console access to both nodes; recovering the NetBird control plane
must not depend on that same control plane being available.

## Offline validation and clean-host restore

From HOU4's provider console or an independent operator access path, copy one
complete directory (`recovery.age`, `manifest.json`, `receipt.json`) to an operator
machine. A recovery receipt alone is insufficient. With the offline age identity:

```bash
python3 scripts/verify-recovery.py \
  --archive /private/point/recovery.age \
  --manifest /private/point/manifest.json \
  --identity /private/offline.agekey \
  --output /private/empty-recovery-directory --environment test
```

The validator verifies ciphertext checksum, decrypts with age authentication,
rejects archive traversal/devices/unsafe links, checks SQLite integrity and
required recovery components, and extracts into a private empty output directory.
It does not change live services. Output contains plaintext secrets; remove it
when the drill is complete. Never point `--output` at an application directory.

Restore on a clean **isolated** HOU1 with matching IaC/app refs and pinned images:

1. Use provider-console access and the separately held operator credentials.
   Recover the runtime age identity from `host-recovery/<index>` to the matching
   path in `recovery.json`; keep it root-only. Reconstruct deployment inputs from
   the operator's SOPS handoff. Controller DNS/renewal secrets are recovered from
   the separate controller handoff, not from either VPS.
2. Prepare the saved Authentik Compose/env and its persistent directories from
   `authentik-files`, with the UIDs in the existing Authentik role. Start only
   PostgreSQL in a new empty data directory. Import
   `authentik/postgresql.sql` using `psql -v ON_ERROR_STOP=1 -U authentik -d authentik`
   before starting Authentik server/worker. Never copy live PGDATA or apply the
   SQL into an existing production database. Do not re-bootstrap identity policy
   over a restored database.
3. Install `netbird-files` as the matching NetBird project root and reapply its
   isolated ingress network and firewall before starting it. Retain the restored
   configuration, database and key material. Use provider console until its
   control plane and private peer path recover. No NetBird upgrade is implied.
4. Check out the saved staff revision, reinstall `app-config` and restore the
   `cms/database/forms.db` plus `cms/media` pair to the paths in the matching
   inventory. Apply the storage UID/modes from the webstack role before starting
   `astro-app`. Restore the saved certificate symlinks/lineage together. Do not
   manually seed the database over restored data.
5. Run discovery/JWKS and Google sign-in, approved role and VPN access, revocation,
   staff content/media and form-data checks, public-denial tests, metrics/Discord
   delivery, renewal, reboot and NetBird rollback gates. Record duration and
   data recovered. Test corrupt/wrong-key archives, interrupted export, and
   restoration when HOU1's VPN control plane is unavailable.

Local tests exercise encrypted round trips, a real PostgreSQL logical import
into a new database, SQLite/media recovery, failure restart, retention, corruption
and transport policy guards. The full two-node restore and outage drill remains
a live rehearsal gate; #27 must stay open until it passes.

[Recorded local evidence](evidence/hou4-backup-protocol-2026-11-08.json) separates
completed tests from live gates. HOU4 receiver interruption coverage is recorded at commit `96f80c0`.
