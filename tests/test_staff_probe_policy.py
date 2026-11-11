"""Optional probe access must remain one monitoring peer to one host on TCP443."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from helpers import ROLES, run_play
from test_recovery_peer_groups import POLICY, fixture


def plan(state, enabled):
    return POLICY['staff_probe_plan'](state['groups'], state['peers'], state['policies'],
                                     '100.91.0.22', '100.91.0.10', '100.91.0.0/16', 'test-servers', enabled)


class StaffProbePolicyTest(unittest.TestCase):
    def test_no_default_grant_exact_https_and_safe_disable(self):
        state = fixture(); original = deepcopy(state)
        self.assertIsNone(plan(state, False))
        proposal = plan(state, True)
        rule = proposal['body']['rules'][0]
        self.assertEqual(rule['sources'], ['metrics-monitor'])
        self.assertEqual(rule['destinations'], ['metrics-source'])
        self.assertEqual(rule['ports'], ['443']); self.assertFalse(rule['bidirectional'])
        self.assertEqual(state, original)
        state['policies'].append(dict(id='probe', **proposal['body']))
        self.assertFalse(plan(state, True)['changed'])
        disabled = plan(state, False)
        self.assertFalse(disabled['body']['enabled']); self.assertEqual(disabled['id'], 'probe')
        state['policies'][-1]['description'] = 'Operator-owned'
        with self.assertRaises(Exception): plan(state, True)
        state = fixture(); state['groups'][2]['peers'].append({'id': 'foreign'})
        with self.assertRaises(Exception): plan(state, True)

    def test_real_ansible_https_reconciliation_is_idempotent_and_does_not_add_admin_groups(self):
        state = fixture(); original_groups = deepcopy(state['groups']); writes = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def respond(self, value):
                data = json.dumps(value).encode(); self.send_response(200)
                self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
            def do_GET(self): self.respond(state[self.path.split('/')[-1]])
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path != '/api/policies': self.send_error(409); return
                state['policies'].append(dict(id='probe', **body)); writes.append(body)
                self.respond(state['policies'][-1])
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        try:
            variables = dict(netbird_api_url='http://127.0.0.1:' + str(http.server_port), netbird_api_headers={},
                             netbird_subnet='100.91.0.0/16', netbird_server_group='test-servers', netbird_ip='100.91.0.10',
                             hou1_monitoring_peer_ip='100.91.0.22', hou1_staff_probe_enabled=True)
            tasks = [{'ansible.builtin.include_tasks': str(ROLES / 'monitoring_exporter/tasks/policy.yml')}] * 2
            result = run_play(variables, tasks)
        finally: http.shutdown(); http.server_close(); thread.join()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state['groups'], original_groups)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['rules'][0]['ports'], ['443'])


if __name__ == '__main__': unittest.main()
