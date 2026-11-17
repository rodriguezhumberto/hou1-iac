"""Local Ansible/OpenSSH fixtures and read-only probe tests; no managed hosts."""

import copy
from contextlib import redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import yaml

from helpers import ROOT

sys.path.insert(0, str(ROOT / 'scripts'))
RUNTIME_PROGRAM = (ROOT / 'scripts/lab_runtime.py').read_text()

SPEC = importlib.util.spec_from_file_location('lab_workflow', ROOT / 'scripts/deploy-lab.py')
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


class LabWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.identity = Path(cls.keys.name) / 'operator'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.identity)], check=True)
        cls.public = cls.identity.with_suffix('.pub').read_text().strip()
        cls.fingerprint = subprocess.check_output(['ssh-keygen', '-lf', str(cls.identity)], text=True).split()[1]

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.config = json.loads((ROOT / 'examples/lab-workflow.json').read_text())
        # Preserve coverage of the original public workflow contract.
        self.config.pop('release')
        self.config.update(version=1, application={'revision': '1' * 40,
            'image': 'ghcr.io/example/application/app@sha256:' + '2' * 64})
        for host in self.config['hosts'].values():
            host.pop('capabilities')
        self.config.update(ansible_bin_directory=str(Path(shutil.which('ansible-playbook')).parent),
                           collections_path=str(self.root), age_key_file=str(self.private('agekey', 'fixture')),
                           run_directory=str(self.root / 'runs'))
        Path(self.config['run_directory']).mkdir(mode=0o700)
        for service in workflow.SERVICES:
            host = self.config['hosts'][service]
            repo = self.root / service
            playbooks = repo / 'ansible/playbooks'
            (playbooks / 'tasks').mkdir(parents=True)
            # The guards' environment variable names differ between repos.
            guard = (ROOT / 'ansible/playbooks/tasks/validate-target.yml').read_text()
            if service == 'hou4':
                guard = guard.replace('cms_environment', 'hou4_environment').replace('hou1_', 'hou4_')
            (playbooks / 'tasks/validate-target.yml').write_text(guard)
            (playbooks / (service + '.yml')).write_text('[]\n')
            (repo / 'ansible/ansible.cfg').write_text('[defaults]\nvars_plugins_enabled=host_group_vars\n')
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            'commit', '-qm', 'fixture'], check=True)
            known = self.private(service + '-known_hosts', host['ssh_host_ip'] + ' ' + self.public + '\n')
            ssh = self.private(service + '-ssh_config', f'Host {service}-lab\n HostName {host["ssh_host_ip"]}\n'
                f' User fixture\n StrictHostKeyChecking yes\n IdentitiesOnly yes\n IdentityFile {self.identity}\n'
                f' UserKnownHostsFile {known}\n')
            host.update(repository=str(repo), revision=subprocess.check_output(
                ['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
                inventory=str(self.private(service + '-inventory.yml', 'all: {}\n')), inputs=[],
                ssh=['ssh', '-F', str(ssh), service + '-lab'], ssh_fingerprint=self.fingerprint)
        self.input = self.private('workflow.json', json.dumps(self.config))
        self.patch_root = patch.object(workflow, 'ROOT', Path(self.config['hosts']['hou1']['repository']))
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def private(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o600)
        return path

    def load(self):
        self.input.write_text(json.dumps(self.config))
        return workflow.load_config(str(self.input))

    def test_schema_rejects_production_bootstrap_and_incomplete_contracts(self):
        self.load()
        original = copy.deepcopy(self.config)
        for change in ('environment', 'address', 'release', 'redirect', 'url', 'missing'):
            self.config = copy.deepcopy(original)
            if change == 'environment': self.config['environment'] = 'production'
            if change == 'address': self.config['hosts']['hou4']['ssh_host_ip'] = '192.168.0.152'
            if change == 'release': self.config['application']['revision'] = 'main'
            if change == 'redirect': self.config['https_checks'][0]['status'] = 302
            if change == 'url': self.config['https_checks'][0]['url'] = 'https://auth.example.com/'
            if change == 'missing': del self.config['hosts']['hou4']
            with self.subTest(change=change), self.assertRaises(workflow.WorkflowError):
                self.load()

    def test_checkout_and_input_drift_are_detected(self):
        host = self.config['hosts']['hou1']
        workflow.check_revision(host)
        before = workflow.input_fingerprints(host)
        Path(host['inventory']).write_text('all: {vars: {changed: true}}\n')
        self.assertNotEqual(before, workflow.input_fingerprints(host))
        group_vars = self.root / 'group_vars'; group_vars.mkdir()
        ciphertext = self.private('secret-fixture.enc.yaml', 'first ciphertext')
        (group_vars / 'test_env.sops.yml').symlink_to(ciphertext)
        before = workflow.input_fingerprints(host)
        ciphertext.write_text('second ciphertext')
        self.assertNotEqual(before, workflow.input_fingerprints(host))
        repo = Path(host['repository'])
        (repo / 'docs/mockups').mkdir(parents=True)
        (repo / 'docs/mockups/user-note.md').write_text('user-owned')
        workflow.check_revision(host)
        (repo / 'ansible/untracked.yml').write_text('[]\n')
        with self.assertRaises(workflow.WorkflowError): workflow.check_revision(host)
        (repo / 'ansible/untracked.yml').unlink()
        (repo / 'ansible/ansible.cfg').write_text('[defaults]\nhost_key_checking=False\n')
        with self.assertRaises(workflow.WorkflowError): workflow.check_revision(host)

    def test_image_repository_is_private_configuration_and_still_requires_a_digest(self):
        reference = 'ghcr.io/another-org/internal/service@sha256:' + 'a' * 64
        self.config['application']['image'] = reference
        self.load()
        for invalid in ('ghcr.io/example/application:latest', reference.replace('ghcr.io', 'registry.example.invalid'),
                        reference[:-1], reference.replace('/internal/', '/../'),
                        'https://' + reference, reference + '?token=fixture'):
            with self.subTest(reference=invalid):
                self.config['application']['image'] = invalid
                with self.assertRaises(workflow.WorkflowError): self.load()

    def test_ssh_alias_is_frozen_for_inventory_ip_and_wrong_key_is_rejected(self):
        host = self.config['hosts']['hou1']
        fixed, user, port = workflow.transport(host)
        values = dict(line.split(' ', 1) for line in subprocess.check_output(
            ['ssh', '-G', *fixed[1:], host['ssh_host_ip']], text=True, stderr=subprocess.DEVNULL).splitlines()
            if ' ' in line)
        self.assertEqual(user, 'fixture')
        self.assertEqual(port, 22)
        self.assertEqual(values['hostname'], host['ssh_host_ip'])
        self.assertEqual(values['stricthostkeychecking'], 'true')
        self.assertEqual(values['identityfile'], str(self.identity))
        self.assertEqual(values['controlmaster'], 'false')
        host['ssh_fingerprint'] = 'SHA256:' + 'A' * 43
        with self.assertRaises(ValueError): workflow.transport(host)

    def inventory(self, service, **changes):
        host = self.config['hosts'][service]
        values = {'ansible_host': host['ssh_host_ip'], 'ansible_user': 'fixture',
            'node_bootstrap': False, 'netbird_ip': host['ssh_host_ip'], 'netbird_subnet': '100.91.0.0/16',
            'netbird_interface': 'wt0', 'netbird_management_url': self.config['management_url'],
            'netbird_server_group': 'test-servers', 'cms_environment': 'test', 'hou4_environment': 'test',
            'application_image_revision': self.config['application']['revision'],
            'application_image_reference': self.config['application']['image'],
            'app_directory': '/opt/lab-runtime', 'app_source_directory': '/opt/lab-source',
            'hou1_controller_managed_tls': True}
        values.update(changes)
        Path(host['inventory']).write_text(yaml.safe_dump({'all': {'children': {'test_env': {
            'hosts': {host['target']: values}}}}}))

    def test_real_ansible_guards_reject_retargeting_and_bootstrap_without_remote_access(self):
        cases = [('hou1', {}), ('hou4', {}), ('hou1', {'node_bootstrap': True}),
                 ('hou1', {'cms_environment': 'production'}), ('hou4', {'hou4_environment': 'production'}),
                 ('hou1', {'ansible_host': '100.91.0.99'}), ('hou1', {'ansible_user': 'root'}),
                 ('hou1', {'application_image_revision': '0' * 40})]
        for index, (service, changes) in enumerate(cases):
            with self.subTest(service=service, changes=changes):
                self.inventory(service, **changes)
                run = self.root / ('run-' + str(index)); run.mkdir(mode=0o700)
                runner = workflow.Workflow(self.config, run)
                with patch.object(runner, 'probe') as probe:
                    if changes:
                        with self.assertRaises(workflow.WorkflowError): runner.preflight(service)
                        probe.assert_not_called()
                    else:
                        runner.preflight(service)
                        probe.assert_called_once_with(service, verify=False)
                        self.assertEqual(runner.selections[service]['interface'], 'wt0')

    def test_no_selected_host_cannot_pass_preflight(self):
        run = self.root / 'missing-target'; run.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, run)
        with patch.object(runner, 'probe') as probe, self.assertRaises(workflow.WorkflowError):
            runner.preflight('hou1')
        probe.assert_not_called()

    def test_production_group_membership_stops_before_access_probe(self):
        self.inventory('hou1')
        host = self.config['hosts']['hou1']
        inventory = yaml.safe_load(Path(host['inventory']).read_text())
        inventory['all']['children']['prod_env'] = {'hosts': {host['target']: {}}}
        Path(host['inventory']).write_text(yaml.safe_dump(inventory))
        run = self.root / 'production-member'; run.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, run)
        with patch.object(runner, 'probe') as probe, self.assertRaises(workflow.WorkflowError):
            runner.preflight('hou1')
        probe.assert_not_called()

    def test_failure_stops_handoff_and_retry_rechecks_both_hosts(self):
        rejected = self.root / 'rejected'; rejected.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, rejected)
        with patch.object(runner, 'preflight', side_effect=[None, workflow.WorkflowError('HOU4 inaccessible')]), \
                patch.object(runner, 'playbook') as deploy:
            with self.assertRaises(workflow.WorkflowError): runner.execute('reconcile')
            deploy.assert_not_called()
        first = self.root / 'first'; first.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, first)
        with patch.object(runner, 'preflight'), patch.object(runner, 'probe'), patch.object(
                runner, 'playbook', side_effect=workflow.WorkflowError('fixture failed')) as deploy:
            with self.assertRaises(workflow.WorkflowError): runner.execute('reconcile')
            self.assertEqual(deploy.call_count, 1)
        receipt = json.loads((first / 'receipt.json').read_text())
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual([r['step'] for r in receipt['steps']], ['hou1-preflight', 'hou4-preflight', 'hou1-reconcile'])
        self.assertEqual(receipt['steps'][-1]['status'], 'failed')
        self.assertEqual((first / 'receipt.json').stat().st_mode & 0o777, 0o600)
        retry = self.root / 'retry'; retry.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, retry)
        with patch.object(runner, 'preflight') as preflight, patch.object(runner, 'probe'), patch.object(runner, 'playbook'):
            runner.execute('reconcile')
            self.assertEqual([c.args[0] for c in preflight.call_args_list], ['hou1', 'hou4'])
        self.assertEqual(json.loads((retry / 'receipt.json').read_text())['status'], 'passed')
        self.assertEqual(receipt, json.loads((first / 'receipt.json').read_text()))

    def test_plan_and_verify_do_not_invoke_deployment(self):
        self.load()
        with patch.object(workflow.Workflow, 'execute') as execute:
            self.assertEqual(workflow.main(['--config', str(self.input)]), 0)
            execute.assert_not_called()
        run = self.root / 'verify'; run.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, run)
        with patch.object(runner, 'preflight'), patch.object(runner, 'probe') as probe, patch.object(runner, 'playbook') as deploy:
            runner.execute('verify')
            self.assertEqual(probe.call_count, 2)
            deploy.assert_not_called()

    def test_workflow_rejects_input_drift_before_invoking_ansible(self):
        host = self.config['hosts']['hou1']
        run = self.root / 'drift'; run.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, run)
        runner.transports['hou1'] = workflow.transport(host)
        Path(host['inventory']).write_text('all: {vars: {changed: true}}\n')
        with self.assertRaises(workflow.WorkflowError):
            runner.playbook('hou1', '/does/not/exist.yml', 'must-not-run')
        self.assertFalse((run / 'must-not-run.log').exists())

    def test_public_playbook_is_limited_and_uses_the_verified_transport(self):
        binary = self.root / 'bin'; binary.mkdir()
        invocation = self.root / 'invocation.json'
        program = binary / 'ansible-playbook'
        program.write_text(f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
                           f'Path({str(invocation)!r}).write_text(json.dumps(sys.argv[1:]))\n')
        program.chmod(0o700)
        self.config['ansible_bin_directory'] = str(binary)
        run = self.root / 'invocation'; run.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, run)
        host = self.config['hosts']['hou1']
        runner.transports['hou1'] = workflow.transport(host)
        runner.playbook('hou1', Path(host['repository']) / 'ansible/playbooks/hou1.yml', 'deploy')
        argv = json.loads(invocation.read_text())
        self.assertEqual(argv[argv.index('--limit') + 1], host['target'])
        extra = json.loads(argv[-1])
        self.assertEqual(extra, {'target_hosts': host['target']})
        hosts = {name: json.loads((run / 'host_vars' / (name + '.json')).read_text())
                 for name in (host['target'], 'localhost')}
        self.assertEqual(hosts[host['target']]['ansible_host'], host['ssh_host_ip'])
        self.assertEqual(hosts[host['target']]['ansible_user'], 'fixture')
        self.assertEqual(hosts[host['target']]['ansible_ssh_private_key_file'], str(self.identity))
        self.assertIn('StrictHostKeyChecking=yes', hosts[host['target']]['ansible_ssh_common_args'])
        self.assertEqual(hosts['localhost']['ansible_connection'], 'local')
        self.assertNotIn('--tags', argv)

    def test_frozen_transport_preserves_real_controller_delegation(self):
        self.inventory('hou1', ansible_connection='unusable', ansible_ssh_host='100.91.0.99')
        host = self.config['hosts']['hou1']
        host_vars = Path(host['inventory']).parent / 'host_vars'
        host_vars.mkdir()
        (host_vars / (host['target'] + '.yml')).write_text(
            'ansible_connection: unusable\nansible_ssh_host: 100.91.0.99\n')
        output = self.root / 'controller-result.json'
        play = Path(host['repository']) / 'ansible/playbooks/hou1.yml'
        play.write_text(yaml.safe_dump([{'hosts': host['target'], 'gather_facts': False,
            'tasks': [
                {'ansible.builtin.assert': {'that': ["ansible_connection == 'ssh'",
                    f'ansible_ssh_host == {host["ssh_host_ip"]!r}']}},
                {'ansible.builtin.command': {'argv': [sys.executable, '-c',
                    'import json,socket;print(json.dumps({"hostname":socket.gethostname()}))']},
                 'delegate_to': 'localhost', 'register': 'controller'},
                {'ansible.builtin.copy': {'content': '{{ controller.stdout }}', 'dest': str(output)},
                 'delegate_to': 'localhost'}]}]))
        subprocess.run(['git', '-C', host['repository'], 'add', '.'], check=True)
        subprocess.run(['git', '-C', host['repository'], '-c', 'user.name=Fixture', '-c',
            'user.email=fixture@example.invalid', 'commit', '-qm', 'delegation fixture'], check=True)
        host['revision'] = subprocess.check_output(['git', '-C', host['repository'], 'rev-parse', 'HEAD'], text=True).strip()
        run = self.root / 'delegation'; run.mkdir(mode=0o700)
        runner = workflow.Workflow(self.config, run)
        runner.transports['hou1'] = workflow.transport(host)
        runner.playbook('hou1', play, 'delegated')
        import socket
        self.assertEqual(json.loads(output.read_text())['hostname'], socket.gethostname())

    def test_global_ansible_extra_vars_are_rejected_before_execution(self):
        source = self.private('extra.yml', 'ansible_connection: ssh\n')
        self.config['hosts']['hou1']['inputs'] = [str(source)]
        with self.assertRaisesRegex(workflow.WorkflowError, 'in inventory'):
            self.load()

    def test_invalid_private_yaml_does_not_expose_input_content(self):
        source = self.private('malformed.yml', 'credential: [private-marker\n')
        self.config['hosts']['hou1']['inputs'] = [str(source)]
        self.input.write_text(json.dumps(self.config))
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(workflow.main(['--config', str(self.input)]), 1)
        self.assertNotIn('private-marker', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())

    def test_repository_environment_replaces_ambient_plugin_and_secret_settings(self):
        with patch.dict(os.environ, {'ANSIBLE_LOG_PATH': '/unexpected/log', 'SOPS_AGE_KEY': 'not-the-selected-key'}):
            env = workflow.environment(self.config, self.config['hosts']['hou4'])
        self.assertNotIn('ANSIBLE_LOG_PATH', env)
        self.assertNotIn('SOPS_AGE_KEY', env)
        self.assertEqual(env['ANSIBLE_HOST_KEY_CHECKING'], 'True')
        self.assertEqual(env['SOPS_AGE_KEY_FILE'], self.config['age_key_file'])
        self.assertEqual(env['ANSIBLE_CONFIG'], self.config['hosts']['hou4']['repository'] + '/ansible/ansible.cfg')


class LabRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.state = self.root / 'state.json'
        self.calls = self.root / 'calls.jsonl'
        self.contract = {'hostname': platform.node(), 'address': '100.91.0.10', 'interface': 'wt0',
            'verify': True, 'compose': [{'directory': '/fixture'}], 'tls': True, 'https': [], 'source': '/fixture-source',
            'application': {'revision': '1' * 40, 'image': 'fixture@sha256:' + '2' * 64}}
        binary = self.root / 'command'
        binary.write_text(f'#!{sys.executable}\n' + '''import json,sys
from pathlib import Path
''' + f'state=json.loads(Path({str(self.state)!r}).read_text())\n' +
            f'with Path({str(self.calls)!r}).open("a") as log: log.write(json.dumps(sys.argv)+"\\n")\n' + '''name=Path(sys.argv[0]).name
args=sys.argv[1:]
if name=='ip': print(json.dumps([{'addr_info':[{'local':state.get('address','100.91.0.10')}]}]))
elif name=='sshd': print('passwordauthentication '+state.get('password','no')+'\\nkbdinteractiveauthentication no\\npermitrootlogin no\\nlistenaddress '+state.get('listener','100.91.0.10')+':22')
elif name=='ss': print('LISTEN 0 128 '+state.get('running_listener','100.91.0.10')+':22 0.0.0.0:* users:(("sshd",pid=123,fd=3))')
elif name=='systemctl':
 active=state.get('socket_active',False) if args[-1]=='ssh.socket' else state.get('fallback_active',False)
 print('active' if active else 'inactive'); sys.exit(0 if active else 3)
elif name=='git': print(state.get('revision','1'*40) if args[-1]=='HEAD' else state.get('dirty',''))
elif name=='python3': print('PRIVATE_TLS_DIAGNOSTIC')
elif '--services' in args: print('astro-app')
elif 'ps' in args: print('fixture-id')
elif '.Config.Image' in ' '.join(args): print(json.dumps(state.get('image','fixture@sha256:'+'2'*64)))
else: print(json.dumps({'Running':state.get('running',True),'Health':{'Status':state.get('health','healthy'),'Log':['PRIVATE_HEALTH_DIAGNOSTIC']}}))
''')
        binary.chmod(0o700)
        for name in ('ip', 'git', 'docker', 'python3', 'sshd', 'ss', 'systemctl'):
            (self.root / name).symlink_to(binary)

    def probe(self, **state):
        self.state.write_text(json.dumps(state))
        return subprocess.run([sys.executable, '-c', RUNTIME_PROGRAM], input=json.dumps(self.contract),
            capture_output=True, text=True, timeout=15,
            env={**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH']})

    def test_runtime_checks_release_and_health_without_returning_private_diagnostics(self):
        good = self.probe()
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)['source_clean'])
        self.assertNotIn('PRIVATE_', good.stdout)
        for state in ({'running': False}, {'health': 'unhealthy'}, {'image': 'wrong'},
                      {'revision': '0' * 40}, {'dirty': ' M changed'}, {'address': '100.91.0.99'}):
            with self.subTest(state=state):
                result = self.probe(**state)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {'failed': True})
        self.assertFalse(any(set(json.loads(line)) & {'up', 'down', 'start', 'stop', 'rm'}
                             for line in self.calls.read_text().splitlines()))

    def test_handoff_requires_private_live_listener_and_no_bootstrap_fallback(self):
        self.contract.update(verify=False, handoff=True, service='hou1')
        good = self.probe()
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)['ssh_policy_verified'])
        for state in [{'password': 'yes'}, {'listener': '0.0.0.0'},
                      {'running_listener': '0.0.0.0'}, {'socket_active': True}, {'fallback_active': True}]:
            with self.subTest(state=state):
                result = self.probe(**state)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'failed': True})
        self.contract['service'] = 'hou4'
        self.assertEqual(self.probe().returncode, 0)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(any(call[-1] == 'ansible-hou4-ssh-bootstrap-fallback.timer' for call in calls))
        self.assertFalse(any(Path(call[0]).name in ('docker', 'git') for call in calls))

    def test_https_denial_is_checked_and_redirect_is_not_followed(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302 if self.path == '/redirect' else 403)
                if self.path == '/redirect': self.send_header('Location', '/denied')
                self.end_headers()
                self.wfile.write(b'PRIVATE_RESPONSE_BODY')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            self.contract['https'] = [{'url': url + '/denied', 'status': 403}]
            result = self.probe()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('PRIVATE_', result.stdout)
            self.contract['https'] = [{'url': url + '/redirect', 'status': 403}]
            self.assertNotEqual(self.probe().returncode, 0)
            self.assertEqual(requests, ['/denied', '/redirect'])
        finally:
            server.shutdown(); server.server_close(); thread.join()
