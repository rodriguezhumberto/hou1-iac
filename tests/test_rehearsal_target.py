"""Use local OpenSSH parsing and synthetic keys; all remote transports are traps."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import ROOT
from identity_fixtures import identity_names, identity_profile

sys.path.insert(0, str(ROOT / 'scripts'))
import ssh_target as target


class RehearsalTargetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.public_keys = []
        for i in range(2):
            path = Path(cls.keys.name) / f'key-{i}'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)],
                           check=True, capture_output=True)
            public = Path(str(path) + '.pub').read_text().strip()
            info = subprocess.check_output(['ssh-keygen', '-lf', str(path), '-E', 'sha256'], text=True)
            cls.public_keys.append((public, info.split()[1]))

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.config = self.destination('server', '100.91.0.10')
        self.config['peer'] = self.destination('peer', '192.0.2.50')
        self.config['staff_probe_command'] = [*self.config['peer']['ssh'],
            'docker exec -i hou1-vps-admin-test python3 /tmp/live-staff-check.py']
        self.config['alert_url'] = 'http://127.0.0.1:19876/alerts'
        self.input = self.root / 'targets.json'
        self.write_json(self.input, self.config)

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def destination(self, name, address, port=22, alias=None):
        known = self.root / (name + '-known_hosts')
        lookup = alias or (address if port == 22 else f'[{address}]:{port}')
        known.write_text(lookup + ' ' + self.public_keys[0][0] + '\n')
        known.chmod(0o600)
        config = self.root / (name + '-ssh_config')
        config.write_text(f'Host {name}\n HostName {address}\n User fixture\n Port {port}\n'
                          f' StrictHostKeyChecking yes\n IdentitiesOnly yes\n'
                          f' UserKnownHostsFile "{known}"\n'
                          + (f' HostKeyAlias {alias}\n' if alias else ''))
        config.chmod(0o600)
        return {'environment': 'test', 'ssh_host_ip': address,
                'ssh_fingerprint': self.public_keys[0][1],
                'ssh': ['ssh', '-F', str(config), name]}

    def test_real_openssh_resolves_pinned_addresses_ports_and_aliases(self):
        for port, alias in ((22, None), (2222, None), (2222, 'verified-lab-server')):
            with self.subTest(port=port, alias=alias):
                config = self.destination('server', '100.91.0.10', port, alias)
                transport = target.pinned_ssh(config)
                resolved = target.local(['ssh', '-G', *transport[1:]])
                values = dict(line.split(' ', 1) for line in resolved.splitlines() if ' ' in line)
                self.assertEqual(values['hostname'], '100.91.0.10')
                self.assertEqual(values['port'], str(port))
                self.assertEqual(values['hostkeyalgorithms'], 'ssh-ed25519')
                self.assertEqual(values['stricthostkeychecking'], 'true')
                self.assertEqual(values.get('controlpath', 'none'), 'none')
                self.assertEqual(values.get('knownhostscommand', 'none'), 'none')
                self.assertEqual(values['globalknownhostsfile'], '/dev/null')
                lookup = alias or ('100.91.0.10' if port == 22 else '[100.91.0.10]:2222')
                self.assertEqual(values['hostkeyalias'], lookup)

    def test_production_and_invalid_declarations_fail_before_ssh_configuration(self):
        for changes in ({'environment': 'production'}, {'ssh_host_ip': '100.90.0.10'},
                        {'ssh_host_ip': '192.0.2.50'}, {'ssh_host_ip': '127.0.0.1'},
                        {'ssh_host_ip': 'not-an-address'}, {'ssh_fingerprint': ''},
                        {'ssh': ['ssh', '-o', 'StrictHostKeyChecking=no', 'server']}):
            with self.subTest(changes=changes), patch.object(target, 'local') as local:
                with self.assertRaises(ValueError):
                    target.pinned_ssh({**self.config, **changes})
                local.assert_not_called()
        with self.assertRaises(ValueError):
            target.pinned_ssh({**self.config['peer'], 'ssh_host_ip': '100.90.0.20'}, peer=True)

    def test_admission_can_select_production_only_through_its_explicit_profile(self):
        config = self.destination('production', '100.90.0.10')
        config['environment'] = 'prod'
        with self.assertRaises(ValueError):
            target.pinned_ssh(config)
        self.assertEqual(target.pinned_ssh(config, environment='prod')[-1], 'production')
        with self.assertRaises(ValueError):
            target.pinned_ssh({**self.config, 'environment': 'prod'}, environment='prod')

    def test_admission_rejects_wrong_ssh_before_token_decryption_or_api_access(self):
        spec = importlib.util.spec_from_file_location('fixture_admission', ROOT / 'scripts/rehearsal-netbird-admission.py')
        admission = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(admission)
        env, marker = self.traps()
        for environment, address, wrong in (('test', '100.91.0.10', '100.90.0.10'),
                                             ('prod', '100.90.0.10', '100.91.0.10')):
            with self.subTest(environment=environment):
                profile = identity_profile(environment, private=False)
                self.write_json(self.input, profile)
                config = self.destination('admission-' + environment, address)
                config.update(environment=environment, netbird_url=profile['netbird_url'],
                    netbird_subnet=profile['netbird_subnet'],
                    authentik_issuer='https://' + profile['issuer_host'] + '/application/o/' + profile['netbird']['client_id'] + '/')
                path = Path(config['ssh'][2])
                path.write_text(path.read_text().replace(address, wrong))
                with patch.dict(os.environ, env), \
                        patch('sys.argv', ['admission', '--config', 'fixture.enc.json', '--profile', str(self.input),
                                           '--identity-secrets', 'names.enc.json', '--age-key-file', 'fixture.key']), \
                        patch.object(admission, 'load_encrypted_config', side_effect=[config,
                            {'IDENTITY_NAMES': identity_names(environment)}]) as decrypt, \
                        patch.object(admission, 'configure') as configure:
                    with self.assertRaisesRegex(ValueError, 'different destination address'):
                        admission.main()
                    self.assertEqual(decrypt.call_count, 2)
                    configure.assert_not_called()
                    self.assertFalse(marker.exists())

    def test_retargeted_alias_or_weakened_ssh_policy_is_rejected(self):
        path = Path(self.config['ssh'][2])
        original = path.read_text()
        for before, after in (('100.91.0.10', '100.90.0.10'),
                              ('StrictHostKeyChecking yes', 'StrictHostKeyChecking no'),
                              ('IdentitiesOnly yes', 'IdentitiesOnly no'),
                              ('User fixture', 'User root'),
                              ('UserKnownHostsFile', 'UserKnownHostsFile /dev/null')):
            with self.subTest(change=after):
                path.write_text(original.replace(before, after))
                with self.assertRaises(ValueError):
                    target.pinned_ssh(self.config)

    def test_missing_wrong_additional_and_revoked_host_keys_are_rejected(self):
        known = self.root / 'server-known_hosts'
        for contents in ('', 'other ' + self.public_keys[0][0],
                         '100.91.0.10 ' + self.public_keys[1][0],
                         '\n'.join('100.91.0.10 ' + key[0] for key in self.public_keys),
                         '@revoked 100.91.0.10 ' + self.public_keys[0][0]):
            with self.subTest(contents=contents[:20]):
                known.write_text(contents + '\n')
                with self.assertRaises(ValueError):
                    target.pinned_ssh(self.config)

    def test_hashed_hosts_and_paths_with_spaces_are_supported(self):
        known = self.root / 'server-known_hosts'
        subprocess.run(['ssh-keygen', '-H', '-f', str(known)], check=True, capture_output=True)
        spaced = self.root / 'verified hosts'
        known.rename(spaced)
        path = Path(self.config['ssh'][2])
        path.write_text(path.read_text().replace(str(known), str(spaced)))
        transport = target.pinned_ssh(self.config)
        values = dict(line.split(' ', 1) for line in target.local(
            ['ssh', '-G', *transport[1:]]).splitlines() if ' ' in line)
        self.assertEqual(str(spaced), values['userknownhostsfile'])

    def test_unsafe_input_files_and_undeclared_peer_probe_are_rejected(self):
        path = Path(self.config['ssh'][2])
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            target.pinned_ssh(self.config)
        path.chmod(0o600)
        linked = self.root / 'symlink'
        linked.symlink_to(path)
        with self.assertRaises(ValueError):
            target.pinned_ssh({**self.config, 'ssh': ['ssh', '-F', str(linked), 'server']})
        transport = target.pinned_ssh(self.config['peer'], peer=True)
        self.assertEqual(target.peer_probe_command(self.config, transport),
                         [*transport, self.config['staff_probe_command'][-1]])
        for command in (['ssh', 'unreviewed-host', 'probe'], ['sh', '-c', 'probe']):
            with self.assertRaises(ValueError):
                target.peer_probe_command({**self.config, 'staff_probe_command': command}, transport)
        self.input.chmod(0o644)
        with self.assertRaises(ValueError):
            target.read_config(self.input)

    def traps(self):
        binary = self.root / 'bin'
        binary.mkdir()
        marker = self.root / 'side-effects'
        real_ssh = shutil.which('ssh')
        for name in ('ssh', 'sops', 'age-keygen', 'docker', 'systemctl'):
            path = binary / name
            path.write_text(f'#!{sys.executable}\nimport os,sys\nfrom pathlib import Path\n'
                + (f'if "-G" in sys.argv[1:]: os.execv({real_ssh!r}, [{real_ssh!r}, *sys.argv[1:]])\n'
                   if name == 'ssh' else '')
                + f'with Path({str(marker)!r}).open("a") as stream: stream.write({name!r}+"\\n")\n'
                + 'sys.exit(99)\n')
            path.chmod(0o700)
        env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
               'HOME': str(self.root), 'HOU1_SECURITY_WORKDIR': str(self.root / 'security')}
        return env, marker

    def invoke(self, name, args, env):
        return subprocess.run([sys.executable, str(ROOT / 'scripts' / name), *args],
                              env=env, text=True, capture_output=True, timeout=20)

    def test_entrypoints_reject_retargeted_aliases_before_remote_or_secret_actions(self):
        management = {'all': {'children': {'isolated_test_vps': {'hosts': {'rehearsal_vps': {
            'cms_environment': 'test', 'ansible_host': '100.91.0.10',
            'primary_domain': 'vps-test.pearlandpaws.com'}}}}}}
        (self.root / 'management.yml').write_text(yaml.safe_dump(management))
        cases = [(name, ['--config', str(self.input)]) for name in (
            'rehearsal-alert-drills.py', 'rehearsal-staff-login-drill.py',
            'rehearsal-isolation-drill.py', 'rehearsal-controller-tls.py',
            'rehearsal-monitor.py', 'install-rehearsal-monitor.py')]
        cases += [('prepare-rehearsal-host-secrets.py', ['--controller-dir', str(self.root)]),
                  ('reconnect-test-controller.py', ['--config', str(self.input)]),
                  ('live_oidc/controller.py', []), ('live_oidc/peer_drill.py', [])]
        env, marker = self.traps()
        for name in ('server', 'peer'):
            path = self.root / (name + '-ssh_config')
            path.write_text(path.read_text().replace('100.91.0.10' if name == 'server' else '192.0.2.50',
                                                     '100.90.0.99'))
        for name, args in cases:
            with self.subTest(script=name):
                result = self.invoke(name, args, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SSH resolves to a different destination address', result.stderr)
                self.assertFalse(marker.exists(), result.stdout + result.stderr)
        path = self.root / 'server-ssh_config'
        path.write_text(path.read_text().replace('100.90.0.99', '100.91.0.10'))
        # A good server must not let scripts mutate it before checking a bad peer.
        for name, args in cases:
            if name in ('rehearsal-isolation-drill.py', 'rehearsal-controller-tls.py',
                        'prepare-rehearsal-host-secrets.py'):
                continue
            with self.subTest(script=name, only_peer_invalid=True):
                result = self.invoke(name, args, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SSH resolves to a different destination address', result.stderr)
                self.assertFalse(marker.exists(), result.stdout + result.stderr)

    def test_bad_auxiliary_peer_blocks_monitor_requests_and_secret_decryption(self):
        spec = importlib.util.spec_from_file_location('fixture_monitor', ROOT / 'scripts/rehearsal-monitor.py')
        monitor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(monitor)
        config = copy.deepcopy(self.config)
        config['peer']['environment'] = 'production'
        with patch.object(monitor, 'run_command') as commands, \
                patch.object(monitor.urllib.request, 'urlopen') as requests:
            with self.assertRaises(ValueError):
                monitor.probe(config)
            commands.assert_not_called()
            requests.assert_not_called()

    def test_installer_delivers_helper_with_standalone_programs(self):
        binary = self.root / 'bin'
        binary.mkdir()
        systemctl = binary / 'systemctl'
        systemctl.write_text('#!/bin/sh\nexit 0\n')
        systemctl.chmod(0o700)
        env = {**os.environ, 'HOME': str(self.root),
               'PATH': str(binary) + os.pathsep + os.environ['PATH']}
        result = self.invoke('install-rehearsal-monitor.py', ['--config', str(self.input)], env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        helper = self.root / 'ssh_target.py'
        self.assertEqual(helper.read_bytes(), (ROOT / 'scripts/ssh_target.py').read_bytes())
        self.config['environment'] = 'production'
        self.write_json(self.input, self.config)
        result = subprocess.run([sys.executable, str(self.root / 'monitor.py'), '--config', str(self.input)],
                                cwd=self.root, env=env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('An explicit test target configuration is required', result.stderr)
        self.assertNotIn('ModuleNotFoundError', result.stderr)


if __name__ == '__main__':
    unittest.main()
