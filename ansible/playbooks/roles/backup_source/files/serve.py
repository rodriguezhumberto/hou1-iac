#!/usr/bin/python3
"""Serve only a completed, fresh encrypted point; never stop services over SSH."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time


def serve(config, stream, now=None):
    now = time.time() if now is None else now
    descriptor = os.open(Path(config['work_root']) / 'ready.export', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        header = source.readline(8193)
        if len(header) > 8192 or not header.endswith(b'\n'): raise ValueError('Invalid prepared header')
        manifest = json.loads(header)
        if (manifest.get('schema') != 1 or manifest.get('environment') != config['environment']
                or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', str(manifest.get('id', '')))
                or type(manifest.get('created')) is not int or not now - 21600 <= manifest['created'] <= now + 60
                or type(manifest.get('size')) is not int or not 0 < manifest['size'] <= config['max_archive_bytes']
                or os.fstat(source.fileno()).st_size != len(header) + manifest['size']
                or not re.fullmatch(r'[a-f0-9]{64}', str(manifest.get('sha256', '')))
                or hashlib.file_digest(source, 'sha256').hexdigest() != manifest['sha256']):
            raise ValueError('Incomplete, stale or corrupt prepared recovery point')
        source.seek(len(header))
        if source.read(22) != b'age-encryption.org/v1\n': raise ValueError('Prepared recovery point is not age ciphertext')
        source.seek(0)
        shutil.copyfileobj(source, stream)
        stream.flush()


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1: raise PermissionError('Fixed root export only')
    serve(json.loads(Path('/etc/hou1-backup/source.json').read_text()), sys.stdout.buffer)


if __name__ == '__main__':
    try: main()
    except Exception:
        print('No complete fresh HOU1 recovery point; private diagnostics withheld.', file=sys.stderr)
        sys.exit(1)
