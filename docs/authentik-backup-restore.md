# Authentik backup, restore, and clean-host drill

Each environment writes encrypted backups under
`/var/backups/hou1-authentik/{test,prod}` and copies the verified archive,
checksum, and success marker to the separately mounted off-host path
`/mnt/offhost-backups/hou1-authentik/{test,prod}`. Retention is 30 days. The
backup contains a PostgreSQL logical dump, PostgreSQL persistent state, media,
templates, certificates, generated configuration/blueprints, Compose refs,
root-only rendered client files, and the SOPS ciphertext used to recover the
environment. It is encrypted with `AUTHENTIK_BACKUP_AGE_RECIPIENT`.

`backup.sh` uses `set -euo pipefail` and writes a `.failed` marker from its
error trap. It writes `.success` only after the age decrypt and tar integrity
check pass and all three off-host files are installed. A checksum mismatch,
missing off-host mount, failed dump, or failed encryption leaves no success
marker and is reported by the cron log and failure marker.

Restore a selected verified archive on a clean host with the environment's
offline age identity:

```bash
export AGE_KEY_FILE=/root/.config/sops/age/hou1-authentik-prod.txt
export AUTHENTIK_BACKUP_AGE_RECIPIENT=AGE1_OFFLINE_RECOVERY_RECIPIENT
/opt/hou1-authentik/prod/restore.sh \
  /mnt/offhost-backups/hou1-authentik/prod/authentik-YYYYMMDDTHHMMSSZ.tar.gz.age
```

The restore verifies the adjacent SHA-256 checksum, decrypts into a mode-0700
temporary directory, restores persistent files and PostgreSQL, then starts the
health-gated Compose project. It never merges a backup into a live database.

Run and record the clean-host drill with the expected issuer:

```bash
/opt/hou1-authentik/prod/restore-drill.sh \
  /mnt/offhost-backups/hou1-authentik/prod/authentik-YYYYMMDDTHHMMSSZ.tar.gz.age \
  https://auth.example.com/application/o/pearland-paws-staff/
```

Attach its output to the issue/runbook with the archive identifier, start/end
times and duration, login result, discovery/JWKS result, signing-key continuity,
and any gaps or accepted follow-up. The production gate requires a successful
drill and an offline recovery identity before cutover.
