"""Hold HOU1's capture lock over pinned SSH while the two-host workflow runs."""

import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time

PROGRAM = (Path(__file__).resolve().parents[1] /
           'ansible/playbooks/roles/backup_source/files/operation_lock.py').read_text()
WAIT_SECONDS = 900
KEEPALIVE_SECONDS = 10


class CaptureGuard:
    def __init__(self, command, log, timeout=WAIT_SECONDS + 30):
        self.command, self.log, self.timeout = command, log, timeout
        self.process = None
        self.admitted = False
        self.stopped = threading.Event()
        self.keepalive_thread = None
        self.keepalive_failed = False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        try:
            descriptor = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as diagnostics:
                while True:
                    self.stopped.clear()
                    self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=diagnostics)
                    with selectors.DefaultSelector() as selector:
                        selector.register(self.process.stdout, selectors.EVENT_READ)
                        events = selector.select(max(0, deadline - time.monotonic()))
                    if events and self.process.stdout.readline() == b'ready\n':
                        self.admitted = True
                        self.keepalive_thread = threading.Thread(target=self.keepalive, daemon=True)
                        self.keepalive_thread.start()
                        self.check()
                        return self
                    self.close()
                    # Admission alone may wait for an already-running capture's
                    # VPN outage. Never retry a deployment operation here.
                    if self.process.returncode != 255 or time.monotonic() >= deadline:
                        raise ValueError('Host capture/deployment admission failed; see private log')
                    time.sleep(min(3, max(0, deadline - time.monotonic())))
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.keepalive_failed or self.process.poll() is not None:
            raise ValueError('Host capture lock connection was lost')

    def keepalive(self):
        try:
            while not self.stopped.is_set():
                self.process.stdin.write(b'.')
                self.process.stdin.flush()
                if self.stopped.wait(KEEPALIVE_SECONDS):
                    return
        except (OSError, ValueError):
            self.keepalive_failed = True

    def run(self, command, **kwargs):
        """Stop active controller work if its lock connection disappears."""
        self.check()
        process = subprocess.Popen(command, start_new_session=True, **kwargs)
        try:
            while process.poll() is None:
                self.check()
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            self.check()
            return subprocess.CompletedProcess(command, process.returncode)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

    def close(self):
        if self.process is None:
            return
        self.stopped.set()
        if self.keepalive_thread is not None:
            self.keepalive_thread.join(timeout=1)
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        if not self.admitted and self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()

    def __exit__(self, *_):
        self.close()
