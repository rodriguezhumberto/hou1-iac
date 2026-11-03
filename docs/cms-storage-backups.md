# CMS storage and backups

Test and production use separate `cms_environment` values, host storage paths,
`.env` files, SQLite databases, and media directories. The generated Compose
override mounts Astro's replacement CMS at `/var/data`; Directus keeps its
legacy `database` and `directus-data/uploads` mounts until the migration is
verified.

The webstack role creates a root-only backup directory and runs
`cms-backup.sh` nightly. Each archive contains an online SQLite backup of every
`*.db` file and a copy of `media/`. Archives are mode `0600` and are retained
for `cms_backup_retention_days` (30 by default). The script checks SQLite
integrity before publishing an archive, so a partial backup is never renamed
into the retention set.

To verify a backup without changing the running stack:

```sh
tar -tzf /var/backups/pearlandpaws/test/cms/cms-<UTC>.tar.gz
mkdir -p /tmp/cms-restore
tar -xzf /var/backups/pearlandpaws/test/cms/cms-<UTC>.tar.gz -C /tmp/cms-restore
sqlite3 /tmp/cms-restore/database/forms.db 'PRAGMA integrity_check;'
```

The deploy also installs `cms-restore.sh`. It validates the archive and all
SQLite files by default; pass `--apply` only after checking the archive name:

```sh
<app-directory>/cms-restore.sh /var/backups/pearlandpaws/test/cms/cms-<UTC>.tar.gz
<app-directory>/cms-restore.sh /var/backups/pearlandpaws/test/cms/cms-<UTC>.tar.gz --apply
```

The apply step snapshots the current CMS first, stops only Astro, replaces its
`database/` and `media/`, and starts Astro again. Directus remains running and
its database/uploads are not part of this procedure. Run the same commands with
`prod`/`production` paths only when the target environment is explicitly
intended.
