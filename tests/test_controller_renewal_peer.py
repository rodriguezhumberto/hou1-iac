"""Renewal authority, secret cleanup, and privilege dropping at namespace entry."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


peer = load(ROOT / 'scripts/controller-renewal-peer.py', 'renewal_peer')
executor = load(ROOT / 'ansible/playbooks/roles/controller_tls/files/namespace-exec.py', 'renewal_executor')


class RenewalPeerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = {'environment': 'production', 'management_url': 'https://vpn.example.invalid',
                       'server_group': 'environment-servers',
                       'subnet': '100.90.0.0/16', 'targets': ['100.90.0.10', '100.90.0.20'],
                       'runtime': self.directory.name, 'container': 'controller-renewal'}
        self.expected = peer.policy_body(self.config, 'source', 'targets')
        self.details = {'State': {'Running': True, 'Pid': 1234}, 'Config': {'Image': peer.IMAGE},
                        'HostConfig': {'NetworkMode': 'bridge', 'Privileged': False,
                            'CapAdd': ['NET_ADMIN'], 'PortBindings': {},
                            'RestartPolicy': {'Name': 'unless-stopped'},
                            'SecurityOpt': ['no-new-privileges=true']},
                        'Mounts': [{'Source': self.directory.name, 'Destination': '/var/lib/netbird', 'RW': True}]}

    def test_contract_rejects_hosted_or_cross_network_receivers(self):
        peer.validate(self.config)
        dotted = Path(self.directory.name) / '.local'
        dotted.mkdir(mode=0o700)
        peer.validate({**self.config, 'runtime': str(dotted)})
        for field, value in [('management_url', 'https://api.netbird.io'),
                             ('management_url', 'https://vpn.example.invalid/redirect'),
                             ('management_url', 'https://fixture@vpn.example.invalid'),
                             ('targets', ['192.0.2.4']), ('targets', ['100.90.0.10'] * 2),
                             ('targets', ['100.90.0.0']), ('subnet', '10.0.0.0/16'),
                             ('server_group', ''), ('server_group', 'All'),
                             ('runtime', '/tmp/../runtime'), ('container', 'bad;command')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                peer.validate({**self.config, field: value})

    def test_receivers_are_unique_environment_servers(self):
        targets = [{'id': str(i), 'ip': ip, 'groups': [{'name': 'environment-servers'}]}
                   for i, ip in enumerate(self.config['targets'])]
        self.assertEqual(peer.target_peers(self.config, targets), {'0', '1'})
        for broken in [targets + [targets[0]], targets[:1],
                       [{**targets[0], 'groups': [{'name': 'other-servers'}]}, targets[1]]]:
            with self.assertRaises(ValueError):
                peer.target_peers(self.config, broken)

    def test_policy_refuses_extra_ports_membership_direction_or_owner(self):
        peer.require_owned_policy(self.expected, self.expected)
        for key, value in [('ports', ['22', '443']), ('bidirectional', True),
                           ('protocol', 'all'), ('sources', ['source', 'other']),
                           ('destinations', ['targets', 'other']), ('port_ranges', [{'start': 1, 'end': 65535}])]:
            broken = copy.deepcopy(self.expected)
            broken['rules'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                peer.require_owned_policy(broken, self.expected)
        with self.assertRaises(ValueError):
            peer.require_owned_policy({**self.expected, 'description': 'unowned'}, self.expected)

    def test_implicit_all_and_reverse_rules_cannot_broaden_access(self):
        groups = [{'id': 'all', 'name': 'All'}, {'id': 'admin', 'name': 'administrators'}]
        source = {'groups': [{'id': 'source', 'name': 'tls-renewal-controller-production'}]}
        peer.require_isolated_source(source, groups, [self.expected], self.expected)
        broad = copy.deepcopy(self.expected)
        broad.update(name='unrelated', description='unrelated')
        broad['rules'][0]['sources'] = ['all']
        with self.assertRaises(ValueError):
            peer.require_isolated_source(source, groups, [broad], self.expected)
        broad['rules'][0].update(sources=['other'], destinations=['source'], bidirectional=True)
        with self.assertRaises(ValueError):
            peer.require_isolated_source(source, groups, [broad], self.expected)
        # Reusing the ownership marker does not exempt a different policy.
        broad['description'] = peer.OWNER
        with self.assertRaises(ValueError):
            peer.require_isolated_source(source, groups, [broad], self.expected)
        with self.assertRaises(ValueError):
            peer.require_isolated_source({'groups': [{'id': 'admin'}]}, groups, [], self.expected)
        with self.assertRaises(ValueError):
            peer.require_isolated_source({'groups': [{'id': 'source'}, {'id': 'unrelated'}]}, groups, [], self.expected)

    def test_namespace_refuses_host_network_or_extra_mounts_before_start(self):
        peer.require_container(self.details, self.config)
        stopped = copy.deepcopy(self.details)
        stopped['State']['Running'] = False
        peer.require_container(stopped, self.config, running=False)
        for key, value in [('NetworkMode', 'host'), ('Privileged', True), ('CapAdd', ['NET_ADMIN', 'SYS_ADMIN'])]:
            broken = copy.deepcopy(stopped)
            broken['HostConfig'][key] = value
            with self.assertRaises(ValueError):
                peer.require_container(broken, self.config, running=False)
        broken = copy.deepcopy(self.details)
        broken['Mounts'].append({'Source': '/var/run/docker.sock'})
        with self.assertRaises(ValueError):
            peer.require_container(broken, self.config)

    def test_persistent_identity_cannot_expire_or_be_ephemeral(self):
        metadata = dict.fromkeys(('ephemeral', 'inactivity_expiration_enabled',
                                  'login_expiration_enabled', 'login_expired'), False)
        peer.require_persistent_peer(metadata)
        for key in metadata:
            with self.subTest(key=key), self.assertRaises(ValueError):
                peer.require_persistent_peer({**metadata, key: True})
            with self.subTest(missing=key), self.assertRaises(ValueError):
                peer.require_persistent_peer({k: v for k, v in metadata.items() if k != key})

    def test_interrupted_enrollment_removes_both_setup_grants(self):
        calls = []
        groups = [{'id': 'all', 'name': 'All', 'peers': []}]
        targets = [{'id': str(i), 'ip': ip, 'groups': [{'name': 'environment-servers'}]}
                   for i, ip in enumerate(self.config['targets'])]

        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(self.data).encode()

        class Opener:
            def open(self, request, timeout):
                method, path = request.method, request.full_url.split('/api')[1]
                calls.append((method, path))
                if method == 'GET':
                    return Response({'/groups': groups, '/peers': targets, '/policies': []}[path])
                if path == '/groups':
                    data = json.loads(request.data)
                    return Response({**data, 'id': data['name']})
                if method == 'POST' and path == '/setup-keys':
                    return Response({'id': 'fixture-setup', 'key': 'fixture-enrollment-value'})
                if method == 'DELETE': return Response({})
                raise AssertionError('Unexpected API mutation')

        def docker(argv):
            if argv[1] == 'ps': return b''
            if 'up' in argv: raise subprocess.CalledProcessError(1, argv)
            return b''

        with patch.object(peer.urllib.request, 'build_opener', return_value=Opener()), patch.object(peer, 'command', docker):
            with self.assertRaises(subprocess.CalledProcessError):
                peer.provision(self.config, 'fixture-token')
        self.assertFalse((Path(self.directory.name) / '.setup-key').exists())
        self.assertIn(('DELETE', '/setup-keys/fixture-setup'), calls)
        self.assertNotIn(('POST', '/policies'), calls)

    def test_executor_enters_namespace_then_drops_user_groups_and_capabilities(self):
        user = pwd.getpwuid(os.getuid())
        if user.pw_uid == 0:
            user = pwd.getpwnam('nobody')
        program = Path(self.directory.name) / 'controller-tls.py'
        config = Path(self.directory.name) / 'controller.json'
        program.write_text(''); config.write_text('{}')
        program.chmod(0o600); config.chmod(0o600)
        if os.getuid() == 0:
            os.chown(program, user.pw_uid, user.pw_gid); os.chown(config, user.pw_uid, user.pw_gid)
        doc = {'uid': user.pw_uid, 'gid': user.pw_gid, 'jobs': {
            'tls-deploy': {'program': str(program), 'config': str(config), 'mode': '--deploy'}}}
        argv, env = executor.invocation(doc, 'tls-deploy', self.details)
        self.assertEqual(argv[:5], ['/usr/bin/nsenter', '--target', '1234', '--net', '/usr/bin/setpriv'])
        self.assertLess(argv.index('--reuid'), argv.index('/usr/bin/python3'))
        self.assertIn('--no-new-privs', argv)
        self.assertIn('--bounding-set=-all', argv)
        self.assertEqual(env['HOME'], user.pw_dir)
        self.assertEqual(env['USER'], user.pw_name)
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('SOPS_AGE_KEY', env)
        for bad in ['unknown', 'tls-deploy;sh']:
            with self.assertRaises(ValueError): executor.invocation(doc, bad, self.details)
        doc['jobs']['tls-deploy']['mode'] = '--arbitrary-command'
        with self.assertRaises(ValueError): executor.invocation(doc, 'tls-deploy', self.details)

    def test_successful_enrollment_is_idempotent_and_never_adds_admin_membership(self):
        calls, policies = [], []
        groups = [{'id': 'all', 'name': 'All', 'peers': []}]
        peers = [{'id': str(i), 'ip': ip, 'groups': [{'id': 'servers', 'name': 'environment-servers'}]}
                 for i, ip in enumerate(self.config['targets'])]
        created = False

        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(self.data).encode()

        class Opener:
            def open(self, request, timeout):
                method, path = request.method, request.full_url.split('/api')[1]
                calls.append((method, path))
                if path == '/groups' and method == 'POST':
                    data = json.loads(request.data)
                    group = {**data, 'id': data['name'], 'peers': [{'id': value} for value in data['peers']]}
                    groups.append(group)
                    return Response(group)
                if method == 'GET':
                    if path == '/groups': return Response(groups)
                    if path.startswith('/groups/'): return Response(next(g for g in groups if g['id'] == path.split('/')[-1]))
                    if path == '/peers': return Response(peers)
                    if path == '/policies': return Response(policies)
                    if path.startswith('/policies/'): return Response(policies[0])
                if path == '/setup-keys' and method == 'POST':
                    data = json.loads(request.data)
                    self_test.assertEqual(data['auto_groups'], ['tls-renewal-controller-production'])
                    self_test.assertFalse(data['ephemeral'])
                    return Response({'id': 'fixture-setup', 'key': 'fixture-enrollment-value'})
                if path == '/policies' and method == 'POST':
                    policies.append({**json.loads(request.data), 'id': 'policy'})
                    return Response(policies[0])
                if method == 'DELETE': return Response({})
                raise AssertionError('Unexpected API operation')

        def docker(argv):
            nonlocal created
            if argv[1] == 'ps': return b'container' if created else b''
            if argv[1] == 'run':
                self.assertEqual(argv[argv.index('--network') + 1], 'bridge')
                created = True
            if argv[1] == 'inspect': return json.dumps([self.details]).encode()
            if 'up' in argv:
                source = next(g for g in groups if g['name'] == 'tls-renewal-controller-production')
                source['peers'] = [{'id': 'controller'}]
                peers.append({'id': 'controller', 'ip': '100.90.9.9', 'name': self.config['container'],
                              'ephemeral': False, 'inactivity_expiration_enabled': False,
                              'login_expiration_enabled': False, 'login_expired': False,
                              'groups': [{'id': source['id'], 'name': source['name']}]})
            if argv[-1] == 'version': return b'0.82.0\n'
            if '--json' in argv:
                return json.dumps({'management': {'connected': True, 'url': 'https://vpn.example.invalid:443'},
                                   'netbirdIp': '100.90.9.9/16'}).encode()
            if 'cat' in argv:
                return json.dumps({key: True for key in ('BlockInbound', 'DisableDNS', 'DisableClientRoutes', 'DisableServerRoutes')}).encode()
            return b''

        self_test = self
        with patch.object(peer.urllib.request, 'build_opener', return_value=Opener()), patch.object(peer, 'command', docker):
            initial = peer.provision(self.config, 'fixture-token')
            repeat = peer.provision(self.config, 'fixture-token')
        self.assertEqual(initial, repeat)
        self.assertEqual(initial['address'], '100.90.9.9')
        self.assertEqual(calls.count(('POST', '/setup-keys')), 1)
        self.assertEqual(calls.count(('DELETE', '/setup-keys/fixture-setup')), 1)
        self.assertEqual(calls.count(('POST', '/policies')), 1)
        self.assertFalse((Path(self.directory.name) / '.setup-key').exists())
        self.assertNotIn('fixture-token', json.dumps(initial))

    def test_kernel_execution_is_non_root_with_no_privilege_gain(self):
        if os.getuid() == 0:
            self.skipTest('This probe models the non-root controller operator')
        permitted = subprocess.run(['sudo', '-n', 'true'], capture_output=True)
        if permitted.returncode:
            self.skipTest('Namespace entry requires controller sudo capability')
        user = pwd.getpwuid(os.getuid())
        program = Path(self.directory.name) / 'controller-tls.py'
        config = Path(self.directory.name) / 'controller.json'
        program.write_text('import json,os,pathlib\ns=pathlib.Path("/proc/self/status").read_text().splitlines()\n'
                           'print(json.dumps({"uid":os.getuid(),"gid":os.getgid(),'
                           '"flags":[v for v in s if v.startswith(("NoNewPrivs:","CapEff:","CapBnd:"))]}))\n')
        config.write_text('{}'); program.chmod(0o600); config.chmod(0o600)
        doc = {'uid': user.pw_uid, 'gid': user.pw_gid, 'jobs': {
            'tls-deploy': {'program': str(program), 'config': str(config), 'mode': '--deploy'}}}
        container = copy.deepcopy(self.details)
        container['State']['Pid'] = os.getpid()
        argv, environment = executor.invocation(doc, 'tls-deploy', container)
        result = subprocess.run(['sudo', '-n'] + argv, capture_output=True, check=True, env=environment, timeout=10)
        status = json.loads(result.stdout)
        self.assertEqual((status['uid'], status['gid']), (user.pw_uid, user.pw_gid))
        flags = dict(line.split(':', 1) for line in status['flags'])
        self.assertEqual(flags['NoNewPrivs'].strip(), '1')
        self.assertEqual(int(flags['CapEff'].strip(), 16), 0)
        self.assertEqual(int(flags['CapBnd'].strip(), 16), 0)

    def test_scheduler_selects_only_fixed_launcher_jobs(self):
        renderer = Environment(undefined=StrictUndefined)
        root = ROOT / 'ansible/playbooks/roles/controller_tls/templates'
        variables = {'ansible_playbook_python': '/usr/bin/python3',
                     'hou1_tls_schedule_directory': '/private/tls',
                     'hou1_tls_controller_config': '/private/tls/controller.json'}
        for name, suffix in [('renew.service', 'deploy'), ('status.service', 'status')]:
            template = renderer.from_string((root / (name + '.j2')).read_text())
            direct = template.render(**variables)
            self.assertIn('NoNewPrivileges=yes', direct)
            self.assertNotIn('/usr/bin/sudo', direct)
            isolated = template.render(**variables, hou1_tls_executor_job_prefix='hou1-production')
            self.assertIn('/usr/bin/sudo -n /usr/local/libexec/controller-renewal-exec hou1-production-' + suffix, isolated)
            self.assertIn('NoNewPrivileges=no', isolated)
            self.assertNotIn('--config', isolated)
