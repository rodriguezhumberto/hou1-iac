"""Render colocated infrastructure and validate it with Compose and Nginx parsers."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

import test_vpn_migration as migration

ROLES = migration.ROLES


class ControlPlaneTest(unittest.TestCase):
    def run_play(self, variables, tasks, extra_args=(), inventory_source=None, hosts='localhost'):
        return migration.MigrationTest.run_play(self, variables, tasks, extra_args, inventory_source, hosts)

    def variables(self, directory):
        return {
            'netbird_domain': 'netbird.example.org',
            'netbird_management_url': 'https://netbird.example.org',
            'primary_domain': 'example.org', 'new_user': 'test-user', 'secondary_domain': 'example.net',
            'app_directory': directory + '/app',
            'netbird_control_plane_directory': directory + '/server',
            'netbird_backup_directory': directory + '/backups',
            'netbird_control_plane_secrets': {name: base64.b64encode(bytes([i]) * 32).decode()
                for i, name in enumerate(['relay_auth_secret', 'store_encryption_key',
                                          'cookie_encryption_key'])},
        }

    def test_colocation_guards(self):
        for overrides, valid in [({}, True), ({'wireguard_enabled': False}, False),
                ({'netbird_domain': 'example.org'}, False),
                ({'netbird_bootstrap_allowed_cidrs': ['0.0.0.0/0']}, False),
                ({'netbird_ingress_proxy_ip': '192.168.99.2'}, False),
                ({'netbird_server_image': 'netbirdio/netbird-server:latest'}, False)]:
            with self.subTest(overrides=overrides):
                result = self.run_play({**self.variables('/tmp/hou1-test'), **overrides}, [
                    {'ansible.builtin.import_tasks': str(ROLES / 'netbird_control_plane/tasks/validate.yml')}])
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)

    def test_recovery_guard_cannot_be_bypassed_by_deploy_tag(self):
        variables = {**self.variables('/tmp/hou1-test'),
                     **migration.MigrationTest.netbird_vars(),
                     'wireguard_enabled': False, 'management_vpn': 'netbird'}
        result = self.run_play(variables, [
            {'ansible.builtin.import_role': {'name': 'vpn_config'}, 'tags': ['always']}],
            extra_args=['--tags', 'deploy'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Keep wireguard_enabled=true', result.stdout)

    def test_shared_inventory_and_production_only_target(self):
        inventory = yaml.safe_load((migration.ROOT / 'ansible/inventory/inventory.yml').read_text())
        # Copy the real group variable files without loading encrypted secrets.
        children = inventory['all']['children']
        for group in ['test_env', 'prod_env']:
            children[group]['vars'] = yaml.safe_load(
                (migration.ROOT / ('ansible/inventory/group_vars/' + group + '.yml')).read_text())
        # The production identity is reused, not duplicated under a new alias.
        self.assertEqual(list(children['netbird_control_plane']['hosts']), ['prod_hou1'])
        self.assertIn('prod_hou1', children['prod_env']['children']['hou1_prod']['hosts'])
        tasks = [
            {'ansible.builtin.assert': {'that': [
                "netbird_domain == 'netbird.example.org'",
                "netbird_management_url == 'https://netbird.example.org'",
                "netbird_package_version == '0.82.0'",
                "netbird_server_image == 'netbirdio/netbird-server:0.82.0'",
                "wireguard_enabled | bool",
                "(inventory_hostname == 'prod_hou1') == ('netbird_control_plane' in group_names)",
            ]}},
        ]
        result = self.run_play({'primary_domain': 'example.org', 'new_user': 'test-user',
            'hou1_vpn_ip': '{{ "10.10.0.1" if inventory_hostname == "prod_hou1" else "10.10.0.10" }}'},
            tasks, inventory_source=inventory, hosts='hou1_prod:hou1_test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        target_tasks = [{'ansible.builtin.import_tasks': str(ROLES / 'netbird_control_plane/tasks/target.yml')}]
        for hosts, overrides, valid in [
                ('netbird_control_plane', {}, True), ('hou1_test', {}, False),
                ('netbird_control_plane', {'target_hosts': 'hou1_test'}, False)]:
            with self.subTest(hosts=hosts, overrides=overrides):
                result = self.run_play(overrides, target_tasks, inventory_source=inventory, hosts=hosts)
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)

    def test_application_runs_do_not_deploy_control_plane(self):
        app_play = yaml.safe_load((migration.ROOT / 'ansible/playbooks/hou1.yml').read_text())[0]
        self.assertNotIn('netbird_control_plane', [item['role'] for item in app_play['roles']])
        self.assertNotIn('post_tasks', app_play)  # No WireGuard retirement.
        control_play = yaml.safe_load(
            (migration.ROOT / 'ansible/playbooks/netbird-control-plane.yml').read_text())[0]
        self.assertEqual(control_play['hosts'], 'netbird_control_plane')
        self.assertEqual(control_play['vars']['ansible_host'], '{{ hou1_vpn_ip }}')
        # Real Ansible task expansion catches role/include changes as well.
        result = self.run_play({'netbird_enabled': True, 'primary_domain': 'example.org'}, [
            {'ansible.builtin.import_role': {'name': 'webstack'}},
        ], extra_args=['--list-tasks'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('netbird_control_plane', result.stdout)
        self.assertNotIn('STUN', result.stdout)
        self.assertNotIn('combined server', result.stdout)

    def test_secret_generation_is_persistent_and_private(self):
        generator = ROLES / 'netbird_control_plane/files/create-secrets.py'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secrets.json'
            result = subprocess.run([sys.executable, str(generator), str(path)], capture_output=True)
            self.assertEqual(result.returncode, 0)
            original = path.read_bytes()
            values = json.loads(original)
            self.assertEqual(len(set(values.values())), 3)
            self.assertTrue(all(len(base64.b64decode(v)) == 32 for v in values.values()))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            result = subprocess.run([sys.executable, str(generator), str(path)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), original)

    def test_rendered_stack_and_ingress(self):
        nginx = os.environ.get('NGINX_BIN') or shutil.which('nginx')
        compose = ([os.environ['COMPOSE_BIN']] if os.environ.get('COMPOSE_BIN') else
                   ['docker', 'compose'] if shutil.which('docker') else
                   [shutil.which('docker-compose')] if shutil.which('docker-compose') else None)
        self.assertTrue(nginx and compose, 'Install Nginx and Docker Compose (or set NGINX_BIN/COMPOSE_BIN).')
        with tempfile.TemporaryDirectory() as directory:
            paths = Path(directory)
            app = paths / 'app'
            app.mkdir()
            server = paths / 'server'
            server.mkdir()
            (server / 'data').mkdir()
            templates = ['compose.yaml', 'config.yaml', 'dashboard.env',
                         'app-compose.override.yml', 'nginx.conf', 'backup.sh']
            tasks = [{'ansible.builtin.copy': {
                'dest': str(paths / name),
                'content': "{{ lookup('template', '" + str(ROLES / 'netbird_control_plane/templates' / (name + '.j2')) + "') }}",
            }} for name in templates]
            result = self.run_play(self.variables(directory), tasks)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config = yaml.safe_load((paths / 'config.yaml').read_text())['server']
            self.assertEqual(config['store']['engine'], 'sqlite')
            self.assertEqual(config['auth']['issuer'], 'https://netbird.example.org/oauth2')
            self.assertEqual(config['reverseProxy']['trustedPeers'], ['172.30.80.2/32'])
            self.assertEqual(len(base64.b64decode(config['store']['encryptionKey'])), 32)
            (paths / 'dashboard.env').replace(server / 'dashboard.env')
            (paths / 'compose.yaml').replace(server / 'compose.yaml')
            cp = subprocess.run(compose + ['-f', str(server / 'compose.yaml'), 'config', '--format', 'json'],
                                text=True, capture_output=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            stack = json.loads(cp.stdout)
            self.assertEqual(set(stack['services']), {'netbird-server', 'dashboard'})
            self.assertEqual(stack['services']['netbird-server']['ports'][0]['protocol'], 'udp')
            self.assertEqual(stack['services']['netbird-server']['ports'][0]['published'], '3478')
            self.assertNotIn('ports', stack['services']['dashboard'])
            self.assertNotIn('privileged', stack['services']['netbird-server'])
            self.assertNotIn('NB_SETUP_PAT_ENABLED', stack['services']['netbird-server'].get('environment', {}))
            # Compose must correctly merge a list-style app network with the
            # mapping-style override and retain all original ingress settings.
            base = {'services': {'nginx': {'image': 'nginx:alpine',
                'ports': ['80:80', '443:443'], 'volumes': ['./original.conf:/etc/nginx/templates/default.conf.template:ro'],
                'networks': ['app-network']}}, 'networks': {'app-network': {}}}
            (app / 'docker-compose.yml').write_text(yaml.safe_dump(base))
            (paths / 'app-compose.override.yml').replace(app / 'docker-compose.override.yml')
            merged = subprocess.run(compose + ['--project-directory', str(app), 'config', '--format', 'json'],
                text=True, capture_output=True)
            self.assertEqual(merged.returncode, 0, merged.stderr)
            service = json.loads(merged.stdout)['services']['nginx']
            self.assertEqual(set(service['networks']), {'app-network', 'netbird-ingress'})
            self.assertEqual(service['networks']['netbird-ingress']['ipv4_address'], '172.30.80.2')
            self.assertEqual(len(service['volumes']), 3)
            self.assertEqual(len(service['ports']), 2)
            subprocess.run(['bash', '-n', str(paths / 'backup.sh')], check=True)
            # Use a throwaway certificate and local temp paths for syntax-only
            # validation. No Nginx server, DNS lookup, or VPN is started.
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(paths / 'key.pem'), '-out', str(paths / 'cert.pem'),
                '-days', '1', '-subj', '/CN=netbird.example.org'],
                check=True, capture_output=True)
            ingress = (paths / 'nginx.conf').read_text()
            self.assertIn('limit_except GET', ingress)
            self.assertIn('allow 10.10.0.0/24;', ingress)
            self.assertIn('grpc_pass grpc://$netbird_server;', ingress)
            self.assertIn('proxy_set_header Upgrade $http_upgrade;', ingress)
            self.assertIn('location ^~ /api/setup/', ingress)
            ingress = ingress.replace('listen 80;', 'listen 18080;').replace('listen 443 ssl;', 'listen 18443 ssl;')
            ingress = ingress.replace('/etc/letsencrypt/live/netbird.example.org/fullchain.pem', str(paths / 'cert.pem'))
            ingress = ingress.replace('/etc/letsencrypt/live/netbird.example.org/privkey.pem', str(paths / 'key.pem'))
            (paths / 'main.conf').write_text('pid ' + str(paths / 'nginx.pid') + ';\n'
                'error_log stderr;\nevents {}\nhttp {\naccess_log off;\n'
                'client_body_temp_path ' + str(paths / 'body') + ';\n'
                'proxy_temp_path ' + str(paths / 'proxy') + ';\n'
                'fastcgi_temp_path ' + str(paths / 'fastcgi') + ';\n'
                'uwsgi_temp_path ' + str(paths / 'uwsgi') + ';\n'
                'scgi_temp_path ' + str(paths / 'scgi') + ';\n' + ingress + '\n}\n')
            check = subprocess.run([nginx, '-t', '-p', directory, '-c', str(paths / 'main.conf')],
                                   text=True, capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == '__main__':
    unittest.main()
