"""Recover only previously verified managed groups after replacing a lost peer."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import threading
import unittest

from helpers import ROOT, ROLES, run_play

POLICY = runpy.run_path(str(ROOT / 'ansible/playbooks/filter_plugins/netbird_policies.py'))


def fixture():
    peers = [{'id': 'monitor', 'ip': '100.91.0.22'}, {'id': 'lost', 'ip': '100.91.0.10'}]
    groups = [dict(id='environment', name='test-servers', peers=[{'id': 'monitor'}, {'id': 'lost'}], resources=None)]
    for identifier, name, peer in [('metrics-monitor', 'hou1-monitoring-test', 'monitor'),
                                   ('metrics-source', 'hou1-metrics-test', 'lost'),
                                   ('backup-receiver', 'hou1-backup-test-receiver', 'monitor'),
                                   ('backup-source', 'hou1-backup-test-source', 'lost')]:
        groups.append(dict(id=identifier, name=name, peers=[{'id': peer}], resources=None))
    metrics = POLICY['monitoring_plan'](groups, peers, [], '100.91.0.22', '100.91.0.10', '100.91.0.0/16', 'test-servers')['policy']['body']
    backup = POLICY['backup_plan'](groups, peers, [], '100.91.0.22', '100.91.0.10', '100.91.0.0/16', 'test')['policy']['body']
    return dict(groups=groups, peers=peers, policies=[dict(id='metrics-policy', **metrics), dict(id='backup-policy', **backup)])


def plan(state, lost='lost'):
    return POLICY['recovery_source_groups'](state['groups'], state['peers'], state['policies'],
                                             '100.91.0.22', '100.91.0.10', '100.91.0.0/16', lost)


class RecoveryPeerGroupsTest(unittest.TestCase):
    def test_only_exact_lost_peer_and_managed_groups_can_be_pinned(self):
        state = fixture()
        before = deepcopy(state)
        self.assertEqual([{k: group[k] for k in ['id', 'name']} for group in plan(state)],
                         [{'id': 'metrics-source', 'name': 'hou1-metrics-test'},
                          {'id': 'backup-source', 'name': 'hou1-backup-test-source'}])
        self.assertEqual(state, before)
        with self.assertRaises(Exception):
            plan(state, 'another-device')
        for change in ['foreign-member', 'empty-members', 'resource', 'duplicate', 'operator-policy', 'broadened-policy']:
            with self.subTest(change=change):
                state = fixture()
                source = state['groups'][2]
                if change == 'foreign-member': source['peers'].append({'id': 'unrelated'})
                if change == 'empty-members': source['peers'] = None
                if change == 'resource': source['resources'] = [{'id': 'resource'}]
                if change == 'duplicate': state['groups'].append(deepcopy(source))
                if change == 'operator-policy': state['policies'][0]['description'] = 'Operator owned'
                if change == 'broadened-policy': state['policies'][0]['rules'][0]['ports'].append('22')
                with self.assertRaises(Exception): plan(state)

    def test_ordinary_planners_still_refuse_empty_source_groups(self):
        state = fixture()
        state['groups'][2]['peers'] = None
        state['groups'][4]['peers'] = None
        with self.assertRaisesRegex(Exception, 'unexpected members'):
            POLICY['monitoring_plan'](state['groups'], state['peers'], state['policies'], '100.91.0.22', '100.91.0.10', '100.91.0.0/16', 'test-servers')
        with self.assertRaisesRegex(Exception, 'unexpected membership'):
            POLICY['backup_plan'](state['groups'], state['peers'], state['policies'], '100.91.0.22', '100.91.0.10', '100.91.0.0/16', 'test')

    def test_real_ansible_rebind_preserves_identity_and_rejects_intervening_changes(self):
        for interference in [None, 'peer', 'resource', 'identity', 'policy']:
            with self.subTest(interference=interference):
                state = fixture()
                original = deepcopy(state)
                writes = []

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args): pass
                    def respond(self, value):
                        body = json.dumps(value).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers(); self.wfile.write(body)
                    def do_GET(self):
                        path = self.path.removeprefix('/api/')
                        if path.startswith('groups/'):
                            self.respond(next(g for g in state['groups'] if g['id'] == path.split('/')[1]))
                        else: self.respond(state[path])
                    def do_POST(self):
                        # Simulate upstream deletion/enrollment between production task files.
                        self.rfile.read(int(self.headers.get('Content-Length', 0)))
                        state['peers'][1] = {'id': 'new', 'ip': '100.91.0.10'}
                        state['groups'][0]['peers'] = [{'id': 'monitor'}, {'id': 'new'}]
                        for group in state['groups'][1:]:
                            if group['peers'] == [{'id': 'lost'}]: group['peers'] = None
                        if interference == 'peer': state['groups'][2]['peers'] = [{'id': 'unrelated'}]
                        if interference == 'resource': state['groups'][2]['resources'] = [{'id': 'resource'}]
                        if interference == 'identity': state['groups'][2]['name'] = 'operator-renamed'
                        if interference == 'policy': state['policies'][0]['rules'][0]['ports'].append('22')
                        self.respond({})
                    def do_PUT(self):
                        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        identifier = self.path.split('/')[-1]
                        writes.append((identifier, deepcopy(body)))
                        group = next(g for g in state['groups'] if g['id'] == identifier)
                        group.update(body, peers=[{'id': p} for p in body['peers']])
                        self.respond(group)

                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                url = 'http://127.0.0.1:' + str(server.server_port)
                try:
                    variables = dict(netbird_api_url=url, netbird_api_headers={},
                                     hou1_backup_receiver_ip='100.91.0.22', netbird_ip='100.91.0.10',
                                     netbird_subnet='100.91.0.0/16', host_recovery_lost_peer_id='lost',
                                     netbird_server_peer={'id': 'new', 'ip': '100.91.0.10'})
                    result = run_play(variables, [
                        {'ansible.builtin.include_tasks': str(ROLES / 'host_recovery/tasks/peer-groups-before.yml')},
                        {'ansible.builtin.uri': {'url': url + '/fixture-enrollment', 'method': 'POST', 'status_code': 200}},
                        {'ansible.builtin.include_tasks': str(ROLES / 'host_recovery/tasks/peer-groups-after.yml')},
                    ])
                finally:
                    server.shutdown(); server.server_close(); thread.join()
                self.assertEqual(result.returncode == 0, interference is None, result.stdout + result.stderr)
                expected_policies = deepcopy(original['policies'])
                if interference == 'policy': expected_policies[0]['rules'][0]['ports'].append('22')
                self.assertEqual(state['policies'], expected_policies)
                for index in [1, 3]: self.assertEqual(state['groups'][index], original['groups'][index])
                if interference:
                    self.assertEqual(writes, [])
                else:
                    self.assertEqual([identifier for identifier, _ in writes], ['metrics-source', 'backup-source'])
                    for index in [2, 4]:
                        self.assertEqual(state['groups'][index]['id'], original['groups'][index]['id'])
                        self.assertEqual(state['groups'][index]['name'], original['groups'][index]['name'])
                        self.assertEqual(state['groups'][index]['peers'], [{'id': 'new'}])


if __name__ == '__main__': unittest.main()
