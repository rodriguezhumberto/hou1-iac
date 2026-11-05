"""Archive stopped Directus storage; never replace a previous recovery archive."""
import hashlib
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import sys
import tarfile
import tempfile


def archive_storage(app, archive):
    database = app / 'database/directus.db'
    media = app / 'directus-data'
    paths = ([database] if database.exists() else [])
    if media.exists():
        paths += [media, *sorted(media.rglob('*'))]
    if not paths:
        return 'absent'
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        raise ValueError('Legacy storage contains links or special files; review before archiving.')
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
            checkpoint = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0] != 0:
                raise ValueError('Directus checkpoint is busy; stop remaining writers/readers before retrying.')
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Directus database failed integrity validation.')
            if not db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                raise ValueError('Directus database has no tables.')

    files = {str(p.relative_to(app)): p for p in paths if p.is_file()}

    def verify(path):
        with tarfile.open(path) as saved:
            members = saved.getmembers()
            if any(not (m.isdir() or m.isfile()) for m in members):
                raise ValueError('Recovery archive contains unsupported entries.')
            saved_files = [m for m in members if m.isfile()]
            if len(saved_files) != len(files) or {m.name for m in saved_files} != set(files):
                raise ValueError('Recovery archive does not match legacy storage.')
            for member in saved_files:
                with saved.extractfile(member) as source, files[member.name].open('rb') as live:
                    if hashlib.file_digest(source, 'sha256').digest() != hashlib.file_digest(live, 'sha256').digest():
                        raise ValueError('Recovery archive differs from legacy storage; choose a new archive path.')

    if archive.is_symlink():
        raise ValueError('Recovery archive must not be a symbolic link.')
    if archive.exists():
        verify(archive)
        return 'verified'
    fd, temporary = tempfile.mkstemp(prefix='.directus-', suffix='.tar.gz', dir=archive.parent)
    os.close(fd)  # mkstemp creates a private file (0600).
    temporary = Path(temporary)
    try:
        with tarfile.open(temporary, 'w:gz') as output:
            for path in paths:
                output.add(path, arcname=str(path.relative_to(app)), recursive=False)
        verify(temporary)
        os.link(temporary, archive)  # Publish atomically without overwriting an older archive.
    finally:
        temporary.unlink(missing_ok=True)
    return 'created'


if __name__ == '__main__':
    print(archive_storage(Path(sys.argv[1]).resolve(), Path(sys.argv[2])))
