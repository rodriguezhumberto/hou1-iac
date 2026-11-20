"""Finalize a published lab restore through the maintained component playbooks."""

import hashlib
import json
from pathlib import Path
import subprocess
import time

from lab_backup_client import BackupClient, require, write_private
from ssh_target import private_file

BUSY = ('active', 'activating', 'deactivating')


class Finalization(BackupClient):
    def __init__(self, workflow, identity, resume=None):
        require(workflow.config['version'] == 2, 'Finalization requires version 2 input contracts')
        super().__init__(workflow, identity)
        self.binding = {
            'configuration_sha256': hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest(),
            'inputs': workflow.inputs, 'capabilities': workflow.capabilities,
            'transports': json.loads(json.dumps(workflow.transports)),
            'identity': {'path': str(self.identity), 'sha256': self.identity_hash}}
        if resume:
            receipt = json.loads(private_file(resume).read_text())
            require(receipt.get('version') == 1 and receipt.get('environment') == 'test'
                    and receipt.get('phase') == 'finalize', 'Resume requires a finalization receipt')
            saved = receipt.get('finalization', {})
            require(saved.get('binding') == self.binding, 'Resume inputs or host identities differ')
            self.point = saved.get('point')
        workflow.finalization = {'binding': self.binding, 'point': self.point}
        workflow.save()

    def observe(self, role, operation, inspect, seconds):
        """Retry observations only; host mutations are requested once per attempt."""
        deadline = time.monotonic() + seconds
        while True:
            self.prefix(role)  # Input or key drift is fatal, even while SSH is unavailable.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Backup finalization observation deadline exceeded')
            try:
                state = self.remote(role, operation, timeout=min(45, remaining))
            except (ConnectionError, subprocess.TimeoutExpired):
                state = None
            if state is not None and inspect(state):
                return state
            time.sleep(min(3, max(0, deadline - time.monotonic())))

    def request(self, role, operation, **kwargs):
        try:
            self.remote(role, operation, **kwargs)
        except (ConnectionError, subprocess.TimeoutExpired):
            # The service may already be running. Observe rather than replaying start.
            pass

    def capture_initial(self):
        before = self.observe('hou1', 'source-status', lambda state: True, 3000)
        public = subprocess.run(['age-keygen', '-y', str(self.identity)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30)
        require(public.returncode == 0 and public.stdout.strip() == before['recipient'],
                'Offline recovery identity does not match the source recipient')
        if self.point:
            require(before['manifest'] == self.point, 'Selected source point changed since the previous attempt')
        self.request('hou1', 'ensure-capture')

        def ready(state):
            if state['service']['ActiveState'] in BUSY:
                return False
            if not state['status']['last_success'] and state['service']['Result'] == 'success':
                return False  # A queued --no-block start may not have run yet.
            require(not state['intent'] and state['status']['last_success'] > 0 and state['manifest'],
                    'Initial capture failed or left interrupted writers; inspect source service logs')
            return True

        state = self.observe('hou1', 'source-status', ready, 3000)
        require(self.point is None or state['manifest'] == self.point, 'Selected source point changed during resume')
        self.point = state['manifest']
        self.workflow.finalization['point'] = self.point
        self.workflow.save()

    def collect(self):
        source = self.remote('hou1', 'source-state')
        require(source['manifest'] == self.point and not source['intent'],
                'Source point changed before off-host collection')
        deadline = time.monotonic() + 2000
        # A scheduled transfer may still hold the previous export open. Let it
        # finish before requesting the selected point; both waits share a budget.
        self.observe('hou4', 'receiver-state',
                     lambda state: state['service']['ActiveState'] not in BUSY, 2000)
        self.request('hou4', 'collect', expected=self.point)

        def ready(state):
            retained = state['points'].get(self.point['id'])
            if retained:
                require(retained['manifest'] == self.point, 'Collected recovery point differs from source')
                if state['service']['ActiveState'] in BUSY:
                    return False
                require(not state['partials'], 'Collection left private staging')
                return True
            require(state['service']['ActiveState'] in BUSY or state['service']['Result'] == 'success',
                    'Collection failed before committing the selected point')
            return False

        self.observe('hou4', 'receiver-state', ready, max(0, deadline - time.monotonic()))

    def reconcile(self, role):
        play = Path(self.config['hosts'][role]['repository']) / f'ansible/playbooks/{role}.yml'
        self.workflow.playbook(role, play, role + '-reconcile')

    def execute(self):
        w = self.workflow
        require(all(w.selections[role]['contracts']['backup_enabled'] for role in ('hou1', 'hou4')),
                'Finalization requires both backup endpoints enabled')
        # Finish source/storage migration before backup-source initialization.
        with w.capture_guard():
            w.step('hou1-reconcile', lambda: self.reconcile('hou1'))
            w.step('hou1-verify', lambda: w.probe('hou1'))
            # Timer activation may queue a capture. Keep it blocked until initialization ends.
            play = Path(self.config['hosts']['hou1']['repository']) / 'ansible/playbooks/backups.yml'
            w.step('backup-source-initialize', lambda: w.playbook('hou1', play, 'backup-source-initialize',
                extra_args=('-e', json.dumps({'hou1_backup_capture_on_initialize': False}))))
        # Never capture under the deployment lock: the exporter needs the same lock.
        w.step('initial-local-capture', self.capture_initial)
        w.step('hou4-private-access', lambda: self.wait_access('hou4'))
        # Hold the selected ready.export stable through collection and verification.
        with w.capture_guard(name='backup-verification-admission'):
            w.step('hou4-reconcile', lambda: self.reconcile('hou4'))
            w.step('off-host-collection', self.collect)
            w.step('offline-decryption-and-integrity', self.verify_offline)
            for role in ('hou1', 'hou4'):
                w.step(role + '-final-verify', lambda role=role: w.probe(role))
