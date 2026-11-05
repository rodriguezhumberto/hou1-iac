"""Check public bootstrap boundaries, retirement gates, and SSH rollback offline."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

from helpers import ROOT, ROLES, guard_play, netbird_vars, run_play, run_plays


class WorkflowTest(unittest.TestCase):
    def test_only_initialization_can_use_public_addresses(self):
        inventory = yaml.safe_load((ROOT / 'ansible/inventory/inventory.yml').read_text())
        tasks = [{'ansible.builtin.import_role': {'name': 'netbird_common'}}]
        guard = 'Require public initialization targets or NetBird management'
        plays = []
        for hosts, bootstrap, address, valid in [
            ('hou1_test_init', True, '203.0.113.10', True),
            ('hou1_prod_init', True, '203.0.113.1', True),
            ('hou1_test_init', True, '203.0.113.99', False),
            ('hou1_test', False, '100.90.0.10', True),
            ('hou1_prod', False, '100.90.0.10', True),
            ('hou1_test', False, '203.0.113.10', False),
            ('hou1_prod', True, '203.0.113.1', False),
        ]:
            variables = {**netbird_vars(), 'node_bootstrap': bootstrap,
                         'test_vps_public_ip': '203.0.113.10',
                         'prod_vps_public_ip': '203.0.113.1', 'ansible_host': address}
            plays.append(dict(guard_play(variables, tasks, None if valid else guard), hosts=hosts))
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tagged_initialization_is_rejected(self):
        inventory = yaml.safe_load((ROOT / 'ansible/inventory/inventory.yml').read_text())
        result = run_play({**netbird_vars(), 'node_bootstrap': True,
                           'test_vps_public_ip': '203.0.113.10',
                           'ansible_host': '203.0.113.10'}, [
            {'ansible.builtin.import_role': {'name': 'netbird_common'}, 'tags': ['always']}],
            extra_args=['--tags', 'deploy'], inventory_source=inventory, hosts='hou1_test_init')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Initialize with a complete playbook run', result.stdout)

    def test_retirement_requires_verification_and_netbird(self):
        inventory = yaml.safe_load((ROOT / 'ansible/inventory/inventory.yml').read_text())
        play = yaml.safe_load((ROOT / 'ansible/playbooks/retire-wireguard.yml').read_text())[0]
        tasks = play['pre_tasks']
        guard = tasks[0]['name']
        plays = []
        for hosts, overrides, valid in [
            ('hou1_test', {}, False),
            ('hou1_test', {'netbird_retirement_verified': True}, True),
            ('hou1_prod', {'netbird_retirement_verified': True}, True),
            ('hou1_prod', {'netbird_retirement_verified': True, 'ansible_host': '203.0.113.1'}, False),
            ('hou1_prod_init', {'netbird_retirement_verified': True, 'node_bootstrap': True}, False),
        ]:
            plays.append(dict(guard_play({**netbird_vars(), **overrides}, tasks,
                                          None if valid else guard), hosts=hosts))
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run_play({**netbird_vars(), 'netbird_retirement_verified': True}, tasks,
                          extra_args=['--tags', 'deploy'], inventory_source=inventory, hosts='hou1_test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Run without tags over NetBird', result.stdout)

    def test_public_control_plane_bootstrap_defers_private_ip_services(self):
        bootstrap = yaml.safe_load((ROOT / 'ansible/playbooks/bootstrap-control-plane.yml').read_text())[0]
        roles = bootstrap['roles']
        web = next(role for role in roles if role['role'] == 'webstack')
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())
        deploy = next(task for task in tasks if task['name'] == 'Rebuild and deploy updated Docker containers')
        # Render the production command with a Compose fixture that records args.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$COMPOSE_ARGUMENTS"\n')
            docker.chmod(0o755)
            for variables, expected_tail in [(web['vars'], 'nginx'), ({}, '120')]:
                task = copy.deepcopy(deploy)
                task.update(environment={'PATH': directory + ':' + os.environ['PATH'],
                                         'COMPOSE_ARGUMENTS': str(root / 'args')})
                result = run_play({**variables, 'app_directory': directory}, [task])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                args = (root / 'args').read_text().splitlines()
                self.assertEqual(args[-1], expected_tail)
                self.assertEqual(args[:3], ['compose', 'up', '-d'])

    def test_initialization_hands_off_to_netbird_before_remaining_tasks(self):
        security = yaml.safe_load((ROLES / 'security/tasks/main.yml').read_text())
        handoff = next(task for task in security if task['name'] ==
                       'Switch initialized hosts to administrative NetBird access')
        tasks = [handoff, {'ansible.builtin.assert': {'that': [
            "ansible_host == netbird_ip", "ansible_user == new_user",
            "ansible_ssh_pass == ''", "not (node_bootstrap | bool)",
        ]}}, {'ansible.builtin.import_role': {'name': 'netbird_common'}}]
        result = run_play({**netbird_vars(), 'node_bootstrap': True,
                           'ansible_host': '203.0.113.10', 'ansible_user': 'root',
                           'ansible_ssh_pass': 'fixture-password'}, tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failed_fresh_ssh_probe_restores_and_reloads_listener(self):
        security = yaml.safe_load((ROLES / 'security/tasks/main.yml').read_text())
        block = next(task for task in security if 'rescue' in task)
        # Execute the real rollback block using disposable configuration files.
        # The daemon and SSH executable are fixtures; no host services are changed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'sshd_config'
            config.write_text('ListenAddress 203.0.113.10\nPort 22\n')
            reloads = root / 'reloads'
            ssh = root / 'ssh'
            ssh.write_text('#!' + sys.executable + '\n' + '''
import os, pathlib, sys
config = pathlib.Path(os.environ['FIXTURE_CONFIG']).read_text()
assert 'ListenAddress 100.90.0.10' in config
assert 'ControlMaster=no' in sys.argv and 'ControlPath=none' in sys.argv
assert 'test-user@100.90.0.10' in sys.argv
sys.exit(int(os.environ['FIXTURE_FAIL']))
''')
            ssh.chmod(0o755)
            rendered_template = str(ROLES / 'security/templates/sshd_config.j2')
            # Adapt paths and daemon calls only, keeping production control flow.
            fixture = copy.deepcopy(block)
            for section in ('block', 'rescue'):
                for task in fixture[section]:
                    if 'ansible.builtin.slurp' in task:
                        task['ansible.builtin.slurp']['src'] = str(config)
                    if 'ansible.builtin.copy' in task:
                        args = task['ansible.builtin.copy']
                        args.update(dest=str(config), owner=str(os.getuid()), group=str(os.getgid()))
                        args.pop('validate', None)
                        if 'lookup' in args['content']:
                            args['content'] = "{{ lookup('template', '" + rendered_template + "') }}"
                    if 'ansible.builtin.systemd' in task:
                        del task['ansible.builtin.systemd']
                        task['ansible.builtin.shell'] = 'echo reload >> ' + str(reloads)
                    if task.get('register') == 'ssh_active_listeners':
                        del task['ansible.builtin.command']
                        task['ansible.builtin.command'] = 'echo tcp LISTEN 0 128 100.90.0.10:22 0.0.0.0:*'
                    if task.get('register') == 'ssh_effective_config':
                        del task['ansible.builtin.command']
                        task['ansible.builtin.shell'] = "sed 's/ListenAddress/listenaddress/; s/$/:22/' " + str(config)
                        # Port lines are ignored by the production listener guard.
                    if 'ansible.builtin.import_tasks' in task:
                        name = task['ansible.builtin.import_tasks']
                        task['ansible.builtin.import_tasks'] = str(ROLES / 'security/tasks' / name)
            for fail in (False, True):
                with self.subTest(fail=fail):
                    original = 'ListenAddress 203.0.113.10\nPort 22\n'
                    config.write_text(original)
                    reloads.unlink(missing_ok=True)
                    tasks = [{'block': [fixture], 'environment': {
                        'PATH': directory + ':' + os.environ['PATH'],
                        'FIXTURE_CONFIG': str(config), 'FIXTURE_FAIL': str(int(fail))},
                        'rescue': [{'ansible.builtin.assert': {'that': [
                            'fixture_fail | bool',
                            "ansible_failed_task.name == 'Stop after restoring the previous listener'",
                        ]}}]}]
                    result = run_play({**netbird_vars(), 'fixture_fail': fail}, tasks)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if fail:
                        self.assertEqual(config.read_text(), original)
                        self.assertEqual(len(reloads.read_text().splitlines()), 2)
                    else:
                        self.assertIn('ListenAddress 100.90.0.10', config.read_text())
                        self.assertNotIn('ListenAddress 203.0.113.10', config.read_text())
                        self.assertEqual(len(reloads.read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
