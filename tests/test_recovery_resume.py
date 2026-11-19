"""Persisted publication resumes only the pinned peer handoff after interruption."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

import yaml

from helpers import ROOT, ROLES, guard_play, run_play, run_plays
import test_recovery_peer_groups as groups

TASKS = ROLES / 'host_recovery/tasks'


class RecoveryResumeTest(unittest.TestCase):
    def test_publication_requires_a_non_quarantined_retained_request(self):
        checkpoint = yaml.safe_load((TASKS / 'publication-checkpoint.yml').read_text())
        guard = next(task for task in checkpoint if task['name'] ==
                     'Bind publication to the restored recovery ID, environment and VPN address')
        values = dict(host_recovery_id='fixture-01', cms_environment='test',
                      netbird_ip='100.91.0.10', host_recovery_quarantine=False)
        request = dict(id='fixture-01', environment='test', server_ip='100.91.0.10')
        scenarios = [({}, True), ({'quarantine': False}, True)]
        scenarios += [({'quarantine': value}, False) for value in [True, 'false', 0, None]]
        plays = []
        for resume in [False, True]:
            for change, valid in scenarios:
                variables = {**values, 'host_recovery_resume': resume,
                             'host_recovery_publication_request': {
                                 'content': base64.b64encode(
                                     json.dumps({**request, **change}).encode()).decode()}}
                plays.append(guard_play(variables, [guard], None if valid else guard['name']))
        result = run_plays(plays)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def scenario(self, root, interruption=None):
        state = groups.fixture()
        state['peers'][1].update(name='lost-server', connected=False)
        stage = root / 'host/fixture-01'
        stage.mkdir(parents=True)
        evidence = root / 'evidence/fixture-01'
        evidence.mkdir(parents=True, mode=0o700)
        daemon = root / 'daemon.json'
        trace = root / 'trace.json'
        operations = []
        failures = [interruption] if interruption else []
        report = dict(passed=True, environment='test', ports_published=True,
                      access_reconciliation_operator_asserted=True, host_enrollment_pending=True)
        (stage / 'request.json').write_text(json.dumps(dict(id='fixture-01', environment='test', server_ip='100.91.0.10')))
        (stage / 'restore.py').write_text('import json\nfrom pathlib import Path\nPath(__file__).with_name("publish-report.json").write_text(' + repr(json.dumps(report)) + ')\n')

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def respond(self, value, code=200):
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
            def fail(self, name):
                if name in failures:
                    failures.remove(name)
                    return True
                return False
            def do_GET(self):
                path = self.path.removeprefix('/api/')
                if path.startswith('groups/'):
                    self.respond(next(group for group in state['groups'] if group['id'] == path.split('/')[1]))
                elif path.startswith('peers/'):
                    peers = [peer for peer in state['peers'] if peer['id'] == path.split('/')[1]]
                    self.respond(peers[0] if peers else {}, 200 if peers else 404)
                else: self.respond(state[path])
            def do_DELETE(self):
                identifier = self.path.split('/')[-1]
                operations.append(('DELETE', identifier))
                state['peers'] = [peer for peer in state['peers'] if peer['id'] != identifier]
                for group in state['groups']:
                    group['peers'] = [peer for peer in group['peers'] if peer['id'] != identifier]
                self.respond({}, 500 if self.fail('delete') else 200)
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                operations.append(('ENROLL', 'new'))
                current = [peer for peer in state['peers'] if peer['id'] == 'new']
                peer = current[0] if current else dict(id='new', name='replacement-server', ip='100.91.0.10', connected=True)
                if not current:
                    state['peers'].append(peer)
                    state['groups'][0]['peers'].append({'id': 'new'})
                daemon.write_text(json.dumps(dict(netbirdIp='100.91.0.10/16',
                    management=dict(url='https://vpn.example.invalid', connected=True))))
                self.respond(peer, 500 if self.fail('enroll') else 200)
            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                identifier = self.path.split('/')[-1]
                operations.append(('PUT', identifier))
                group = next(group for group in state['groups'] if group['id'] == identifier)
                group.update(body, peers=[{'id': identifier} for identifier in body['peers']])
                self.respond(group, 500 if self.fail('bind') else 200)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:' + str(server.server_port)
        values = dict(netbird_api_url=url, netbird_api_headers={}, host_recovery_resume=False,
            host_recovery_evidence_directory=str(root / 'evidence'), host_recovery_id='fixture-01',
            host_recovery_lost_peer_id='lost', host_recovery_lost_peer_name='lost-server',
            cms_environment='test', netbird_ip='100.91.0.10', netbird_subnet='100.91.0.0/16',
            hou1_backup_receiver_ip='100.91.0.22', netbird_management_url='https://vpn.example.invalid')
        checkpoint = (TASKS / 'publication-checkpoint.yml').read_text().replace('/var/lib/hou1-host-recovery', str(root / 'host'))
        (root / 'publication-checkpoint.yml').write_text(checkpoint)
        preflight = yaml.safe_load((TASKS / 'replacement-preflight.yml').read_text())
        local = next(task for task in preflight if 'ansible.builtin.command' in task)
        local['ansible.builtin.command']['argv'] = ['python3', '-c',
            'from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())', str(daemon)]
        (root / 'replacement-preflight.yml').write_text(yaml.safe_dump(preflight))
        pipeline = []
        def include(name):
            return {'ansible.builtin.include_tasks': str((root if name in
                ('publication-checkpoint.yml', 'replacement-preflight.yml') else TASKS) / name)}
        for task in yaml.safe_load((TASKS / 'publish.yml').read_text()):
            role = task.get('ansible.builtin.include_role', {}).get('name')
            source = task.get('ansible.builtin.include_tasks')
            if source:
                name = Path(source).name
                if name in ('refresh-management.yml', 'refresh-companion.yml'):
                    pipeline.append(self.record(trace, 'controller' if name == 'refresh-management.yml' else 'companion'))
                else:
                    pipeline.append(include(name))
                    if name == 'replacement-receipt.yml' and interruption == 'receipt':
                        pipeline.append({'ansible.builtin.command': {'argv': ['false']}})
            elif 'block' in task:
                copied = deepcopy(task)
                if any('netbird_token_vault' in item for item in task['block']):
                    copied['block'] = [include('peer-retirement.yml')]
                else:
                    copied['block'] = [include('peer-groups-after.yml')]
                pipeline.append(copied)
            elif role == 'netbird_client':
                pipeline += [{'ansible.builtin.uri': {'url': url + '/enroll', 'method': 'POST', 'status_code': 200},
                              'register': 'fixture_peer'},
                             {'ansible.builtin.set_fact': {'netbird_server_peer': '{{ fixture_peer.json }}'}}]
            elif role in ('netbird_dns', 'security'):
                pipeline.append(self.record(trace, 'dns' if role == 'netbird_dns' else 'ssh'))
        return state, stage, evidence, daemon, trace, operations, values, pipeline

    @staticmethod
    def record(path, phase):
        return {'ansible.builtin.command': {'argv': ['python3', '-c',
            'import json,sys; from pathlib import Path; p=Path(sys.argv[1]); '
            'v=json.loads(p.read_text()) if p.exists() else []; v.append(sys.argv[2]); p.write_text(json.dumps(v))',
            str(path), phase]}}

    def test_publication_guard_keeps_initial_bootstrap_and_explicit_private_resume(self):
        inventory = {'all': {'children': {name: {'hosts': {'fixture': {}}} for name in
                     ['production_recovery', 'prod_env', 'hou1_prod_init']}}}
        play = yaml.safe_load((ROOT / 'ansible/playbooks/publish-production-recovery.yml').read_text())[0]
        guard = next(task for task in play['pre_tasks'] if task.get('name') ==
                     'Require the explicit production restore and reviewed access changes')
        base = dict(cms_environment='production', authentik_environment='prod', node_bootstrap=True,
                    ansible_host='198.51.100.10', prod_vps_public_ip='198.51.100.10', netbird_ip='100.90.0.10',
                    ansible_user='root', new_user='admin', host_recovery_id='fixture-01',
                    host_recovery_console_confirmed=True, host_recovery_access_reviewed=True,
                    host_recovery_lost_peer_id='lost', host_recovery_lost_peer_name='lost-server',
                    hou1_controller_managed_tls=True, host_recovery_resume=False)
        private = dict(node_bootstrap=False, ansible_host='100.90.0.10', ansible_user='admin')
        scenarios = [({}, True), ({**private, 'host_recovery_resume': True}, True),
                     (private, False), ({**private, 'host_recovery_resume': True, 'ansible_user': 'root'}, False),
                     ({'host_recovery_resume': 'true'}, False),
                     ({**private, 'host_recovery_resume': True, 'cms_environment': 'test'}, False)]
        plays = [dict(guard_play({**base, **changes}, [guard], None if valid else guard['name']), hosts='fixture')
                 for changes, valid in scenarios]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resume_after_delete_enroll_receipt_and_partial_binding(self):
        for interruption in ('delete', 'enroll', 'receipt', 'bind'):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                state, stage, evidence, daemon, trace, operations, values, pipeline = self.scenario(root, interruption)
                first = run_play(values, pipeline)
                self.assertNotEqual(first.returncode, 0)
                self.assertTrue((evidence / 'peer-handoff.json').exists(), first.stdout + first.stderr)
                self.assertEqual(json.loads((evidence / 'source-groups-before.json').read_text())['lost_peer_id'], 'lost')
                self.assertFalse(trace.exists())
                if interruption == 'receipt':
                    pipeline = [task for task in pipeline if task.get('ansible.builtin.command', {}).get('argv') != ['false']]
                for attempt in range(2):
                    result = run_play({**values, 'host_recovery_resume': True}, pipeline)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual([operation for operation in operations if operation[0] == 'DELETE'], [('DELETE', 'lost')])
                self.assertEqual([operation for operation in operations if operation[0] == 'PUT'],
                                 [('PUT', 'metrics-source'), ('PUT', 'backup-source')])
                self.assertEqual(json.loads(trace.read_text()), ['dns', 'controller', 'companion', 'ssh'] * 2)
                self.assertEqual(json.loads((evidence / 'replacement-peer.json').read_text())['peer'],
                                 dict(id='new', name='replacement-server', ip='100.91.0.10'))

    def test_resume_refuses_missing_plan_changed_contract_and_foreign_occupant(self):
        for change in ('missing-plan', 'changed-contract', 'foreign-occupant', 'latched-error', 'changed-policy'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                state, stage, evidence, daemon, trace, operations, values, pipeline = self.scenario(root, 'delete')
                first = run_play(values, pipeline)
                self.assertNotEqual(first.returncode, 0)
                if change == 'missing-plan': (evidence / 'peer-handoff.json').unlink()
                if change == 'changed-contract': values['host_recovery_lost_peer_name'] = 'another-server'
                if change == 'foreign-occupant': state['peers'].append(dict(id='foreign', name='foreign', ip='100.91.0.10'))
                if change == 'latched-error': (stage / 'first-error.json').write_text('{}')
                if change == 'changed-policy': state['policies'][0]['rules'][0]['ports'].append('22')
                before = list(operations)
                result = run_play({**values, 'host_recovery_resume': True}, pipeline)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(trace.exists())
                self.assertFalse(any(operation[0] in ('DELETE', 'PUT') for operation in operations[len(before):]))
                self.assertEqual(operations, before)
