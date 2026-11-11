"""Production recovery uses explicit destinations and rejects rehearsal state."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined
import yaml

from helpers import DEFAULTS, ROOT, ROLES, guard_play, run_plays
import test_application_image as images
import test_host_recovery as host


def request():
    cfg = host.inputs()
    cfg.update(environment='production', netbird_subnet='100.90.0.0/16', server_ip='100.90.0.10',
               runtime_age_path='/root/.config/sops/age/runtime-prod.key')
    for key in ['test_suffix', 'deny_cidrs', 'test_addresses', 'allow_local_cidrs', 'allow_update_cidrs']:
        cfg.pop(key)
    cfg['origins'] = {key: 'https://' + name + '.example.invalid' for key, name in
                      [('auth', 'identity'), ('staff', 'staff'), ('netbird', 'vpn'), ('public', 'www')]}
    cfg['hostnames'] = [value.removeprefix('https://') for value in cfg['origins'].values()]
    cfg['deployment'] = dict(
        paths=dict(app='/opt/site-production', auth='/opt/hou1-authentik/prod',
                   auth_data='/var/lib/hou1-authentik/prod', netbird='/opt/hou1-netbird',
                   cms='/var/lib/site/production/cms'),
        projects=dict(app='site-production', auth='hou1-authentik-prod', netbird='hou1-netbird-production'),
        networks=dict(auth='hou1-authentik-prod', netbird='hou1-private-ingress',
                      subnet='172.29.20.0/29', dynamic_range='172.29.20.4/30', proxy_ip='172.29.20.2'))
    return cfg


def fixture(root):
    old, payload, bundle, lock_path = host.HostRecoveryPreparationTest().fixture(root)
    cfg = request()
    cfg['staff_revision'] = old['staff_revision']
    metadata = json.loads((payload / 'recovery.json').read_text())
    metadata.update(environment='production', recovery_files=[cfg['runtime_age_path']])
    (payload / 'recovery.json').write_text(json.dumps(metadata))
    images.add_receipt(payload, cfg['staff_revision'])
    lock = json.loads(lock_path.read_text()); lock.pop('node'); lock['astro-app'] = images.IMAGE
    lock_path.write_text(json.dumps(lock))
    values = dict(staff_vpn_subnet=cfg['netbird_subnet'], HOU1_VPN_IP='100.90.0.10',
                  STAFF_OIDC_ISSUER=cfg['origins']['auth'] + '/application/o/staff/',
                  STAFF_OIDC_REDIRECT_URI=cfg['origins']['staff'] + '/api/staff/oidc/callback',
                  PUBLIC_SITE_ORIGIN=cfg['origins']['public'],
                  PRIMARY_HOSTNAMES='www.example.invalid', SECONDARY_HOSTNAMES='www.example.invalid',
                  STAFF_HOSTNAME='staff.example.invalid', PRIMARY_CERT_DIR='shared',
                  SECONDARY_CERT_DIR='shared', STAFF_CERT_DIR='shared')
    (payload / 'app-config/.env').write_text(''.join(key + '=' + value + '\n' for key, value in values.items()))
    (payload / 'netbird-files/config.yaml').write_text(yaml.safe_dump({'server': {
        'exposedAddress': cfg['origins']['netbird'], 'disableGeoliteUpdate': True}}))
    web_path = payload / 'app-config/docker-compose.yml'
    web = yaml.safe_load(web_path.read_text())
    web['networks'] = {'app-network': {'driver': 'bridge'}}
    for name in ('astro-app', 'nginx'):
        web['services'][name]['networks'] = ['app-network']
    web_path.write_text(yaml.safe_dump(web))
    deployment = cfg['deployment']
    context = {**DEFAULTS, 'authentik_environment': 'prod',
               'authentik_compose_project': deployment['projects']['auth'],
               'authentik_data_root': deployment['paths']['auth_data'],
               'authentik_ingress_network': deployment['networks']['auth'],
               'netbird_server_image': 'netbirdio/netbird-server:0.82.0',
               'netbird_ingress_network': deployment['networks']['netbird']}
    engine = Environment(undefined=StrictUndefined)
    for role, destination in [('authentik', 'authentik'), ('netbird_control_plane', 'netbird-files')]:
        template = (ROLES / role / 'templates/compose.yaml.j2').read_text()
        (payload / destination / 'compose.yaml').write_text(engine.from_string(template).render(**context))
    (payload / 'app-config/docker-compose.override.yml').write_text(yaml.safe_dump(dict(
        services=dict(nginx=dict(networks={
            'app-network': {}, 'netbird-ingress': {'ipv4_address': deployment['networks']['proxy_ip']},
            'authentik-ingress': None}, volumes=[deployment['paths']['netbird'] + '/ingress:/etc/nginx/netbird:ro'])),
        networks={'netbird-ingress': {'external': True, 'name': deployment['networks']['netbird']},
                  'authentik-ingress': {'external': True, 'name': deployment['networks']['auth']}})))
    cert = payload / 'app-config/certbot/conf/live/shared'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=fixture', '-addext', 'subjectAltName=' + ','.join('DNS:' + host for host in cfg['hostnames']),
                    '-keyout', str(cert / 'privkey.pem'), '-out', str(cert / 'fullchain.pem')],
                   capture_output=True, check=True)
    return cfg, payload, bundle, lock_path


class ProductionRecoveryTest(unittest.TestCase):
    def test_real_templates_and_digest_backed_bundle_preserve_production_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, payload, bundle, lock = fixture(root)
            output = root / 'prepared'
            self.assertTrue(host.prepare.prepare(cfg, payload, bundle, lock, output)['prepared'])
            saved = json.loads((output / 'request.json').read_text())
            self.assertEqual(saved['environment'], 'production')
            self.assertEqual(saved['deployment'], cfg['deployment'])
            self.assertEqual(saved['application_image'], images.receipt(cfg['staff_revision']))
            self.assertFalse((output / 'Dockerfile.recovery').exists())
            self.assertFalse((output / 'isolation.py').exists())
            proxy = (output / 'authentik.conf').read_text()
            self.assertIn('authentik-server-prod:9000', proxy)
            self.assertIn('allow 100.90.0.0/16;', proxy)
            if shutil.which('docker'):
                for source, name in [('authentik/authentik.env', 'authentik.env'), ('netbird-files/dashboard.env', 'dashboard.env'), ('app-config/.env', '.env')]:
                    shutil.copyfile(payload / source, output / name)
                for name in ['auth', 'netbird', 'app']:
                    command = ['docker', 'compose', '-f', str(output / (name + '.compose.json')),
                               'config', '--quiet']
                    if name == 'app':
                        command[4:4] = ['-f', str(payload / 'app-config/docker-compose.override.yml')]
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrong_environment_endpoints_key_or_revision_fail_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, payload, bundle, lock = fixture(root)
            cases = []
            for key, value in [('environment', 'test'), ('staff_revision', 'a' * 40),
                               ('runtime_age_path', '/root/.ssh/id_ed25519'), ('netbird_subnet', '100.91.0.0/16'), ('server_ip', '100.90.0.11')]:
                changed = deepcopy(cfg); changed[key] = value; cases.append(changed)
            changed = deepcopy(cfg); changed['origins']['auth'] = 'https://wrong.example.invalid'; cases.append(changed)
            changed = deepcopy(cfg); changed['hostnames'] = ['www.example.invalid']; cases.append(changed)
            for index, changed in enumerate(cases):
                output = root / ('rejected-' + str(index))
                with self.subTest(index=index), self.assertRaises((ValueError, KeyError)):
                    host.prepare.prepare(changed, payload, bundle, lock, output)
                self.assertFalse(output.exists())
            metadata_path = payload / 'recovery.json'
            metadata = json.loads(metadata_path.read_text()); metadata.pop('application_image')
            metadata_path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'requires a saved CI'):
                host.prepare.prepare(cfg, payload, bundle, lock, root / 'legacy')

    def test_paths_and_external_networks_cannot_escape_the_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, payload, bundle, lock = fixture(root)
            for value in ['/opt', '/var/run/docker.sock', '/opt/app/../escape', '/opt/hou1-netbird']:
                changed = deepcopy(cfg); changed['deployment']['paths']['app'] = value
                with self.subTest(path=value), self.assertRaises(ValueError):
                    host.prepare.prepare(changed, payload, bundle, lock, root / 'rejected')
            path = payload / 'app-config/docker-compose.override.yml'
            original = yaml.safe_load(path.read_text())
            for change in ['socket', 'interpolated-mount', 'foreign-network', 'proxy-address', 'build']:
                override = deepcopy(original)
                if change == 'socket': override['services']['nginx']['volumes'] = ['/var/run/docker.sock:/socket']
                if change == 'interpolated-mount': override['services']['nginx']['volumes'] = ['${UNREVIEWED_PATH}:/socket']
                if change == 'foreign-network': override['networks']['netbird-ingress']['name'] = 'foreign-ingress'
                if change == 'proxy-address': override['services']['nginx']['networks']['netbird-ingress']['ipv4_address'] = '172.29.20.3'
                if change == 'build': override['services']['nginx']['build'] = '.'
                path.write_text(yaml.safe_dump(override))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    host.prepare.prepare(cfg, payload, bundle, lock, root / 'rejected')
                self.assertFalse((root / 'rejected').exists())

    def test_production_inventory_guard_requires_independent_console_and_registry_access(self):
        play = yaml.safe_load((ROOT / 'ansible/playbooks/restore-production-host.yml').read_text())[0]
        guard = play['pre_tasks'][0]
        variables = dict(cms_environment='production', authentik_environment='prod', netbird_server_group='prod-servers',
                         netbird_subnet='100.90.0.0/16', node_bootstrap=True, ansible_host='198.51.100.10',
                         prod_vps_public_ip='198.51.100.10', host_recovery_id='fixture-01', host_recovery_request=request(),
                         ansible_facts=dict(distribution='Debian', distribution_major_version='12', architecture='x86_64'),
                         host_recovery_console_confirmed=True, hou1_controller_managed_tls=True,
                         application_image_registry_secret_source='/private/registry.enc.json',
                         application_image_registry_age_key_file='/private/temporary.key')
        inventory = {'all': {'children': {name: {'hosts': {'fixture': {}}} for name in
                     ['production_recovery', 'prod_env', 'hou1_prod_init']}}}
        scenarios = [{}, {'cms_environment': 'test'}, {'node_bootstrap': False},
                     {'host_recovery_console_confirmed': False}, {'hou1_controller_managed_tls': False},
                     {'application_image_registry_secret_source': ''}]
        plays = [dict(guard_play({**variables, **change}, [guard], guard['name'] if change else None), hosts='fixture')
                 for change in scenarios]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_publication_rejects_a_test_restore_before_network_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'restore-report.json').write_text(json.dumps({'passed': True, 'environment': 'test'}))
            with patch.object(host.restore, 'ROOT', root), patch.object(host.restore, 'run') as run:
                with self.assertRaises(ValueError): host.restore.publish(request())
                run.assert_not_called()

    def test_inventory_contract_rejects_changed_paths_or_origins(self):
        cfg = request()
        paths, projects, networks = [cfg['deployment'][key] for key in ['paths', 'projects', 'networks']]
        variables = dict(host_recovery_request=cfg, host_recovery_id=cfg['id'], cms_environment='production',
                         authentik_environment='prod', netbird_subnet=cfg['netbird_subnet'], netbird_ip=cfg['server_ip'], deploy_ref=cfg['staff_revision'],
                         authentik_age_key_file=cfg['runtime_age_path'], app_directory=paths['app'],
                         authentik_project_root_base='/opt/hou1-authentik', authentik_data_root_base='/var/lib/hou1-authentik',
                         netbird_control_plane_directory=paths['netbird'], cms_data_directory=paths['cms'],
                         authentik_identity_profiles={'prod': {'issuer_host': 'identity.example.invalid',
                                                              'compose_project': projects['auth']}},
                         authentik_active_network=networks['auth'], netbird_control_plane_project=projects['netbird'],
                         netbird_ingress_network=networks['netbird'], netbird_ingress_subnet=networks['subnet'],
                         netbird_ingress_dynamic_range=networks['dynamic_range'], netbird_ingress_proxy_ip=networks['proxy_ip'],
                         staff_domain='staff.example.invalid', public_site_origin=cfg['origins']['public'],
                         netbird_management_url=cfg['origins']['netbird'])
        tasks = yaml.safe_load((ROLES / 'host_recovery/tasks/request.yml').read_text())
        guard = tasks[-1]
        scenarios = [{}, {'app_directory': '/opt/wrong-environment'}, {'public_site_origin': 'https://wrong.example.invalid'},
                     {'deploy_ref': 'b' * 40}]
        result = run_plays([guard_play({**variables, **change}, tasks, guard['name'] if change else None)
                            for change in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_certificates_do_not_reinstall_acme_state_or_dns_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; destination = root / 'restored'
            for name in ['live/example', 'archive/example', 'accounts', 'renewal-hooks']:
                (source / name).mkdir(parents=True)
            (source / 'archive/example/key1.pem').write_text('synthetic key fixture')
            (source / 'live/example/privkey.pem').symlink_to('../../archive/example/key1.pem')
            (source / 'cloudflare.ini').write_text('synthetic DNS credential fixture')
            (source / 'accounts/registration').write_text('synthetic account fixture')
            with patch.object(host.restore.os, 'chown'):
                host.restore.copy_certificates('production', source, destination)
            self.assertEqual({path.name for path in destination.iterdir()}, {'live', 'archive'})
            self.assertEqual((destination / 'live/example/privkey.pem').read_text(), 'synthetic key fixture')
            self.assertTrue((destination / 'live/example/privkey.pem').is_symlink())

    def test_successful_production_publication_uses_the_completed_restore_contract(self):
        cfg = request(); cfg['application_image'] = images.receipt(cfg['staff_revision'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = dict(passed=True, environment='production', staff_revision=cfg['staff_revision'],
                              application_image=cfg['application_image'], ports_bound_to_loopback=True,
                              sessions_revoked_before_writers=True)
            (root / 'restore-report.json').write_text(json.dumps(checkpoint))
            projects = {}
            for project, service, ports in [('netbird', 'netbird-server', ['127.0.0.1:3478:3478/udp']),
                                            ('app', 'nginx', ['127.0.0.1:80:80', '127.0.0.1:443:443'])]:
                path = root / project; path.mkdir()
                projects[project] = (cfg['deployment']['projects'][project], path)
                (path / ('docker-compose.yml' if project == 'app' else 'compose.yaml')).write_text(
                    json.dumps({'services': {service: {'ports': ports}}}))
            with patch.object(host.restore, 'ROOT', root), patch.object(host.restore, 'PROJECTS', projects), \
                 patch.object(host.restore, 'run'), patch.object(host.restore, 'dc') as dc:
                report = host.restore.publish(cfg)
            self.assertEqual(report['environment'], 'production')
            self.assertTrue(report['host_enrollment_pending'])
            self.assertEqual(dc.call_count, 3)
            self.assertEqual(json.loads((root / 'app/docker-compose.yml').read_text())['services']['nginx']['ports'],
                             ['80:80', '443:443'])


if __name__ == '__main__':
    unittest.main()
