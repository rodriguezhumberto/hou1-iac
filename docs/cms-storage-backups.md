# CMS storage and backups

Forms and CMS share the existing `<app-directory>/database/forms.db` mount at
`/var/data/forms.db`. Deployment never copies or relocates this live WAL database.
Directus keeps its separate `directus.db` and uploads. Media is mounted separately
from `/var/lib/pearlandpaws/<test|production>/cms/media` at `/var/data/media`.

The managed Compose override preserves NetBird ingress and the media mount in
either deployment order. Operator-owned overrides require a manual merge.
The app's pinned Node image uses UID 100/GID 102; update storage ownership if
the image user changes. Each environment retains its own data and credentials.
Only Directus is seeded by default; optional `seed_cms_media_source_path` must
contain approved sample photos. Customer submissions are never seeded.

Public pet/service reads default to Directus. After importing and verifying test
content, select `cms_pets_source=cms` and `cms_services_source=cms` for the intended
environment; deployment renders both as application runtime settings. Each accepts
only `directus` or `cms`. Blog reads already use the custom CMS.

Nightly `cms-backup.sh` uses SQLite's online backup API (including committed WAL
writes) and archives `database/forms.db` plus `media/`. It checks integrity and
nonempty schema, uses unique names, and retains private archives for 30 days.

```sh
<app-directory>/cms-restore.sh /var/backups/pearlandpaws/test/cms/cms-<id>.tar.gz
# Apply only after validating the intended environment/archive:
<app-directory>/cms-restore.sh /var/backups/pearlandpaws/test/cms/cms-<id>.tar.gz --apply
```

Restore validates paths, rejects links and empty/corrupt databases, stops Astro,
takes a recovery snapshot, overlays photos, restores SQLite, and restarts Astro
even on failure. Newer orphan photos remain on disk for recovery but are not
publicly served. Directus and its data are untouched. Keep off-host copies;
a local backup does not protect against server loss.
