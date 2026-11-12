# CMS storage and recovery

Forms and CMS share `<app-directory>/database/forms.db`, mounted at
`/var/data/forms.db`. `cms_media_directory` is mounted at `/var/data/media`.
Database/media roots and credentials are separate for test and production.
Customer submissions are never seeded.

## Current backups

With `hou1_backup_enabled: true`, the [unified protocol](hou4-backup-protocol.md)
captures SQLite/media with the other HOU1 components and disables the legacy CMS
backup cron. HOU4 retains seven daily and four weekly points. Use
[production replacement recovery](production-host-recovery.md) for a lost host.
A SQLite file without its matching media is not a complete CMS recovery point.

## Legacy helpers and Directus retirement

`cms-backup.sh` and `cms-restore.sh` remain for component-only legacy archives.
When unified backup is disabled, the old job retains local archives for 30 days;
that is not the current off-host retention policy. Restore only a reviewed,
compatible database/media pair, with writers stopped and current data preserved.

Legacy Directus retirement archives its database/media at the configured
`directus_retirement_archive` path before removing matching project containers.
Removal requires a healthy replacement and valid Nginx configuration. An existing
archive is reused only if contents still match; otherwise select a new path.
It does not contain the customer-submissions database. Retain it off-host through
the rollback window and restore it only for a deliberate Directus rollback.
