"""Narrow UFW ownership, network replacement, missing dependencies and retries."""

import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

from helpers import ROLES
import yaml

SPEC = importlib.util.spec_from_file_location('lab_firewall', ROLES / 'lab_dns_containers/files/firewall.py')
firewall = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(firewall)


def network(name='application-default', identifier='a', subnet='172.25.0.0/24', internal=False):
    return {'Name': name, 'Id': identifier * 64, 'Driver': 'bridge', 'Options': {},
            'Internal': internal, 'IPAM': {'Config': [{'Subnet': subnet}]}}


class LabDnsContainerTest(unittest.TestCase):
    def setUp(self):
        self.config = {'version': 1, 'state': 'present', 'address': '192.168.50.11',
                       'networks': ['application-default', 'identity-backend']}
        self.networks = [network(), network('identity-backend', 'b', '172.26.0.0/24', internal=True),
                         network('unrelated', 'c', '172.27.0.0/24')]
        self.rules = set()
        self.commands = []
        self.fail_after_add = False

    def execute(self, argv):
        self.commands.append(argv)
        if argv[:3] == ['docker', 'network', 'ls']:
            return '\n'.join(n['Name'] for n in self.networks)
        if argv[:3] == ['docker', 'network', 'inspect']:
            return json.dumps([n for n in self.networks if n['Name'] in argv[3:]])
        if argv[:3] == ['ip', '-j', '-d']:
            return '[{"linkinfo":{"info_kind":"bridge"}}]'
        if argv == ['ufw', 'show', 'added']:
            return '\n'.join(shlex.join(['ufw', *firewall.rule_args(rule)]) for rule in sorted(self.rules, key=repr))
        deleting = argv[1:3] == ['--force', 'delete']
        args = argv[3:] if deleting else argv[1:]
        parsed = firewall.owned_rules(shlex.join(['ufw', *args]))
        if deleting: self.rules -= parsed
        else:
            self.rules |= parsed
            if self.fail_after_add: raise subprocess.CalledProcessError(1, argv)
        return ''

    def reconcile(self, required=()):
        return firewall.reconcile(self.config, required, self.execute)

    def test_only_declared_external_bridges_receive_exact_dns_rules(self):
        result = self.reconcile(['application-default'])
        self.assertEqual(result, {'changed': True, 'rules': 2})
        for interface, subnet, destination, protocol, comment in self.rules:
            self.assertEqual((interface, subnet, destination), ('br-aaaaaaaaaaaa', '172.25.0.0/24', '192.168.50.11'))
            self.assertIn(protocol, ('tcp', 'udp'))
            self.assertEqual(comment, firewall.COMMENT)
        self.assertFalse(self.reconcile()['changed'])
        self.assertFalse(any('unrelated' in c for c in self.commands if c[:3] == ['docker', 'network', 'inspect']))

    def test_replacement_removes_old_interface_subnet_and_preserves_unowned_rules(self):
        self.reconcile()
        self.networks[0] = network(identifier='d', subnet='172.28.0.0/24')
        self.commands.clear(); self.assertTrue(self.reconcile()['changed'])
        self.assertEqual({r[0] for r in self.rules}, {'br-dddddddddddd'})
        self.assertEqual({r[1] for r in self.rules}, {'172.28.0.0/24'})
        additions = [i for i,c in enumerate(self.commands) if c[:2] == ['ufw', 'allow']]
        deletions = [i for i,c in enumerate(self.commands) if c[:3] == ['ufw', '--force', 'delete']]
        self.assertLess(max(additions), min(deletions))
        unowned = "ufw allow from 172.29.0.0/24 to 192.168.50.11 port 53 proto tcp comment 'operator DNS'"
        self.assertEqual(firewall.owned_rules(unowned), set())
        self.config['networks'] = []
        self.assertEqual(self.reconcile()['rules'], 0)

    def test_legacy_allowances_are_replaced_and_disable_removes_only_owned_rules(self):
        self.rules = {(None, '172.17.0.0/16', '192.168.50.11', 'tcp', firewall.LEGACY)}
        self.assertTrue(self.reconcile()['changed'])
        self.assertEqual({r[-1] for r in self.rules}, {firewall.COMMENT})
        self.config['state'] = 'absent'; self.commands.clear()
        self.assertEqual(self.reconcile(), {'changed': True, 'rules': 0})
        self.assertFalse(any(c[0] == 'docker' for c in self.commands))
        self.assertFalse(self.reconcile()['changed'])

    def test_missing_required_network_and_wrong_bridge_fail_before_firewall_changes(self):
        for missing in ('absent', 'undeclared'):
            self.commands.clear()
            if missing == 'absent': self.config['networks'].append(missing)
            with self.assertRaises(ValueError): self.reconcile([missing])
            self.assertFalse(any(c[:2] == ['ufw', 'allow'] for c in self.commands))
        def physical(argv):
            return '[{"linkinfo":{"info_kind":"ether"}}]' if argv[0] == 'ip' else self.execute(argv)
        with self.assertRaises(ValueError): firewall.reconcile(self.config, execute=physical)
        self.assertFalse(self.rules)

    def test_interrupted_rule_installation_is_repaired_from_actual_ufw_state(self):
        self.fail_after_add = True
        with self.assertRaises(subprocess.CalledProcessError): self.reconcile()
        self.assertEqual(len(self.rules), 1)
        self.fail_after_add = False
        self.assertTrue(self.reconcile()['changed'])
        self.assertEqual(len(self.rules), 2)
        self.assertFalse(self.reconcile()['changed'])

    def test_invalid_network_and_owned_rule_shapes_cannot_broaden_access(self):
        for changes in [dict(Driver='host'), dict(IPAM={'Config': [{'Subnet': '0.0.0.0/0'}]}),
                dict(IPAM={'Config': [{'Subnet': '10.0.0.0/7'}]}),
                dict(Options={'com.docker.network.bridge.name': 'bad;interface'})]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                firewall.desired_rules(self.config, [{**network(), **changes}])
        for line in ["ufw allow 53 comment 'hou-lab-dns managed'",
                     "ufw allow from 0.0.0.0/0 to 192.168.50.11 port 53 proto tcp comment 'hou-lab container DNS'"]:
            with self.assertRaises((ValueError, IndexError)): firewall.owned_rules(line)

    def test_compose_preparation_creates_without_starting_then_requires_network_access(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policy.json'; path.write_text(json.dumps(self.config))
            document = {'services': {'app': {'networks': {'default': {}}, 'depends_on': {'database': {}}},
                                     'database': {'networks': {'backend': {}}}},
                        'networks': {'default': {'name': 'application-default'}, 'backend': {'name': 'identity-backend'}}}
            calls = []
            def execute(argv, directory): calls.append(argv); return b''
            def apply(argv, **kwargs):
                calls.append(argv); return subprocess.CompletedProcess(argv, 0, b'{"changed":false}')
            with patch.object(firewall, 'POLICY', path), patch.object(firewall.subprocess, 'run', side_effect=apply):
                firewall.prepare_compose(Path(folder), ['docker', 'compose'], document, ['app'], execute)
            self.assertEqual(calls[0], ['docker', 'compose', 'create', '--no-build', '--no-recreate', 'app'])
            self.assertEqual(calls[1][-2:], ['application-default', 'identity-backend'])
            self.assertFalse(any('up' in c or '--force-recreate' in c for c in calls))
            self.config['networks'] = []; path.write_text(json.dumps(self.config)); calls.clear()
            with patch.object(firewall, 'POLICY', path), self.assertRaises(ValueError):
                firewall.prepare_compose(Path(folder), ['docker', 'compose'], document, ['app'], execute)
            self.assertEqual(calls, [])


@unittest.skipUnless(shutil.which('docker'), 'Docker is needed for the disposable preparation fixture')
class ComposeDnsPreparationIntegrationTest(unittest.TestCase):
    def test_fresh_network_is_prepared_before_start_and_existing_container_is_preserved(self):
        image = 'nginx:1.30.7-alpine@sha256:caf4e1e032a20f9c3c5e28fc7f25ffd768ad8677b4975c54193075997b3a97e3'
        if subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, timeout=15).returncode:
            self.skipTest('Pinned fixture image is not cached locally')
        spec = importlib.util.spec_from_file_location('dns_runtime', ROLES / 'compose_runtime/files/apply.py')
        runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
        with tempfile.TemporaryDirectory(prefix='lab-dns-compose-') as folder:
            root = Path(folder); project = 'lab-dns-' + uuid.uuid4().hex[:10]
            compose = ['docker', 'compose', '--project-name', project]
            (root / 'compose.yml').write_text(yaml.safe_dump({'services': {'fixture': {
                'image': image, 'entrypoint': ['/bin/sh', '-c'],
                'command': ['trap "exit 0" TERM; while :; do sleep 1; done']}}}))
            config = {'version': 1, 'state': 'present', 'address': '192.168.50.11', 'networks': [project + '_default']}
            policy = root / 'policy.json'; policy.write_text(json.dumps(config))
            real_run = subprocess.run
            dns_ready = False
            fail = True
            def dispatch(argv, *args, **kwargs):
                nonlocal dns_ready
                if argv[0] == '/usr/local/libexec/hou-lab-dns-firewall':
                    state = subprocess.check_output(compose + ['ps', '--all', '--quiet'], cwd=root, text=True).strip()
                    self.assertTrue(state)
                    if fail:
                        running = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Running}}', state], text=True).strip()
                        self.assertEqual(running, 'false')
                        raise subprocess.CalledProcessError(1, argv)
                    dns_ready = True
                    return subprocess.CompletedProcess(argv, 0, b'{"changed":false}')
                if 'up' in argv: self.assertTrue(dns_ready)
                return real_run(argv, *args, **kwargs)
            try:
                with patch.object(runtime, 'LAB_DNS_POLICY', policy), patch.object(firewall, 'POLICY', policy), \
                        patch.object(runtime.runpy, 'run_path', return_value={'prepare_compose': firewall.prepare_compose}), \
                        patch('subprocess.run', side_effect=dispatch):
                    with self.assertRaises(subprocess.CalledProcessError): runtime.reconcile(root, compose)
                    self.assertFalse((root / '.compose-applied-inputs.json').exists())
                    fail = False
                    self.assertTrue(runtime.reconcile(root, compose))
                    before = runtime.container_ids(compose, root, ['fixture'])
                    self.assertFalse(runtime.reconcile(root, compose))
                    self.assertEqual(runtime.container_ids(compose, root, ['fixture']), before)
            finally:
                real_run(compose + ['down', '--remove-orphans', '--timeout', '2'], cwd=root,
                         capture_output=True, timeout=60, check=True)


if __name__ == '__main__':
    unittest.main()
