"""Check defaults with Ansible's real role and inventory precedence."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from helpers import ROOT, ROLES


def probe(role_specs, assertions, overrides=None, tags=None, extra_vars=None,
          vars_files=None):
    """Use production defaults in inert roles; do not install or contact hosts."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Keep actual default files and role names; replace executable tasks only.
        fixture_roles = root / 'roles'
        for spec in role_specs:
            role_root = fixture_roles / spec['role']
            (role_root / 'tasks').mkdir(parents=True)
            (role_root / 'tasks/main.yml').write_text(yaml.safe_dump([
                {'ansible.builtin.debug': {'msg': 'defaults loaded'}}]))
            source = ROLES / spec['role'] / 'defaults'
            if source.exists():
                (role_root / 'defaults').symlink_to(source, target_is_directory=True)
        inventory = root / 'inventory.yml'
        inventory.write_text(yaml.safe_dump({'all': {'hosts': {'localhost': {}},
            'vars': overrides or {}}}))
        group_vars = root / 'group_vars/all'
        group_vars.mkdir(parents=True)
        (group_vars / 'vars.yml').write_text(
            (ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
        play = dict(hosts='localhost', gather_facts=False,
                    roles=role_specs,
                    pre_tasks=[{'name': 'Probe defaults before role execution',
                                'ansible.builtin.assert': {'that': assertions},
                                'tags': ['always']}],
                    post_tasks=[{'name': 'Probe defaults after role execution',
                                 'ansible.builtin.assert': {'that': assertions},
                                 'tags': ['always']}])
        if vars_files:
            play['vars_files'] = vars_files
        play_path = root / 'play.yml'
        play_path.write_text(yaml.safe_dump([play]))
        argv = ['ansible-playbook', '-i', str(inventory), '-c', 'local', str(play_path)]
        if tags:
            argv += ['--tags', tags]
        if extra_vars:
            argv += ['-e', yaml.safe_dump(extra_vars, default_flow_style=True)]
        return subprocess.run(argv, env={**os.environ, 'ANSIBLE_ROLES_PATH': str(fixture_roles),
                              'ANSIBLE_NOCOLOR': '1'}, capture_output=True, text=True, timeout=40)


class ConfigurationDefaultsTest(unittest.TestCase):
    def test_standalone_component_roles_load_their_own_defaults(self):
        scenarios = [
            ('webstack', "cms_storage_owner == '100'"),
            ('authentik', "authentik_image == 'ghcr.io/goauthentik/server:2025.10.4'"),
            ('authentik_policy', 'authentik_min_vcpus == 2'),
            ('netbird_control_plane', "netbird_dashboard_image == 'netbirdio/dashboard:v2.96.0'"),
            ('netbird_client', "netbird_enrollment_marker == '/etc/netbird/ansible-enrollment.json'"),
            ('netbird_dns', "netbird_dns_upstreams == ['1.1.1.1', '9.9.9.9']"),
            ('security', "ssh_bootstrap_fallback_delay == '180s'"),
        ]
        for role, assertion in scenarios:
            with self.subTest(role=role):
                result = probe([{'role': role}], [assertion])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tagged_entrypoint_preserves_inventory_and_extra_var_precedence(self):
        play = yaml.safe_load((ROOT / 'ansible/playbooks/hou1.yml').read_text())[0]
        result = probe(play['roles'], ["cms_storage_owner == '205'",
                       "cms_storage_group == '306'", "cms_backup_retention_days == 30"],
                       overrides={'cms_storage_owner': '205', 'cms_storage_group': '206'},
                       tags='webstack', extra_vars={'cms_storage_group': '306'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Probe defaults after role execution', result.stdout)

    def test_shared_paths_and_identity_remain_inventory_inputs(self):
        shared = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
        for name in ['cms_media_directory', 'cms_database_directory', 'authentik_identity_profiles',
                     'authentik_project_root_base', 'netbird_ingress_network', 'netbird_token_vault_file']:
            self.assertIn(name, shared)
        for name in ['cms_storage_owner', 'authentik_image', 'authentik_min_memory_mb',
                     'netbird_dashboard_image', 'ssh_bootstrap_fallback_delay']:
            self.assertNotIn(name, shared)
