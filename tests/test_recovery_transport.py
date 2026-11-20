"""Optional recovery transports preserve pinned identities and stop on failure."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import tempfile
import threading
import unittest

import yaml

from helpers import ROOT, ROLES, guard_play, run_play, run_plays
import test_recovery_peer_groups as existing

FILTERS = runpy.run_path(str(ROOT / 'ansible/playbooks/filter_plugins/host_recovery.py'))
TASKS = ROLES / 'host_recovery/tasks'


def fixture():
    state = existing.fixture()
    for peer in state['peers']:
        peer['groups'] = [{'id': 'environment', 'name': 'test-servers'}]
    state['peers'].append(dict(id='renewal-peer', ip='100.91.0.30',
        groups=[{'id': 'renewal-source', 'name': 'tls-renewal-controller-test'}],
        ephemeral=False, inactivity_expiration_enabled=False, login_expiration_enabled=False, login_expired=False))
    state['groups'] += [dict(id='renewal-source', name='tls-renewal-controller-test',
                            peers=[{'id': 'renewal-peer'}], resources=[]),
                        dict(id='renewal-targets', name='tls-renewal-receivers-test',
                            peers=[{'id': 'monitor'}, {'id': 'lost'}], resources=[])]
    state['policies'].append(dict(id='renewal-policy', name='tls-renewal-test',
        description='controller-renewal-peer/v1', enabled=True, rules=[dict(name='receiver-ssh',
            enabled=True, action='accept', bidirectional=False, protocol='tcp', ports=['22'],
            sources=[{'id': 'renewal-source'}], destinations=[{'id': 'renewal-targets'}])]))
    config = dict(environment='test', targets=['100.91.0.10', '100.91.0.22'], subnet='100.91.0.0/16',
                  server_group='test-servers', management_url='https://vpn.example.invalid')
    return state, config


def plan(state, config, lost='lost', unbound=False):
    return FILTERS['renewal_plan'](state['groups'], state['peers'], state['policies'], config,
                                 'test', '100.91.0.10', lost, 'renewal-peer', unbound)


class RecoveryRenewalTest(unittest.TestCase):
    def test_renewal_plan_pins_existing_grants_and_preserves_other_receivers(self):
        state, config = fixture()
        before = deepcopy(state)
        result = plan(state, config)
        self.assertEqual(result['id'], 'renewal-targets')
        self.assertEqual(result['peers'], ['monitor'])
        self.assertEqual(state, before)
        for change in ('extra-member', 'missing-member', 'resource', 'owner', 'port', 'source',
                       'source-group', 'expired', 'duplicates', 'foreign-target', 'environment'):
            with self.subTest(change=change):
                altered, values = fixture()
                if change == 'extra-member': altered['groups'][-1]['peers'].append({'id': 'foreign'})
                if change == 'missing-member': altered['groups'][-1]['peers'].pop()
                if change == 'resource': altered['groups'][-1]['resources'] = [{'id': 'resource'}]
                if change == 'owner': altered['policies'][-1]['description'] = 'operator'
                if change == 'port': altered['policies'][-1]['rules'][0]['ports'].append('443')
                if change == 'source': altered['policies'][-1]['rules'][0]['sources'] = ['foreign']
                if change == 'source-group': altered['peers'][-1]['groups'].append({'id': 'administrators'})
                if change == 'expired': altered['peers'][-1]['login_expired'] = True
                if change == 'duplicates': values['targets'].append(values['targets'][0])
                if change == 'foreign-target': values['targets'].append('192.0.2.4')
                if change == 'environment': values['environment'] = 'production'
                with self.assertRaises(Exception): plan(altered, values)

    def test_real_tasks_rebind_once_retry_partial_work_and_reject_interference(self):
        for interference in (None, 'partial', 'source-member', 'receiver-member', 'policy', 'resource'):
            with self.subTest(interference=interference):
                state, config = fixture()
                writes = []

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args): pass
                    def respond(self, value):
                        data = json.dumps(value).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers(); self.wfile.write(data)
                    def do_GET(self):
                        path = self.path.removeprefix('/api/')
                        if path.startswith('groups/'):
                            self.respond(next(g for g in state['groups'] if g['id'] == path.split('/')[1]))
                        else: self.respond(state[path])
                    def do_POST(self):
                        self.rfile.read(int(self.headers.get('Content-Length', 0)))
                        state['peers'][1]['id'] = 'new'
                        for group in state['groups']:
                            group['peers'] = [peer for peer in group['peers'] if peer['id'] != 'lost']
                        state['groups'][0]['peers'].append({'id': 'new'})
                        if interference == 'partial': state['groups'][2]['peers'] = [{'id': 'new'}]
                        if interference == 'source-member': state['groups'][-2]['peers'].append({'id': 'foreign'})
                        if interference == 'receiver-member': state['groups'][-1]['peers'].append({'id': 'foreign'})
                        if interference == 'resource': state['groups'][-1]['resources'] = [{'id': 'resource'}]
                        if interference == 'policy': state['policies'][-1]['rules'][0]['ports'].append('443')
                        self.respond({})
                    def do_PUT(self):
                        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        identifier = self.path.split('/')[-1]
                        writes.append(identifier)
                        group = next(g for g in state['groups'] if g['id'] == identifier)
                        group.update(body, peers=[{'id': peer} for peer in body['peers']])
                        self.respond(group)

                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                url = 'http://127.0.0.1:' + str(server.server_port)
                try:
                    values = dict(netbird_api_url=url, netbird_api_headers={},
                        hou1_backup_receiver_ip='100.91.0.22', netbird_ip='100.91.0.10', netbird_subnet='100.91.0.0/16',
                        netbird_management_url=config['management_url'], netbird_server_group='test-servers',
                        cms_environment='test', host_recovery_lost_peer_id='lost',
                        netbird_server_peer={'id': 'new', 'ip': '100.91.0.10'},
                        host_recovery_renewal_handoff=True, host_recovery_renewal_peer_id='renewal-peer',
                        hou1_tls_peer_config=config)
                    tasks = [{'ansible.builtin.include_tasks': str(TASKS / 'peer-groups-before.yml')},
                             {'ansible.builtin.uri': {'url': url + '/fixture-enrollment', 'method': 'POST', 'status_code': 200}},
                             {'ansible.builtin.include_tasks': str(TASKS / 'peer-groups-after.yml')},
                             {'ansible.builtin.include_tasks': str(TASKS / 'peer-groups-after.yml')}]
                    result = run_play(values, tasks)
                finally:
                    server.shutdown(); server.server_close(); thread.join()
                success = interference in (None, 'partial')
                self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
                if success:
                    self.assertEqual(writes, ['metrics-source', 'backup-source', 'renewal-targets']
                                     if interference is None else ['backup-source', 'renewal-targets'])
                    self.assertEqual(state['groups'][-1]['peers'], [{'id': 'monitor'}, {'id': 'new'}])
                else:
                    self.assertEqual(writes, [])


class RecoveryTransportTest(unittest.TestCase):
    def test_monitoring_omission_is_explicit_and_test_only_before_publication(self):
        guard = yaml.safe_load((TASKS / 'transport-preflight.yml').read_text())[0]
        scenarios = [({'cms_environment': 'test', 'host_recovery_monitoring_required': False}, True),
                     ({'cms_environment': 'production', 'host_recovery_monitoring_required': True}, True),
                     ({'cms_environment': 'production', 'host_recovery_monitoring_required': False}, False),
                     ({'cms_environment': 'test', 'host_recovery_monitoring_required': 'false'}, False)]
        result = run_plays([guard_play(values, [guard], None if valid else guard['name'])
                            for values, valid in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_receiver_input_is_required_before_publication(self):
        tasks = yaml.safe_load((TASKS / 'transport-preflight.yml').read_text())
        guard = next(task for task in tasks if task['name'] == 'Require the declared backup receiver before opening ingress')
        self.assertEqual(guard['name'], 'Require the declared backup receiver before opening ingress')
        values = dict(netbird_subnet='100.91.0.0/16', netbird_ip='100.91.0.10')
        scenarios = [({}, False), ({'hou1_backup_receiver_ip': ''}, False),
                     ({'hou1_backup_receiver_ip': '100.91.0.22/32'}, False),
                     ({'hou1_backup_receiver_ip': '100.91.0.10'}, False),
                     ({'hou1_backup_receiver_ip': '100.90.0.22'}, False),
                     ({'hou1_backup_receiver_ip': '100.91.0.22'}, True)]
        result = run_plays([guard_play({**values, **change}, [guard], None if valid else guard['name'])
                            for change, valid in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_companion_requires_an_explicit_independent_pinned_destination(self):
        config = dict(inventory_host='hou4-recovery', public_ip='8.8.8.8', known_hosts_file='/private/known_hosts')
        inventory = {'hou4-recovery': {'ansible_host': '8.8.8.8'}}
        check = FILTERS['companion_endpoint']
        self.assertTrue(check(config, inventory, 'hou1', '100.91.0.22'))
        for key, value in [('inventory_host', 'localhost'), ('inventory_host', 'hou1'),
                           ('public_ip', '100.91.0.22'), ('public_ip', '127.0.0.1'),
                           ('known_hosts_file', 'relative'), ('known_hosts_file', '/private/../key')]:
            with self.subTest(key=key), self.assertRaises(Exception):
                check({**config, key: value}, inventory, 'hou1', '100.91.0.22')
        with self.assertRaises(Exception): check(config, {}, 'hou1', '100.91.0.22')

    def test_controller_guard_checks_environment_and_preflight_precedes_publication(self):
        tasks = [{'ansible.builtin.include_tasks': str(TASKS / 'management-refresh-preflight.yml')}]
        command = ['true']
        cases = [dict(cms_environment='test'), dict(cms_environment='production'), dict(cms_environment='unknown')]
        plays = [guard_play({**values, 'host_recovery_management_refresh_argv': command}, tasks,
                            'Validate the optional dedicated controller VPN reconnect command'
                            if values['cms_environment'] == 'unknown' else None) for values in cases]
        result = run_plays(plays)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        publication = yaml.safe_load((TASKS / 'publish.yml').read_text())
        self.assertEqual(publication[0]['ansible.builtin.include_tasks'], 'roles/host_recovery/tasks/transport-preflight.yml')
        before = yaml.safe_load((TASKS / 'peer-groups-before.yml').read_text())
        self.assertTrue(any('host_recovery_renewal_handoff' in str(task) for task in before))
        retirement = yaml.safe_load((TASKS / 'peer-retirement.yml').read_text())
        block = next(task['block'] for task in retirement if task['name'] == 'Pin exact managed groups before the one permitted retirement')
        pin = next(index for index, task in enumerate(block) if task.get('ansible.builtin.include_tasks', '').endswith('peer-groups-before.yml'))
        save = next(index for index, task in enumerate(block) if 'ansible.builtin.copy' in task)
        delete = next(index for index, task in enumerate(block) if task.get('ansible.builtin.uri', {}).get('method') == 'DELETE')
        self.assertLess(pin, save)
        self.assertLess(save, delete)

    def test_dns_controller_companion_and_private_ssh_order_with_failure_stop(self):
        publication = yaml.safe_load((TASKS / 'publish.yml').read_text())
        order = []
        for task in publication:
            role = task.get('ansible.builtin.include_role', {}).get('name')
            include = task.get('ansible.builtin.include_tasks', '')
            if role in ('netbird_dns', 'security'): order.append(role)
            elif include.endswith('refresh-management.yml'): order.append('controller')
            elif include.endswith('refresh-companion.yml'): order.append('companion')
        self.assertEqual(order, ['netbird_dns', 'controller', 'companion', 'security'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for fail in (False, True):
                tasks = []
                for index, phase in enumerate(order):
                    target = root / phase
                    target.unlink(missing_ok=True)
                    program = 'from pathlib import Path; import sys; '
                    if index: program += 'assert Path(sys.argv[1]).exists(); '
                    program += 'Path(sys.argv[2]).touch(); '
                    if phase == 'companion' and fail: program += 'sys.exit(1)'
                    args = ['python3', '-c', program, str(root / order[index-1]), str(target)]
                    if phase == 'controller':
                        tasks.append({'ansible.builtin.include_tasks': str(TASKS / 'refresh-management.yml')})
                        command = args
                    else: tasks.append({'ansible.builtin.command': {'argv': args}})
                result = run_play({'cms_environment': 'test', 'host_recovery_management_refresh_argv': command}, tasks)
                self.assertEqual(result.returncode == 0, not fail, result.stdout + result.stderr)
                self.assertEqual((root / 'security').exists(), not fail)

    def test_companion_tasks_retry_safely_and_stop_cutover_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            restart = root / 'restarted'
            cutover = root / 'cutover'
            status_program = root / 'status.py'
            values = dict(hou1_backup_receiver_ip='100.91.0.22', netbird_management_url='https://vpn.example.invalid',
                          host_recovery_companion={'inventory_host': 'hou4-recovery', 'public_ip': '8.8.8.8',
                                                   'known_hosts_file': '/private/known_hosts'})
            for scenario in ('success', 'wrong-peer', 'wrong-management', 'restart-failure', 'not-connected'):
                with self.subTest(scenario=scenario):
                    restart.unlink(missing_ok=True)
                    cutover.unlink(missing_ok=True)
                    status = dict(netbirdIp='100.91.0.23' if scenario == 'wrong-peer' else '100.91.0.22',
                                  management=dict(url='https://other.example.invalid' if scenario == 'wrong-management'
                                                  else 'https://vpn.example.invalid', connected=scenario != 'not-connected'))
                    status_program.write_text('import json\nprint(' + repr(json.dumps(status)) + ')\n')
                    # Exercise production includes, assertions, retry and failure propagation;
                    # replace only host actions with harmless local fixture commands.
                    status_tasks = yaml.safe_load((TASKS / 'companion-status.yml').read_text())
                    command = status_tasks[0]
                    command['ansible.builtin.command']['argv'] = ['python3', str(status_program)]
                    command['delegate_to'] = 'localhost'
                    command['become'] = False
                    command['vars']['ansible_connection'] = 'local'
                    command['retries'] = 1
                    command['delay'] = 0
                    (root / 'companion-status.yml').write_text(yaml.safe_dump(status_tasks))
                    refresh = yaml.safe_load((TASKS / 'companion-refresh.yml').read_text())
                    action = refresh[0]['block'][1]
                    del action['ansible.builtin.systemd']
                    action['ansible.builtin.command'] = {'argv': ['python3', '-c',
                        'from pathlib import Path; import sys; Path(sys.argv[1]).touch(); sys.exit(int(sys.argv[2]))',
                        str(restart), '1' if scenario == 'restart-failure' else '0']}
                    action['delegate_to'] = 'localhost'
                    action['become'] = False
                    action['vars']['ansible_connection'] = 'local'
                    (root / 'refresh.yml').write_text(yaml.safe_dump(refresh))
                    tasks = [{'ansible.builtin.include_tasks': str(root / 'refresh.yml')},
                             {'ansible.builtin.file': {'path': str(cutover), 'state': 'touch'}}]
                    if scenario == 'success':
                        # A repeated refresh keeps the same identity and can finish again.
                        tasks.insert(1, {'ansible.builtin.include_tasks': str(root / 'refresh.yml')})
                    result = run_play(values, tasks)
                    self.assertEqual(result.returncode == 0, scenario == 'success', result.stdout + result.stderr)
                    self.assertEqual(cutover.exists(), scenario == 'success')
                    self.assertEqual(restart.exists(), scenario not in ('wrong-peer', 'wrong-management'))
            # Existing recovery inventories retain their no-refresh default.
            result = run_play({}, [{'ansible.builtin.include_tasks': str(TASKS / 'refresh-companion.yml')}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_transport_configuration_stops_before_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'published'
            preflight = {'ansible.builtin.include_tasks': str(TASKS / 'transport-preflight.yml')}
            for values in ({'host_recovery_management_refresh_argv': 'shell command', 'cms_environment': 'test'},
                           {'host_recovery_renewal_handoff': 'true'},
                           {'host_recovery_companion': {'inventory_host': 'localhost', 'public_ip': '8.8.8.8',
                                                        'known_hosts_file': '/private/known_hosts'}}):
                result = run_play(values, [preflight, {'ansible.builtin.file': {'path': str(marker), 'state': 'touch'}}])
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())

    def test_companion_status_identity_checks_and_fixed_restart_scope(self):
        status = yaml.safe_load((TASKS / 'companion-status.yml').read_text())
        guard = status[-1]
        value = dict(netbirdIp='100.91.0.22/16', management=dict(url='https://vpn.example.invalid', connected=True))
        variables = dict(hou1_backup_receiver_ip='100.91.0.22', netbird_management_url='https://vpn.example.invalid',
                         host_recovery_companion_require_connected=True)
        cases = [value, {**value, 'netbirdIp': '100.91.0.23'},
                 {**value, 'management': dict(url='https://foreign.example.invalid')}]
        result = run_plays([guard_play({**variables, 'host_recovery_companion_status': {'stdout': json.dumps(candidate)}},
                                      [guard], guard['name'] if index else None)
                            for index, candidate in enumerate(cases)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        refresh = yaml.safe_load((TASKS / 'refresh-companion.yml').read_text())[0]
        restart = yaml.safe_load((TASKS / 'companion-refresh.yml').read_text())[0]['block'][1]
        self.assertEqual(restart['ansible.builtin.systemd'], {'name': 'netbird', 'state': 'restarted'})
        self.assertEqual(restart['delegate_to'], '{{ host_recovery_companion.inventory_host }}')
        self.assertIn('StrictHostKeyChecking=yes', restart['vars']['ansible_ssh_args'])
        self.assertIn('ProxyCommand=none', restart['vars']['ansible_ssh_args'])
        self.assertIn('ControlMaster=no', restart['vars']['ansible_ssh_args'])
        self.assertIn('host_recovery_companion is defined', refresh['when'])
