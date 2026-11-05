# CMS storage and backups

Forms and the custom CMS share `<app-directory>/database/forms.db`, mounted at
`/var/data/forms.db`. Media is stored separately at
`/var/lib/pearlandpaws/<test|production>/cms/media` and mounted at
`/var/data/media`. Test and production have separate databases, media, and
credentials. Customer submissions are never seeded.

Nightly `cms-backup.sh` archives the SQLite database and media under
`/var/backups/pearlandpaws/<environment>/cms` and retains 30 days.

```sh
<app-directory>/cms-restore.sh /var/backups/pearlandpaws/test/cms/cms-<id>.tar.gz
<app-directory>/cms-restore.sh /var/backups/pearlandpaws/test/cms/cms-<id>.tar.gz --apply
```

During the first deployment after cutover, legacy Directus data is archived at
`/var/backups/pearlandpaws/<environment>/directus-retirement/directus-legacy.tar.gz`
before its container is removed. Keep that archive off-host until the rollback
window expires. It contains only the old Directus database and media; the
customer database remains in the CMS backup.

Retirement requires an app stack without Directus. It stops only containers
matching the Compose project and working directory, checkpoints SQLite, and
verifies the archive before removal. Removal waits for a healthy replacement
stack and successful Nginx validation. Failures leave containers/data available
for recovery. Existing archives are reused only when their contents match;
if legacy content changed, select a new `directus_retirement_archive` path.

Restore the application and infrastructure at the paired release tag to roll
back. Restore the legacy archive only when deliberately restoring Directus.
