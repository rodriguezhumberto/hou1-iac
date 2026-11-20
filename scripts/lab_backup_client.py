"""Pinned backup transport and controller-only recovery verification."""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import lab_backup_runtime
from ssh_target import private_file

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


class BackupClient:
    def __init__(self, workflow, identity):
        self.workflow = workflow
        self.config, self.run = workflow.config, workflow.run
        self.identity = private_file(identity)
        self.identity_hash = hashlib.sha256(self.identity.read_bytes()).hexdigest()
        self.calls, self.point = 0, None
        write_private(self.run / 'offline-identity.json', json.dumps({
            'path': str(self.identity), 'sha256': self.identity_hash}) + '\n')

    def prefix(self, role):
        self.workflow.check_inputs()
        require(hashlib.sha256(private_file(str(self.identity)).read_bytes()).hexdigest()
                == self.identity_hash, 'The offline recovery identity changed during this attempt')
        host = self.config['hosts'][role]
        current = self.workflow.backup_transport(role)
        require(current == self.workflow.transports[role], 'Backup SSH settings changed during this attempt')
        return [*current[0], host['ssh_host_ip']]

    def remote(self, role, operation, expected=None, timeout=None):
        host = self.config['hosts'][role]
        contract = {'environment': 'test', 'role': role, 'hostname': host['hostname'],
                    'address': host['ssh_host_ip'], 'interface': self.workflow.selections[role]['interface'],
                    'source_ip': self.config['hosts']['hou1']['ssh_host_ip'], 'operation': operation}
        if expected is not None:
            contract['expected'] = expected
        command = [*self.prefix(role), shlex.join(['sudo', '-n', 'python3', '-c',
                                                Path(lab_backup_runtime.__file__).read_text()])]
        self.calls += 1
        stem = self.run / f'{self.calls:03d}-{role}-{operation}'
        result = subprocess.run(command, input=json.dumps(contract), capture_output=True,
                                text=True, timeout=timeout if timeout is not None else
                                (420 if operation == 'interrupt' else 45))
        write_private(stem.with_suffix('.json'), result.stdout)
        write_private(stem.with_suffix('.stderr'), result.stderr)
        if result.returncode == 255:
            raise ConnectionError('Backup observation lost its pinned SSH connection')
        require(result.returncode == 0, 'Backup host operation failed; inspect its private result')
        value = json.loads(result.stdout)
        require(not value.get('failed'), 'Backup host operation failed; inspect its private result')
        return value

    def wait_access(self, role):
        """A completed source snapshot can precede companion VPN reconnection."""
        deadline = time.monotonic() + 180
        while True:
            self.prefix(role)  # Fail immediately on input, identity or guard drift.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Private backup access did not recover')
            try:
                report = self.remote(role, 'access', timeout=min(30, remaining))
                require(report.get('private_access') is True, 'Private backup admission did not pass')
                return
            except (ConnectionError, subprocess.TimeoutExpired):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(3, remaining))

    def verify_offline(self):
        require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', self.point['id']), 'Invalid recovery point identifier')
        point = self.run / 'point'; point.mkdir(mode=0o700)
        for name in lab_backup_runtime.POINT_FILES:
            source = '/var/lib/hou4-backups/test/hou1/' + self.point['id'] + '/' + name
            with (point / name).open('xb') as output:
                result = subprocess.run([*self.prefix('hou4'), shlex.join(['sudo', '-n', 'cat', source])],
                                        stdout=output, stderr=subprocess.PIPE, timeout=240)
            require(result.returncode == 0, 'Committed recovery point copy failed')
        require(json.loads((point / 'manifest.json').read_text()) == self.point,
                'Copied recovery manifest differs from the selected point')
        plaintext = self.run / 'verified-plaintext'
        try:
            self.prefix('hou4')  # Recheck inputs and offline identity before using it.
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify-recovery.py'),
                '--archive', str(point / 'recovery.age'), '--manifest', str(point / 'manifest.json'),
                '--identity', str(self.identity), '--output', str(plaintext), '--environment', 'test'],
                capture_output=True, text=True, timeout=660)
            write_private(self.run / 'offline-verification.stdout', result.stdout)
            write_private(self.run / 'offline-verification.stderr', result.stderr)
            require(result.returncode == 0, 'Offline recovery verification failed')
            report = json.loads(result.stdout)
            require(report['offline_decryption'] and report['staff_revision'] == self.config['application']['revision'],
                    'Offline recovery set does not match the selected release')
            write_private(self.run / 'offline-verification.json', json.dumps(report, indent=2) + '\n')
        finally:
            if plaintext.exists():
                shutil.rmtree(plaintext)
