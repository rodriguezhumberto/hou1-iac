"""Capture, interrupt collection and verify recovery through maintained paths."""

import importlib.util
import json
from pathlib import Path
import subprocess
import time

from lab_backup_client import BackupClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('backup_deploy_lab', ROOT / 'scripts/deploy-lab.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def require(condition, message):
    if not condition:
        raise ValueError(message)


class BackupScenario(BackupClient):
    def __init__(self, config, declaration, run):
        self.config, self.declaration, self.run = config, declaration, run
        run.mkdir(mode=0o700)
        self.workflow = deploy.Workflow(config, run)
        self.workflow.phase = 'backup-transfer-interruption'
        super().__init__(self.workflow, declaration['recovery_identity'])

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
                for role in deploy.SERVICES:
                    w.step(role + '-private-access', lambda role=role: self.wait_access(role))
                w.step('interrupt-and-retry-transfer', self.interrupt)
                w.step('offline-decryption-and-integrity', self.verify_offline)
                for role in deploy.SERVICES:
                    w.step(role + '-verify', lambda role=role: w.probe(role))
            w.status = 'passed'; w.save()
        except BaseException:
            w.status = 'failed'; w.save()
            raise
