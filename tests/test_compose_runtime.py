"""Static input changes, repair and failure retries against the host activation helper."""
import copy
import grp
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import yaml

from helpers import ROLES, run_play

SPEC = importlib.util.spec_from_file_location('compose_runtime', ROLES / 'compose_runtime/files/apply.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class ComposeRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.template = self.root / 'nginx.conf.template'
        self.template.write_text('first template')
        self.directory = self.root / 'ingress'
        self.directory.mkdir()
        self.document = {'services': {
            'nginx': {'image': 'nginx:pinned', 'environment': {'SECRET': 'private-value'}, 'volumes': [
                {'type': 'bind', 'source': str(self.template), 'read_only': True},
                {'type': 'bind', 'source': str(self.directory), 'read_only': True}]},
            'astro-app': {'image': 'app@sha256:pinned'},
            'uptime-kuma': {'image': 'uptime:pinned'}}}
        self.ids = {name: 'old-' + name for name in self.document['services']}
        self.commands = []
        self.failure = None
        self.compose_replaces = []
        self.counter = 0
        self.runner = patch.object(runtime, 'run', side_effect=self.command)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def command(self, command, directory):
        self.assertEqual(directory, self.root)
        self.commands.append(command)
        if self.failure and self.failure(command):
            raise subprocess.CalledProcessError(1, command)
        args = command[2:]
        if args[:2] == ['config', '--format']:
            return json.dumps(self.document).encode()
        if args[:2] == ['ps', '--quiet']:
            return self.ids.get(args[-1], '').encode()
        if args[0] == 'up':
            self.counter += 1
            selected = args[args.index('120') + 1:]
            selected = [name for name in selected if not name.startswith('--')] or list(self.document['services'])
            for name in selected:
                if '--force-recreate' in args or not self.ids.get(name) or name in self.compose_replaces:
                    self.ids[name] = f'new-{self.counter}-{name}'
            return b''
        if args[:4] == ['exec', '-T', 'nginx', 'nginx']:
            return b''
        raise AssertionError(command)

    def reconcile(self, selected=None):
        return runtime.reconcile(self.root, ['docker', 'compose'], selected, validate_nginx=True)

    def forced(self):
        return [command[command.index('120') + 1:] for command in self.commands if '--force-recreate' in command]

    def state(self):
        return (self.root / '.compose-applied-inputs.json').read_bytes()

    def test_existing_adoption_then_noop_and_fresh_initial_start(self):
        self.assertTrue(self.reconcile())
        self.assertEqual(self.forced(), [['astro-app', 'nginx', 'uptime-kuma']])
        state = self.state()
        self.commands.clear()
        self.assertFalse(self.reconcile())
        self.assertEqual(self.forced(), [])
        self.assertEqual(self.state(), state)
        self.assertNotIn(b'private-value', state)
        self.assertEqual((self.root / '.compose-applied-inputs.json').stat().st_mode & 0o777, 0o600)
        (self.root / '.compose-applied-inputs.json').unlink()
        self.ids.clear()
        self.commands.clear()
        self.assertTrue(self.reconcile())
        self.assertEqual(self.forced(), [])

    def test_atomic_template_change_recreates_only_nginx(self):
        self.reconcile()
        original = copy.deepcopy(self.ids)
        replacement = self.root / 'new-template'
        replacement.write_text('new ACL')
        replacement.replace(self.template)
        self.commands.clear()
        self.reconcile()
        self.assertEqual(self.forced(), [['nginx']])
        self.assertNotEqual(self.ids['nginx'], original['nginx'])
        self.assertEqual(self.ids['astro-app'], original['astro-app'])
        self.assertEqual(self.ids['uptime-kuma'], original['uptime-kuma'])

    def test_environment_and_network_changes_target_only_their_consumer(self):
        self.reconcile()
        self.document['services']['nginx']['environment']['SECRET'] = 'rotated-private-value'
        self.commands.clear()
        self.reconcile()
        self.assertEqual(self.forced(), [['nginx']])
        self.document['services']['nginx']['networks'] = {'ingress': {}}
        self.document['networks'] = {'ingress': {'name': 'first-network'}}
        self.reconcile()
        self.compose_replaces = []
        self.document['networks']['ingress']['name'] = 'replacement-network'
        self.commands.clear()
        self.reconcile()
        self.assertEqual(self.forced(), [['nginx']])

    def test_changed_input_replaces_unhealthy_old_container_before_waiting(self):
        self.reconcile()
        self.template.write_text('repaired input')
        self.commands.clear()
        self.reconcile()
        ups = [command for command in self.commands if 'up' in command]
        self.assertIn('--force-recreate', ups[0])
        self.assertNotIn('--force-recreate', ups[1])

    def test_failed_lab_dns_preparation_blocks_activation_and_preserves_applied_inputs(self):
        self.reconcile()
        previous = self.state()
        self.template.write_text('new input')
        policy = self.root / 'dns-policy.json'; policy.write_text('{}')
        self.commands.clear()
        with patch.object(runtime, 'LAB_DNS_POLICY', policy), patch.object(runtime.runpy, 'run_path') as loader:
            prepare = loader.return_value['prepare_compose']
            prepare.side_effect = ValueError('DNS access not ready')
            with self.assertRaises(ValueError): self.reconcile()
            self.assertFalse(any('up' in command for command in self.commands))
            self.assertEqual(self.state(), previous)
            prepare.side_effect = None
            self.assertTrue(self.reconcile())
            prepare.assert_called_with(self.root, ['docker', 'compose'], self.document, None, runtime.run)
        self.assertNotEqual(self.state(), previous)

    def test_directory_contents_and_writable_data_do_not_trigger_recreate(self):
        data = self.root / 'database'
        data.write_text('initial data')
        self.document['services']['astro-app']['volumes'] = [
            {'type': 'bind', 'source': str(data), 'read_only': False}]
        self.reconcile()
        data.write_text('new data')
        (self.directory / 'authentik.conf').write_text('reloadable ingress')
        self.commands.clear()
        self.assertFalse(self.reconcile())
        self.assertEqual(self.forced(), [])

    def test_stopped_missing_and_bootstrap_service_selection(self):
        self.ids.clear()
        self.reconcile(['nginx'])
        self.assertEqual(set(json.loads(self.state())['services']), {'nginx'})
        normal = next(command for command in self.commands if 'up' in command)
        self.assertEqual(normal[-1], 'nginx')
        self.assertNotIn('--no-deps', normal)
        self.reconcile()
        for name in ('nginx', 'astro-app'):
            self.ids.pop(name)
            self.commands.clear()
            self.assertTrue(self.reconcile())
            self.assertTrue(self.ids[name])
            self.assertEqual(self.forced(), [])

    def test_failure_preserves_state_and_retries_static_inputs(self):
        self.reconcile()
        old = self.state()
        self.template.write_text('new template')
        for failure in (lambda args: 'config' in args, lambda args: '--force-recreate' in args,
                        lambda args: 'exec' in args):
            self.failure = failure
            with self.assertRaises(subprocess.CalledProcessError):
                self.reconcile()
            self.assertEqual(self.state(), old)
        self.failure = None
        self.commands.clear()
        self.reconcile()
        self.assertEqual(self.forced(), [['nginx']])
        self.assertNotEqual(self.state(), old)
        self.commands.clear()
        self.assertFalse(self.reconcile())

    def test_missing_or_symlink_static_file_refused_before_mutation(self):
        self.template.unlink()
        for link in (False, True):
            if link:
                self.template.symlink_to(self.root / 'absent')
            self.commands.clear()
            with self.assertRaises((FileNotFoundError, ValueError)):
                self.reconcile()
            self.assertFalse(any('up' in command for command in self.commands))

    def test_boot_activation_waits_for_exact_vpn_address_before_any_mutation(self):
        command = ['ip', '-j', '-4', 'address', 'show', 'dev', 'wt0']
        with patch.object(runtime.subprocess, 'check_output', return_value=json.dumps([
                {'addr_info': [{'family': 'inet', 'local': '100.91.0.11'}]}]).encode()) as probe:
            with self.assertRaises(ValueError):
                runtime.require_address('100.91.0.10', 'wt0')
            probe.assert_called_once_with(command, stderr=subprocess.DEVNULL, timeout=10)
        with patch.object(runtime.subprocess, 'check_output', return_value=json.dumps([
                {'addr_info': [{'family': 'inet', 'local': '100.91.0.10'}]}]).encode()):
            runtime.require_address('100.91.0.10', 'wt0')

        arguments = ['apply.py', str(self.root), '--require-address', '100.91.0.10',
                     '--interface', 'wt0']
        with patch.object(sys, 'argv', arguments), patch.object(runtime, 'require_address') as ready:
            ready.side_effect = ValueError('address not present')
            with self.assertRaises(ValueError):
                runtime.main()
            self.assertEqual(self.commands, [])
            self.assertFalse((self.root / '.compose-apply.lock').exists())
            self.assertFalse((self.root / '.compose-applied-inputs.json').exists())
            ready.side_effect = None
            runtime.main()
            self.assertTrue((self.root / '.compose-applied-inputs.json').exists())

    def test_address_gate_requires_both_arguments(self):
        for option, value in [('--require-address', '100.91.0.10'), ('--interface', 'wt0')]:
            with patch.object(sys, 'argv', ['apply.py', str(self.root), option, value]):
                with self.assertRaises(SystemExit) as error:
                    runtime.main()
                self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.commands, [])

    def test_boot_unit_retries_without_holding_docker_or_ingress_startup(self):
        target = self.root / 'hou1-webstack.service'
        task = yaml.safe_load((ROLES / 'webstack/tasks/startup.yml').read_text())[0]
        task['ansible.builtin.template'].update(
            src=str(ROLES / 'webstack/templates/hou1-webstack.service.j2'), dest=str(target),
            owner=pwd.getpwuid(os.getuid()).pw_name, group=grp.getgrgid(os.getgid()).gr_name)
        result = run_play({'app_directory': '/srv/application space%name', 'netbird_ip': '100.91.0.10',
                           'netbird_interface': 'wt0'}, [task])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unit = target.read_text()
        self.assertIn('Restart=on-failure', unit)
        self.assertIn('StartLimitIntervalSec=0', unit)
        self.assertIn('"/srv/application space%%name"', unit)
        self.assertIn('--require-address "100.91.0.10" --interface "wt0"', unit)
        self.assertNotIn('ExecStop=', unit)
        self.assertNotIn('Before=', unit)
        target.write_text(unit.replace('/usr/local/libexec/hou1-compose-apply', '/usr/bin/true'))
        for dependency in ('docker', 'netbird'):
            (self.root / (dependency + '.service')).write_text('[Service]\nExecStart=/usr/bin/true\n')
        verified = subprocess.run(['systemd-analyze', 'verify', str(target)],
                                  capture_output=True, text=True, timeout=15)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        deployment = yaml.safe_load((ROLES / 'webstack/tasks/deploy.yml').read_text())
        startup = next(task for task in deployment if task.get('ansible.builtin.include_tasks') == 'startup.yml')
        self.assertEqual(startup['when'], 'not (webstack_bootstrap_ingress_only | default(false) | bool)')
        enable = yaml.safe_load((ROLES / 'webstack/tasks/startup.yml').read_text())[1]
        self.assertTrue(enable['ansible.builtin.systemd_service']['enabled'])
        self.assertNotIn('state', enable['ansible.builtin.systemd_service'])

    def test_fresh_webstack_prepares_readonly_challenge_directory_before_activation(self):
        challenge = self.root / 'certbot/www'
        self.document['services']['nginx']['volumes'].append(
            {'type': 'bind', 'source': str(challenge), 'read_only': True})
        self.ids.clear()
        with self.assertRaises(FileNotFoundError):
            self.reconcile(['nginx'])
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/tls.yml').read_text())
        task = next(task for task in tasks
                    if '{{ app_directory }}/certbot/www' in task.get('loop', [])
                    and task.get('ansible.builtin.file', {}).get('state') == 'directory')
        task['ansible.builtin.file'].update(
            owner=pwd.getpwuid(os.getuid()).pw_name,
            group=grp.getgrgid(os.getgid()).gr_name)
        result = run_play({'app_directory': str(self.root)}, [task])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(challenge.is_dir())
        self.assertTrue(self.reconcile(['nginx']))
        self.assertFalse(self.reconcile(['nginx']))

    def test_server_config_recreates_server_only(self):
        self.document = {'services': {
            'netbird-server': self.document['services']['nginx'],
            'dashboard': {'image': 'dashboard:pinned'}}}
        runtime.reconcile(self.root, ['docker', 'compose'])
        self.template.write_text('updated server configuration')
        self.commands.clear()
        runtime.reconcile(self.root, ['docker', 'compose'])
        self.assertEqual(self.forced(), [['netbird-server']])


@unittest.skipUnless(shutil.which('docker'), 'Docker is needed for the disposable Compose fixture')
class ComposeRuntimeIntegrationTest(unittest.TestCase):
    def test_real_noop_atomic_rotation_environment_and_stopped_service(self):
        image = 'nginx:1.28.0-alpine'
        if subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, timeout=15).returncode:
            self.skipTest('Pinned fixture image is not cached locally')
        with tempfile.TemporaryDirectory(prefix='hou1-apply-') as folder:
            root = Path(folder)
            root.chmod(0o755)
            config = root / 'input.txt'
            config.write_text('first')
            project = 'hou1-apply-' + uuid.uuid4().hex[:10]
            compose = ['docker', 'compose', '--project-name', project]
            document = {'services': {name: {
                'image': image, 'entrypoint': ['/bin/sh', '-c'],
                'command': ['trap "exit 0" TERM; while :; do sleep 1; done'],
                'environment': {'FIXTURE': 'first'},
                'volumes': [str(config) + ':/input.txt:ro'] if name == 'ingress' else []}
                for name in ('ingress', 'application')}}
            import yaml
            def save():
                (root / 'compose.yml').write_text(yaml.safe_dump(document))
            def execute(*argv):
                return subprocess.check_output(compose + list(argv), cwd=root, stderr=subprocess.DEVNULL,
                                               timeout=60).decode().strip()
            try:
                save()
                self.assertTrue(runtime.reconcile(root, compose))
                before = runtime.container_ids(compose, root, document['services'])
                self.assertFalse(runtime.reconcile(root, compose))
                self.assertEqual(runtime.container_ids(compose, root, before), before)
                replacement = root / 'replacement'
                replacement.write_text('second')
                os.replace(replacement, config)
                self.assertTrue(runtime.reconcile(root, compose))
                after = runtime.container_ids(compose, root, before)
                self.assertNotEqual(after['ingress'], before['ingress'])
                self.assertEqual(after['application'], before['application'])
                self.assertEqual(execute('exec', '-T', 'ingress', 'cat', '/input.txt'), 'second')
                document['services']['ingress']['environment']['FIXTURE'] = 'second'
                save()
                self.assertTrue(runtime.reconcile(root, compose))
                self.assertEqual(execute('exec', '-T', 'ingress', 'printenv', 'FIXTURE'), 'second')
                execute('stop', '-t', '2', 'ingress')
                self.assertTrue(runtime.reconcile(root, compose))
                self.assertTrue(execute('ps', '--quiet', 'ingress'))
                execute('rm', '--stop', '--force', 'application')
                self.assertTrue(runtime.reconcile(root, compose))
                self.assertTrue(execute('ps', '--quiet', 'application'))
            finally:
                subprocess.run(compose + ['down', '--remove-orphans', '--timeout', '2'], cwd=root,
                               capture_output=True, timeout=60, check=True)
