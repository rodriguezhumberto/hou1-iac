"""Offline transport contracts; privileged commands are mocked."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from helpers import ROOT

SPEC = importlib.util.spec_from_file_location('lab_network', ROOT / 'scripts/lab-network.py')
network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network)


class LabNetworkTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = json.loads((ROOT / 'examples/lab-network.json').read_text())
        state = self.root / 'peer'; state.mkdir(mode=0o700)
        resolver = self.root / 'resolv.conf'; resolver.write_text('nameserver 192.0.2.53\n'); resolver.chmod(0o600)
        self.config.update(state_directory=str(state), resolver_file=str(resolver))
        self.path = self.root / 'network.json'
        self.path.write_text(json.dumps(self.config)); self.path.chmod(0o600)
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(str(self.root / 'agent'))
        self.addCleanup(self.socket.close)
        self.peer = {'id': 'a' * 64, 'pid': 12345}

    def test_private_configuration_and_test_boundaries(self):
        Path(self.config['resolver_file']).write_text('nameserver 192.0.2.53\noptions timeout:2 attempts:2\n')
        self.assertEqual(network.load_config(self.path, os.getuid()), self.config)
        for field, value in [('environment', 'prod'), ('address', '100.90.0.10'),
                ('management_url', 'http://netbird-test.example.invalid'),
                ('management_url', 'https://netbird.example.invalid'), ('image', 'netbirdio/netbird:latest')]:
            with self.subTest(field=field, value=value):
                bad = {**self.config, field: value}; self.path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): network.load_config(self.path, os.getuid())
        self.path.write_text(json.dumps(self.config)); self.path.chmod(0o644)
        with self.assertRaises(ValueError): network.load_config(self.path, os.getuid())
        self.path.chmod(0o600)
        Path(self.config['resolver_file']).write_text('search example.invalid\n')
        with self.assertRaises(ValueError): network.load_config(self.path, os.getuid())

    def inspect_fixture(self):
        return {'Name': '/' + self.config['container'], 'Id': self.peer['id'],
                'State': {'Running': True, 'Pid': self.peer['pid']},
                'Config': {'Image': self.config['image']},
                'HostConfig': {'NetworkMode': 'bridge', 'Privileged': False, 'CapAdd': ['NET_ADMIN']},
                'Mounts': [{'Source': self.config['state_directory'], 'Destination': '/var/lib/netbird', 'RW': True}]}

    def test_peer_identity_isolation_and_connection_are_checked(self):
        container = self.inspect_fixture()
        status = {'management': {'url': self.config['management_url'] + ':443', 'connected': True},
                  'netbirdIp': self.config['address'] + '/16'}
        def check(data, state):
            with patch.object(network.subprocess, 'check_output',
                    side_effect=[json.dumps([data]), json.dumps(state)]):
                return network.inspect_peer(self.config)
        self.assertEqual(check(container, status), self.peer)
        for change in ('image', 'privileged', 'network', 'mount', 'capabilities', 'stopped', 'pid'):
            bad = copy.deepcopy(container)
            if change == 'image': bad['Config']['Image'] = 'unselected:latest'
            if change == 'privileged': bad['HostConfig']['Privileged'] = True
            if change == 'network': bad['HostConfig']['NetworkMode'] = 'host'
            if change == 'mount': bad['Mounts'].append({'Source': '/', 'Destination': '/host', 'RW': True})
            if change == 'capabilities': bad['HostConfig']['CapAdd'].append('SYS_ADMIN')
            if change == 'stopped': bad['State']['Running'] = False
            if change == 'pid': bad['State']['Pid'] = 0
            with self.subTest(change=change), self.assertRaises(ValueError): check(bad, status)
        for state in ({**status, 'netbirdIp': '100.90.0.10/16'},
                {**status, 'management': {'url': self.config['management_url'], 'connected': False}},
                {**status, 'management': {'url': 'https://other.example.invalid', 'connected': True}}):
            with self.assertRaises(ValueError): check(container, state)

    def test_outer_command_creates_private_namespace_without_shell_or_container_mutation(self):
        command = ['/bin/echo', 'literal;$(text)']
        with patch.object(network, 'inspect_peer', return_value=self.peer), \
                patch.dict(os.environ, {'SSH_AUTH_SOCK': str(self.root / 'agent')}), \
                patch.object(network.os, 'execvp') as execute:
            self.assertEqual(network.main(['--config', str(self.path), '--', *command]), 0)
        argv = execute.call_args.args[1]
        self.assertEqual(argv[:7], ['sudo', '-n', 'unshare', '--mount', '--propagation', 'private', '/usr/bin/python3'])
        self.assertEqual(argv[-2:], command)
        contract = json.loads(argv[argv.index('--inside-private-mount') + 1])
        self.assertEqual(contract['peer'], self.peer)
        self.assertEqual(contract['uid'], os.getuid())

    def contract(self):
        return {'uid': os.getuid(), 'gid': os.getgid(), 'config': self.config, 'peer': self.peer,
                'agent': str(self.root / 'agent'), 'resolver_sha256': network.resolver_hash(self.config),
                'mount_namespace': 'mnt:[original]'}

    def test_entry_rechecks_identity_then_mounts_and_drops_privileges(self):
        contract = self.contract()
        with patch.object(network.os, 'geteuid', return_value=0), \
                patch.dict(os.environ, {'SUDO_UID': str(os.getuid()), 'SUDO_GID': str(os.getgid())}), \
                patch.object(network.os, 'readlink', return_value='mnt:[private]'), \
                patch.object(network, 'inspect_peer', return_value=self.peer), \
                patch.object(network.subprocess, 'run') as run, patch.object(network.os, 'execvp') as execute:
            network.enter(self.path, contract, ['/bin/true'])
        run.assert_called_once_with(['mount', '--bind', self.config['resolver_file'], '/etc/resolv.conf'], check=True)
        argv = execute.call_args.args[1]
        self.assertEqual(argv[:5], ['nsenter', '--target', str(self.peer['pid']), '--net', 'setpriv'])
        self.assertIn('--no-new-privs', argv)
        self.assertIn('--bounding-set=-all', argv)
        self.assertIn('-i', argv)
        self.assertEqual(argv[-1], '/bin/true')

    def test_changed_transport_never_mounts_or_executes(self):
        for change in ('peer', 'resolver', 'mount_namespace', 'sudo_user'):
            contract = self.contract()
            peer = dict(self.peer)
            if change == 'peer': peer['pid'] += 1
            if change == 'resolver': contract['resolver_sha256'] = 'changed'
            if change == 'mount_namespace': contract['mount_namespace'] = 'mnt:[private]'
            uid = str(os.getuid() + 1 if change == 'sudo_user' else os.getuid())
            with self.subTest(change=change), patch.object(network.os, 'geteuid', return_value=0), \
                    patch.dict(os.environ, {'SUDO_UID': uid, 'SUDO_GID': str(os.getgid())}), \
                    patch.object(network.os, 'readlink', return_value='mnt:[private]'), \
                    patch.object(network, 'inspect_peer', return_value=peer), \
                    patch.object(network.subprocess, 'run') as run, patch.object(network.os, 'execvp') as execute:
                with self.assertRaises(ValueError): network.enter(self.path, contract, ['/bin/true'])
                run.assert_not_called(); execute.assert_not_called()

    def test_missing_agent_and_docker_failure_cannot_execute_command(self):
        for error in (KeyError('SSH_AUTH_SOCK'), subprocess.TimeoutExpired('docker', 15)):
            with patch.object(network, 'inspect_peer', side_effect=error), patch.object(network.os, 'execvp') as execute:
                self.assertEqual(network.main(['--config', str(self.path), '--', '/bin/true']), 1)
                execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
