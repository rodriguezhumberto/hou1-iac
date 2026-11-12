# Authentik backup and restore

## Current recovery path

With `hou1_backup_enabled: true`, the [unified backup protocol](hou4-backup-protocol.md)
replaces the standalone Authentik cron. HOU4 collects the owner-encrypted recovery
set with seven daily/four weekly retention. Use
[production replacement recovery](production-host-recovery.md) for a lost node or
the [test-only component drill](reproducible-recovery-drill.md) for isolated checks.

The authoritative database copy is a logical PostgreSQL dump. A live PGDATA
copy is unsafe and is not part of either backup implementation. Keep signing
keys, clients, configuration and media with the database, and reconcile later
revocations before publishing restored services.

## Legacy standalone archives

`backup.sh`, `restore.sh` and `restore-drill.sh` remain installed for older
component archives. Their cron runs only while unified backup is disabled.
Configured local/off-host directories and 30-day retention belong to that legacy
path, not the active HOU4 collection schedule.

The legacy archive contains SQL, media/templates/certs/config, Compose/runtime
files and SOPS ciphertext. Its job writes success only after authenticated
decryption/tar validation and off-host file installation; failure writes a marker.
It requires a matching decryption identity, unlike owner-only unified capture,
which encrypts on HOU1 without the offline owner's private key.

Only restore a selected verified archive into clean component storage with its
compatible revision and protected recovery identity. Never merge it into a live
database or count its success marker as proof of a unified recovery point.
Historical component-drill results do not prove replacement-controller recovery
or uninterrupted production automation.
