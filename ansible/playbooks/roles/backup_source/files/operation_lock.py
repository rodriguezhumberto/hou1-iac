#!/usr/bin/python3
"""One host lock for backup capture and controller-managed reconciliation."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import select
import stat
import sys
import time

LOCK_PATH = Path('/run/lock/hou1-backup-export.lock')
WAIT_SECONDS = 900
HOLD_IDLE_SECONDS = 60
INTENT_PATH = Path('/var/lib/hou1-backup/restart-intent.json')


@contextmanager
def exclusive(path=LOCK_PATH, timeout=WAIT_SECONDS, cancelled=None):
    # Never unlink the file: contenders must keep referring to the same inode.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError('Invalid operation lock')
        deadline = time.monotonic() + timeout
        while True:
            if cancelled is not None and cancelled():
                raise BrokenPipeError('Controller closed admission')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Capture or deployment still owns the host lock')
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        yield


def hold():
    with exclusive(cancelled=lambda: bool(select.select([sys.stdin.buffer], [], [], 0)[0])):
        # Recovery must finish before deployment can change recorded writers.
        if INTENT_PATH.exists():
            raise RuntimeError('Interrupted writers require backup recovery')
        print('ready', flush=True)
        # EOF releases immediately; silence also bounds a lost network's lease.
        while True:
            if not select.select([sys.stdin.buffer], [], [], HOLD_IDLE_SECONDS)[0]:
                raise TimeoutError('Controller capture guard stopped responding')
            message = sys.stdin.buffer.read(1)
            if not message:
                return
            if message != b'.':
                raise ValueError('Invalid capture guard keepalive')


if __name__ == '__main__':
    try:
        if os.geteuid() != 0 or sys.argv[1:] != ['--hold']:
            raise PermissionError('Fixed root lock holder only')
        hold()
    except Exception:
        print('Host capture/deployment admission failed.', file=sys.stderr)
        sys.exit(1)
