"""Capture, interrupt collection and verify recovery through maintained paths."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import lab_backup_runtime

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('backup_deploy_lab', ROOT / 'scripts/deploy-lab.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def require(condition, message):
    if not condition:
        raise ValueError(message)


class BackupScenario:
    def __init__(self, config, declaration, run):
        self.config, self.declaration, self.run = config, declaration, run
        run.mkdir(mode=0o700)
        self.workflow = deploy.Workflow(config, run)
        self.workflow.phase = 'backup-transfer-interruption'
        self.identity = deploy.private_file(declaration['recovery_identity'])
        self.identity_hash = hashlib.sha256(self.identity.read_bytes()).hexdigest()
        self.calls = 0
        self.point = None
        deploy.write_private(run / 'offline-identity.json', json.dumps({
            'path': str(self.identity), 'sha256': self.identity_hash}) + '\n')

    def prefix(self, role):
        self.workflow.check_inputs()
        require(hashlib.sha256(deploy.private_file(str(self.identity)).read_bytes()).hexdigest()
                == self.identity_hash, 'The offline recovery identity changed during this attempt')
        host = self.config['hosts'][role]
        current = deploy.transport(host)
        require(current == self.workflow.transports[role], 'Backup SSH settings changed during this attempt')
        return [*current[0], host['ssh_host_ip']]

    def remote(self, role, operation, expected=None):
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
                                text=True, timeout=420 if operation == 'interrupt' else 45)
        deploy.write_private(stem.with_suffix('.json'), result.stdout)
        deploy.write_private(stem.with_suffix('.stderr'), result.stderr)
        if result.returncode == 255:
            raise ConnectionError('Backup observation lost its pinned SSH connection')
        require(result.returncode == 0, 'Backup host operation failed; inspect its private result')
        value = json.loads(result.stdout)
        require(not value.get('failed'), 'Backup host operation failed; inspect its private result')
        return value

    def capture(self):
        before = self.remote('hou1', 'source-state')
        require(before['manifest']['size'] > 3 * 1024 * 1024,
                'The live interruption drill needs ciphertext larger than three MiB')
        public = subprocess.run(['age-keygen', '-y', str(self.identity)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30)
        require(public.returncode == 0 and public.stdout.strip() == before['recipient'],
                'Offline recovery identity does not match the source recipient')
        self.remote('hou1', 'capture')
        deadline = time.monotonic() + 3000
        while time.monotonic() < deadline:
            try:
                state = self.remote('hou1', 'source-state')
            except (ConnectionError, subprocess.TimeoutExpired):
                # This local capture intentionally pauses NetBird; retry observations only.
                time.sleep(3)
                continue
            done = state['service']['ActiveState'] not in ('active', 'activating', 'deactivating')
            ran = state['service']['ExecMainStartTimestampMonotonic'] != before['service']['ExecMainStartTimestampMonotonic']
            if done and ran:
                require(state['service']['Result'] == 'success' and state['status']['last_run_success'] == 1
                        and state['status']['last_success'] > before['status']['last_success']
                        and not state['intent'], 'Fresh source capture did not recover successfully')
                self.point = state['manifest']
                return
            time.sleep(2)
        raise TimeoutError('Fresh local capture did not finish before its observation deadline')

    def interrupt(self):
        frozen = self.remote('hou1', 'source-state')
        require(frozen['manifest'] == self.point and not frozen['intent'],
                'Source recovery point changed before transfer admission')
        result = self.remote('hou4', 'interrupt', expected=self.point)
        deploy.write_private(self.run / 'interruption.json', json.dumps(result, indent=2) + '\n')

    def verify_offline(self):
        require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', self.point['id']), 'Invalid recovery point identifier')
        point = self.run / 'point'; point.mkdir(mode=0o700)
        for name in lab_backup_runtime.POINT_FILES:
            source = '/var/lib/hou4-backups/test/hou1/' + self.point['id'] + '/' + name
            with (point / name).open('xb') as output:
                result = subprocess.run([*self.prefix('hou4'), shlex.join(['sudo', '-n', 'cat', source])],
                                        stdout=output, stderr=subprocess.PIPE, timeout=240)
            require(result.returncode == 0, 'Committed recovery point copy failed')
        plaintext = self.run / 'verified-plaintext'
        try:
            self.prefix('hou4')  # Recheck inputs and offline identity before using it.
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify-recovery.py'),
                '--archive', str(point / 'recovery.age'), '--manifest', str(point / 'manifest.json'),
                '--identity', str(self.identity), '--output', str(plaintext), '--environment', 'test'],
                capture_output=True, text=True, timeout=660)
            deploy.write_private(self.run / 'offline-verification.stdout', result.stdout)
            deploy.write_private(self.run / 'offline-verification.stderr', result.stderr)
            require(result.returncode == 0, 'Offline recovery verification failed')
            report = json.loads(result.stdout)
            require(report['offline_decryption'] and report['staff_revision'] == self.config['application']['revision'],
                    'Offline recovery set does not match the selected release')
            deploy.write_private(self.run / 'offline-verification.json', json.dumps(report, indent=2) + '\n')
        finally:
            if plaintext.exists():
                shutil.rmtree(plaintext)

    def execute(self):
        w = self.workflow
        try:
            for role in deploy.SERVICES:
                w.step(role + '-inputs', lambda role=role: w.prepare_inputs(role))
            w.step('input-contracts', w.validate_contracts)
            require(all(w.selections[role]['contracts']['backup_enabled'] for role in deploy.SERVICES),
                    'Both backup endpoints must be enabled in the selected inputs')
            before = self.remote('hou4', 'receiver-state')
            require(before['points'] and not before['partials'], 'Require an intact prior recovery point')
            require(before['service']['ActiveState'] in ('inactive', 'failed'), 'Collection is already running')
            require(before['service']['RuntimeMaxUSec'] not in ('infinity', '0'),
                    'Upgrade the receiver runtime limit before this drill')
            deploy.write_private(self.run / 'prior-receiver.json', json.dumps(before, indent=2) + '\n')
            # Capture must run outside the guard that would otherwise block it.
            w.step('fresh-local-capture', self.capture)
            with w.capture_guard():
                w.step('interrupt-and-retry-transfer', self.interrupt)
                w.step('offline-decryption-and-integrity', self.verify_offline)
                for role in deploy.SERVICES:
                    w.step(role + '-verify', lambda role=role: w.probe(role))
            w.status = 'passed'; w.save()
        except BaseException:
            w.status = 'failed'; w.save()
            raise
