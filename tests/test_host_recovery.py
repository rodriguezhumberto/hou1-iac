"""Replacement-host guards use synthetic identities, source and certificates."""
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import guard_play, run_plays
import test_recovery_drill as drill_tests

FILES = Path(__file__).resolve().parents[1] / 'ansible/playbooks/roles/host_recovery/files'


def load(name):
    spec = importlib.util.spec_from_file_location('host_recovery_' + name, FILES / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare')
restore = load('restore')


def inputs():
    return dict(id='fixture-01', environment='test', staff_revision='a' * 40,
                test_suffix='vps-test.example.invalid', netbird_subnet='100.91.0.0/16', server_ip='100.91.0.10',
                bootstrap_cidr='8.8.8.8/32', runtime_age_path='/root/.config/sops/age/fixture-test.key',
                deny_cidrs=['192.0.2.0/24'], test_addresses=['198.51.100.10'],
                allow_local_cidrs=['100.91.0.0/16'], allow_update_cidrs=[],
                deployment=dict(paths=dict(app='/opt/site-test', auth='/opt/hou1-authentik/test',
                    auth_data='/var/lib/hou1-authentik/test', netbird='/opt/hou1-netbird', cms='/var/lib/site/test/cms'),
                    projects=dict(app='site-test', auth='hou1-authentik-test', netbird='hou1-netbird-test'),
                    networks=dict(auth='hou1-authentik-test', netbird='hou1-netbird-ingress', subnet='172.30.80.0/29',
                                  dynamic_range='172.30.80.4/30', proxy_ip='172.30.80.2')),
                origins=dict(auth='https://auth-test.vps-test.example.invalid', staff='https://staff-test.vps-test.example.invalid',
                             public='https://test.vps-test.example.invalid', netbird='https://netbird-test.vps-test.example.invalid'),
                hostnames=['auth-test.vps-test.example.invalid', 'staff-test.vps-test.example.invalid',
                           'test.vps-test.example.invalid', 'netbird-test.vps-test.example.invalid'])


def lock():
    return {name: repository + '@sha256:' + 'b' * 64 for name, repository in prepare.REPOSITORIES.items()}


class HostRecoveryPreparationTest(unittest.TestCase):
    def test_resolver_unlock_skips_normal_files_and_propagates_failures(self):
        task = yaml.safe_load((FILES.parents[1] / 'common/tasks/main.yml').read_text())[1]
        self.assertNotIn('ignore_errors', task)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / 'unlock-attempted'
            # Exercise the production conditions without changing host attributes.
            probe = {**task, 'ansible.builtin.command': {
                'argv': ['python3', '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).touch(); sys.exit(1)', str(marker)]}}
            probe.pop('ansible.builtin.file')
            scenarios = [{'isreg': True, 'attributes': []},
                         {'isreg': False, 'islnk': True}, {}]
            plays = [dict(variables={'hou1_resolver_stat': {'stat': stat}}, tasks=[probe]) for stat in scenarios]
            result = run_plays(plays)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(marker.exists())
            later = root / 'deployment-continued'
            result = run_plays([dict(variables={'hou1_resolver_stat': {
                'stat': {'isreg': True, 'attributes': ['immutable']}}}, tasks=[probe, {
                    'ansible.builtin.file': {'path': str(later), 'state': 'touch'}}])])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.exists())
            self.assertFalse(later.exists())

    def test_controller_refresh_is_explicit_and_failure_stops_handoff(self):
        tasks = [{'ansible.builtin.include_tasks': str(FILES.parent / 'tasks/refresh-management.yml')}]
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'refreshed'
            command = ['python3', '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)]
            result = run_plays([dict(variables={'host_recovery_management_refresh_argv': command}, tasks=tasks)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(marker.exists())
            plays = [guard_play({'host_recovery_management_refresh_argv': value}, tasks,
                                'Validate the optional dedicated controller VPN reconnect command')
                     for value in ['shell command', [], ['valid', 7]]]
            result = run_plays(plays)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            marker.unlink()
            result = run_plays([dict(variables={'host_recovery_management_refresh_argv': ['false']}, tasks=tasks + [
                {'ansible.builtin.file': {'path': str(marker), 'state': 'touch'}},
            ])])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def values(self):
        cfg = inputs()
        metadata = dict(environment='test', staff_revision=cfg['staff_revision'], recovery_files=[cfg['runtime_age_path']])
        app = dict(staff_vpn_subnet=cfg['netbird_subnet'], HOU1_VPN_IP='100.91.0.10',
                   STAFF_OIDC_ISSUER='https://auth-test.' + cfg['test_suffix'] + '/application/o/staff/',
                   STAFF_OIDC_REDIRECT_URI='https://staff-test.' + cfg['test_suffix'] + '/api/staff/oidc/callback',
                   PUBLIC_SITE_ORIGIN='https://test.' + cfg['test_suffix'])
        vpn = {'server': {'exposedAddress': 'https://netbird-test.' + cfg['test_suffix'] + ':443', 'disableGeoliteUpdate': True}}
        return cfg, metadata, lock(), app, vpn

    def test_raw_urls_image_locks_and_runtime_key_scope(self):
        cfg, metadata, images, app, vpn = self.values()
        self.assertEqual(prepare.validate(cfg, metadata, images, app, vpn)['netbird'],
                         'https://netbird-test.' + cfg['test_suffix'])
        for key, value in [('environment', 'prod'), ('staff_revision', 'main'),
                           ('runtime_age_path', '/root/.ssh/id_ed25519'), ('netbird_subnet', '100.90.0.0/16'),
                           ('bootstrap_cidr', '0.0.0.0/0'), ('test_suffix', 'example.com')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                prepare.validate({**cfg, key: value}, metadata, images, app, vpn)
        for bad in ['postgres:16.6-alpine', 'foreign.invalid/postgres@sha256:' + 'b' * 64]:
            with self.subTest(image=bad), self.assertRaises(ValueError):
                prepare.validate(cfg, metadata, {**images, 'postgresql': bad}, app, vpn)
        with self.assertRaises(ValueError):
            prepare.validate(cfg, metadata, images, {**app, 'STAFF_OIDC_ISSUER': 'https://auth.example.invalid'}, vpn)

    def test_rehearsal_recovery_still_requires_offline_geolocation_startup(self):
        cfg, metadata, images, app, vpn = self.values()
        for setting in [None, False, 'true']:
            candidate = {'server': {'exposedAddress': vpn['server']['exposedAddress']}}
            if setting is not None:
                candidate['server']['disableGeoliteUpdate'] = setting
            with self.subTest(setting=setting), self.assertRaisesRegex(ValueError, 'offline geolocation'):
                prepare.validate(cfg, metadata, images, app, candidate)
        self.assertEqual(prepare.validate(cfg, metadata, images, app, vpn)['netbird'], cfg['origins']['netbird'])

    def fixture(self, root):
        payload = drill_tests.RecoveryPreparationTest().fixture(root / 'payload')
        cfg, metadata, images, app, vpn = self.values()
        repository = root / 'source'
        repository.mkdir()
        subprocess.run(['git', 'init', '-b', 'recovery', str(repository)], capture_output=True, check=True)
        (repository / 'astro-app').mkdir()
        (repository / 'astro-app/Dockerfile').write_text('FROM node:24.22.2 AS builder\nFROM node:24.22.2 AS runtime\n')
        subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-m', 'synthetic fixture'], capture_output=True, check=True)
        cfg['staff_revision'] = metadata['staff_revision'] = subprocess.check_output(
            ['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
        bundle = root / 'source.bundle'
        subprocess.run(['git', '-C', str(repository), 'bundle', 'create', str(bundle), 'recovery'], capture_output=True, check=True)
        (payload / 'recovery.json').write_text(json.dumps(metadata))
        with (payload / 'app-config/.env').open('a') as out:
            for key, value in dict(staff_vpn_subnet='100.91.0.0/16', HOU1_VPN_IP='100.91.0.10',
                                   PRIMARY_HOSTNAMES='test.' + cfg['test_suffix'], SECONDARY_HOSTNAMES='test.' + cfg['test_suffix'],
                                   STAFF_HOSTNAME='staff-test.' + cfg['test_suffix'], PRIMARY_CERT_DIR='shared',
                                   SECONDARY_CERT_DIR='shared', STAFF_CERT_DIR='shared').items():
                out.write(key + '=' + value + '\n')
        (payload / 'netbird-files/config.yaml').write_text(yaml.safe_dump(vpn))
        (payload / 'netbird-files/compose.yaml').write_text(yaml.safe_dump({'services': {
            'netbird-server': {'image': 'netbirdio/netbird-server:0.82.0', 'ports': ['3478:3478/udp']},
            'dashboard': {'image': 'netbirdio/dashboard:v2.96.0'}}}))
        auth = yaml.safe_load((payload / 'authentik/compose.yaml').read_text())
        for service in auth['services'].values():
            service.pop('ports')
        (payload / 'authentik/compose.yaml').write_text(yaml.safe_dump(auth))
        app = yaml.safe_load((payload / 'app-config/docker-compose.yml').read_text())
        app['services']['astro-app']['build'] = {'context': '.'}
        app['services']['nginx'] = {'image': 'nginx:1.28.0-alpine', 'ports': ['80:80', '443:443']}
        app['services']['uptime-kuma'] = {'image': 'fixture'}
        (payload / 'app-config/docker-compose.yml').write_text(yaml.safe_dump(app))
        (payload / 'app-config/docker-compose.override.yml').write_text('services:\n  nginx: {}\n')
        images_file = root / 'images.json'
        images_file.write_text(json.dumps(images))
        return cfg, payload, bundle, images_file

    @unittest.skipUnless(shutil.which('openssl') and shutil.which('git'), 'openssl/git required')
    def test_real_bundle_revision_and_saved_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg, payload, bundle, images = self.fixture(root)
            output = root / 'stage'
            report = prepare.prepare(cfg, payload, bundle, images, output)
            self.assertTrue(report['prepared'])
            web = json.loads((output / 'app.compose.json').read_text())
            self.assertNotIn('build', web['services']['astro-app'])
            self.assertEqual(web['services']['astro-app']['image'], 'hou1-restored-staff:' + cfg['staff_revision'])
            self.assertIn('HOST=0.0.0.0', web['services']['astro-app']['environment'])
            self.assertIn('FROM node@sha256:', (output / 'Dockerfile.recovery').read_text())
            self.assertIn('deny all;', (output / 'authentik.conf').read_text())
            self.assertIn('location = /api/setup', (output / 'netbird.conf').read_text())
            with tarfile.open(output / 'bundle.tar') as archive:
                self.assertIn('staff.bundle', archive.getnames())
                self.assertIn('payload/app-config/.env', archive.getnames())
            bad = yaml.safe_load((payload / 'app-config/docker-compose.override.yml').read_text())
            bad['services']['nginx']['volumes'] = ['/var/run/docker.sock:/socket']
            (payload / 'app-config/docker-compose.override.yml').write_text(yaml.safe_dump(bad))
            with self.assertRaises(ValueError):
                prepare.prepare(cfg, payload, bundle, images, root / 'escaped')
            self.assertFalse((root / 'escaped').exists())

    def test_recovery_of_a_restored_netbird_requires_the_exact_locked_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg, payload, bundle, images = self.fixture(root)
            saved = payload / 'netbird-files/compose.yaml'
            vpn = yaml.safe_load(saved.read_text())
            pinned = json.loads(images.read_text())['netbird-server']
            vpn['services']['netbird-server']['image'] = pinned
            saved.write_text(yaml.safe_dump(vpn))
            output = root / 'pinned'
            self.assertTrue(prepare.prepare(cfg, payload, bundle, images, output)['prepared'])
            self.assertEqual(json.loads((output / 'netbird.compose.json').read_text())[
                'services']['netbird-server']['image'], pinned)
            for index, image in enumerate([
                'netbirdio/netbird-server@sha256:' + 'c' * 64,
                'foreign.invalid/netbird-server@sha256:' + 'b' * 64,
                'netbirdio/netbird-server:0.83.0',
            ]):
                with self.subTest(image=image):
                    vpn['services']['netbird-server']['image'] = image
                    saved.write_text(yaml.safe_dump(vpn))
                    rejected = root / ('rejected-' + str(index))
                    with self.assertRaises(ValueError):
                        prepare.prepare(cfg, payload, bundle, images, rejected)
                    self.assertFalse(rejected.exists())

    def test_publication_starts_dns_before_controller_refresh_and_private_ssh(self):
        play = {'tasks': yaml.safe_load((FILES.parent / 'tasks/publish.yml').read_text())}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver, refreshed, cutover = [root / name for name in ['resolver', 'refreshed', 'cutover']]
            tasks = []
            for task in play['tasks']:
                role = task.get('ansible.builtin.include_role', {}).get('name')
                if role == 'netbird_dns':
                    tasks.append({'ansible.builtin.file': {'path': str(resolver), 'state': 'touch'}})
                elif task.get('ansible.builtin.include_tasks') == 'roles/host_recovery/tasks/refresh-management.yml':
                    tasks.append({'ansible.builtin.include_tasks': str(FILES.parent / 'tasks/refresh-management.yml')})
                elif role == 'security':
                    tasks.append({'ansible.builtin.command': {'argv': ['python3', '-c',
                        'from pathlib import Path; import sys; assert Path(sys.argv[1]).is_file(); Path(sys.argv[2]).touch()',
                        str(refreshed), str(cutover)]}})
            self.assertEqual(len(tasks), 3)
            command = ['python3', '-c',
                       'from pathlib import Path; import sys; assert Path(sys.argv[1]).is_file(); Path(sys.argv[2]).touch()',
                       str(resolver), str(refreshed)]
            result = run_plays([dict(variables={'host_recovery_management_refresh_argv': command}, tasks=tasks)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(cutover.is_file())

    def test_live_inventory_and_missing_console_are_refused(self):
        plays = yaml.safe_load((FILES.parents[2] / 'restore-test-host.yml').read_text())
        guard = plays[0]['pre_tasks'][0]
        variables = dict(cms_environment='test', node_bootstrap=True, ansible_host='198.51.100.10',
                         test_vps_public_ip='198.51.100.10', ansible_facts={'distribution': 'Debian', 'distribution_major_version': '12', 'architecture': 'x86_64'},
                         host_recovery_console_confirmed=True, host_recovery_id='fixture-01',
                         app_directory='/opt/pearlandpaws-test', authentik_environment='test', netbird_version='0.82.0')
        inventory = {'all': {'children': {'isolated_test_vps': {'hosts': {'fixture': {}}}}}}
        tests = [dict(guard_play({**variables, **changes}, [guard], guard['name'] if changes else None), hosts='fixture')
                 for changes in [{}, {'node_bootstrap': False}, {'cms_environment': 'prod'}, {'host_recovery_console_confirmed': False}]]
        result = run_plays(tests, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lost_peer_guard_refuses_live_or_different_devices(self):
        play = {'tasks': yaml.safe_load((FILES.parent / 'tasks/publish.yml').read_text())}
        block = next(task['block'] for task in play['tasks'] if 'block' in task)
        guard = next(task for task in block if task['name'] == 'Refuse another device or a connected peer')
        peer = dict(id='lost-hou1', name='fixture-host', ip='100.91.0.10', connected=False)
        variables = dict(host_recovery_lost_peer_id=peer['id'], host_recovery_lost_peer_name=peer['name'], netbird_ip=peer['ip'])
        scenarios = [{}, {'connected': True}, {'id': 'different'}, {'ip': '100.91.0.22'}]
        plays = [guard_play({**variables, 'host_recovery_old_peer': {'json': {**peer, **change}}},
                            [guard], guard['name'] if change else None) for change in scenarios]
        result = run_plays(plays)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HostRecoveryRuntimeTest(unittest.TestCase):
    def setUp(self):
        restore.configure(inputs())

    def test_publication_validates_both_baselines_before_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'restore-report.json').write_text('{"passed":true}')
            projects = {key: (key, root / key) for key in ['netbird', 'app']}
            for name, (_, path) in projects.items():
                path.mkdir()
                file = path / ('docker-compose.yml' if name == 'app' else 'compose.yaml')
                value = {'services': {'nginx' if name == 'app' else 'netbird-server': {
                    'ports': ['443:443'] if name == 'app' else ['127.0.0.1:3478:3478/udp']}}}
                file.write_text(json.dumps(value))
            before = (root / 'netbird/compose.yaml').read_bytes()
            with patch.object(restore, 'ROOT', root), patch.object(restore, 'PROJECTS', projects), \
                 patch.object(restore, 'run') as run, patch.object(restore, 'dc') as dc:
                with self.assertRaises(ValueError):
                    restore.publish(inputs())
                run.assert_not_called()
                dc.assert_not_called()
            self.assertEqual((root / 'netbird/compose.yaml').read_bytes(), before)

    def test_existing_roots_and_symlinks_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            restore.paths_absent([root / 'new'])
            for path in [root, root / 'broken-link']:
                if path != root:
                    path.symlink_to(root / 'absent')
                with self.assertRaises(ValueError):
                    restore.paths_absent([path])
            with self.assertRaises(ValueError):
                restore.paths_absent([root / 'broken-link/nested'])

    def test_public_ports_held_on_loopback(self):
        configs = {'netbird': {'services': {'netbird-server': {'ports': ['3478:3478/udp']}}},
                   'app': {'services': {'nginx': {'ports': ['80:80', '443:443']}}}}
        restore.hold_public_ports(configs)
        self.assertEqual(configs['app']['services']['nginx']['ports'], ['127.0.0.1:80:80', '127.0.0.1:443:443'])
        self.assertEqual(configs['netbird']['services']['netbird-server']['ports'], ['127.0.0.1:3478:3478/udp'])

    def test_empty_import_and_revocation_precede_writers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'payload/authentik').mkdir(parents=True)
            (root / 'payload/authentik/postgresql.sql').write_text('-- synthetic logical dump')
            events = []
            def dc(project, *args, **kwargs):
                events.append((project, args))
                return b''
            with patch.object(restore, 'ROOT', root), patch.object(restore, 'install'), \
                 patch.object(restore, 'dc', side_effect=dc), patch.object(restore, 'wait_database'), \
                 patch.object(restore, 'sql', side_effect=['0', '187']), \
                 patch.object(restore, 'revoke_sessions', side_effect=lambda path: events.append(('revoke', ()))), \
                 patch.object(restore, 'reset_peer_connections', side_effect=lambda path: events.append(('reset-peers', ())) or 2):
                report = restore.restore(inputs())
            imported = next(i for i, event in enumerate(events) if event[1] and event[1][0] == 'exec' and 'psql' in event[1])
            revoked = events.index(('revoke', ()))
            started = next(i for i, event in enumerate(events) if 'authentik-server' in event[1])
            self.assertLess(imported, revoked)
            self.assertLess(revoked, started)
            reset = events.index(('reset-peers', ()))
            netbird_started = next(i for i, event in enumerate(events) if event[0] == 'netbird' and 'up' in event[1])
            self.assertLess(reset, netbird_started)
            self.assertTrue(report['netbird_connection_flags_reset_before_server'])
            self.assertEqual(report['netbird_stale_connections_cleared'], 2)
            self.assertEqual(report['postgres_tables'], 187)
            self.assertFalse(report['identity_bootstrap_run'])
            self.assertFalse(report['cms_seed_run'])

    def test_stale_peer_status_cleared_offline_without_changing_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'store.db'
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE peers (id TEXT, key TEXT, peer_status_connected INTEGER,
                    peer_status_last_seen TEXT, user_id TEXT);
                    INSERT INTO peers VALUES ('lost', 'key-a', 1, '2026-11-09', 'owner-a');
                    INSERT INTO peers VALUES ('retained', 'key-b', 0, '2026-11-08', 'owner-b');
                    CREATE TABLE group_peers (group_id TEXT, peer_id TEXT);
                    INSERT INTO group_peers VALUES ('admins', 'retained');''')
            with patch.object(restore, 'run', return_value=b'live-server'):
                with self.assertRaises(ValueError):
                    restore.reset_peer_connections(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT peer_status_connected FROM peers WHERE id="lost"').fetchone(), (1,))
            with patch.object(restore, 'run', return_value=b''):
                self.assertEqual(restore.reset_peer_connections(path), 1)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT * FROM peers ORDER BY id').fetchall(), [
                    ('lost', 'key-a', 0, '2026-11-09', 'owner-a'),
                    ('retained', 'key-b', 0, '2026-11-08', 'owner-b')])
                self.assertEqual(db.execute('SELECT * FROM group_peers').fetchall(), [('admins', 'retained')])
            with patch.object(restore, 'run', return_value=b''):
                with self.assertRaises(sqlite3.OperationalError):
                    restore.reset_peer_connections(Path(temp) / 'missing.db')
                self.assertFalse((Path(temp) / 'missing.db').exists())

    def test_live_writers_refused_and_real_sqlite_sessions_revoked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'forms.db'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE staff_sessions (revoked_at INTEGER); INSERT INTO staff_sessions VALUES (NULL);')
            with patch.object(restore, 'run', return_value=b'writer'), patch.object(restore, 'sql') as sql:
                with self.assertRaises(ValueError):
                    restore.revoke_sessions(path)
                sql.assert_not_called()
            with patch.object(restore, 'run', return_value=b''), patch.object(restore, 'sql', return_value='0'):
                restore.revoke_sessions(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0], 0)

    def test_failure_latches_and_stops_only_recovery_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / inputs()['id']
            root.mkdir()
            (root / 'request.json').write_text(json.dumps(inputs()))
            projects = {name: (name, base) for name in ['auth', 'netbird', 'app']}
            with patch.object(restore, 'ROOT', root), patch.object(restore, 'RECOVERY_BASE', base), \
                 patch.object(restore, 'configure'), patch.object(restore, 'PROJECTS', projects), patch.object(restore, 'restore', side_effect=RuntimeError), \
                 patch.object(restore, 'dc') as dc:
                with self.assertRaises(RuntimeError):
                    restore.main('restore')
                self.assertEqual(dc.call_count, 3)
                self.assertTrue(all(call.args[1] == 'stop' for call in dc.call_args_list))
                with self.assertRaises(ValueError):
                    restore.main('restore')
                self.assertEqual(dc.call_count, 3)


if __name__ == '__main__':
    unittest.main()
