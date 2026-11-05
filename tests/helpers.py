"""Run production Ansible tasks locally with disposable inventory and fixtures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLES = ROOT / 'ansible/playbooks/roles'
DEFAULTS = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())


def run_plays(play_specs, extra_args=(), inventory_source=None):
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / 'play.yml'
        inventory = 'localhost,'
        if inventory_source is not None:
            inventory_file = Path(directory) / 'inventory.yml'
            inventory_file.write_text(yaml.safe_dump(inventory_source))
            inventory = str(inventory_file)
        defaults = yaml.safe_load(
            (ROOT / 'ansible/playbooks/hou1.yml').read_text())[0]['module_defaults']
        play.write_text(yaml.safe_dump([
            dict(hosts=spec.get('hosts', 'localhost'), gather_facts=False,
                 module_defaults=defaults,
                 vars={**DEFAULTS, 'new_user': 'test-user', 'ansible_host': '100.90.0.10', **spec.get('variables', {}),
                       'ansible_python_interpreter': sys.executable},
                 tasks=spec['tasks'])
            for spec in play_specs
        ]))
        env = {**os.environ, 'ANSIBLE_ROLES_PATH': str(ROLES),
               'ANSIBLE_FILTER_PLUGINS': str(ROOT / 'ansible/playbooks/filter_plugins'),
               'ANSIBLE_ACTION_PLUGINS': str(ROOT / 'ansible/playbooks/action_plugins'),
               'ANSIBLE_NOCOLOR': '1', 'ANSIBLE_LOCALHOST_WARNING': 'False'}
        return subprocess.run(
            ['ansible-playbook', '-i', inventory, '-c', 'local', str(play), *extra_args],
            env=env, text=True, capture_output=True, timeout=40,
        )


def run_play(variables, tasks, extra_args=(), inventory_source=None, hosts='localhost'):
    return run_plays([dict(variables=variables, tasks=tasks, hosts=hosts)],
                     extra_args, inventory_source)


def guard_play(variables, tasks, expected_guard=None):
    """Batch stateless guards, accepting only the intended assertion failure."""
    return dict(variables={**variables, 'fixture_expected_guard': expected_guard}, tasks=[
        {'block': tasks + [
            {'name': 'Require an invalid scenario to fail',
             'ansible.builtin.assert': {'that': 'fixture_expected_guard is none'}},
        ], 'rescue': [
            {'name': 'Verify the expected guard rejected this scenario',
             'ansible.builtin.assert': {'that': [
                 'fixture_expected_guard is not none',
                 'ansible_failed_task.name == fixture_expected_guard',
                 "ansible_failed_task.action == 'ansible.builtin.assert'",
                 'ansible_failed_result.evaluated_to is defined',
                 'ansible_failed_result.evaluated_to == false',
             ]}},
        ]},
    ])


def netbird_vars():
    return dict(netbird_server_group='test-servers',
                netbird_management_url='https://netbird.example.org',
                netbird_subnet='100.90.0.0/16', netbird_ip='100.90.0.10')
