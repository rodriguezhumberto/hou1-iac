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


def run_play(variables, tasks, extra_args=(), inventory_source=None, hosts='localhost'):
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / 'play.yml'
        inventory = 'localhost,'
        if inventory_source is not None:
            inventory_file = Path(directory) / 'inventory.yml'
            inventory_file.write_text(yaml.safe_dump(inventory_source))
            inventory = str(inventory_file)
        play.write_text(yaml.safe_dump([dict(
            hosts=hosts, gather_facts=False,
            vars={**DEFAULTS, 'new_user': 'test-user', 'hou1_vpn_ip': '10.10.0.10',
                  'ansible_host': '100.90.0.10', **variables,
                  'ansible_python_interpreter': sys.executable}, tasks=tasks,
        )]))
        env = {**os.environ, 'ANSIBLE_ROLES_PATH': str(ROLES),
               'ANSIBLE_FILTER_PLUGINS': str(ROOT / 'ansible/playbooks/filter_plugins'),
               'ANSIBLE_ACTION_PLUGINS': str(ROOT / 'ansible/playbooks/action_plugins'),
               'ANSIBLE_NOCOLOR': '1', 'ANSIBLE_LOCALHOST_WARNING': 'False'}
        return subprocess.run(
            ['ansible-playbook', '-i', inventory, '-c', 'local', str(play), *extra_args],
            env=env, text=True, capture_output=True, timeout=40,
        )


def netbird_vars():
    return dict(netbird_enabled=True,
                netbird_management_url='https://netbird.example.org',
                netbird_subnet='100.90.0.0/16', netbird_ip='100.90.0.10')
