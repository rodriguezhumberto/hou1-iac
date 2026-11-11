"""Recovery regressions use generated certificates and synthetic data, never VPS keys."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import yaml

from helpers import guard_play, run_plays

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'ansible/playbooks/roles/recovery_drill/files'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('recovery_prepare', FILES / 'prepare.py')
driver = load('recovery_driver', FILES / 'driver.py')


def request():
    return dict(environment='test', project='hou1-recovery-drill-fixture-01',
                root='/var/tmp/hou1-recovery-drill-fixture-01', staff_revision='a' * 40,
                test_suffix='vps-test.example.invalid', app_source='/opt/staff-test',
                primary_containers=['auth-test-server', 'auth-test-worker', 'auth-test-db',
                                    'netbird-test-server', 'netbird-test-dashboard', 'app-test', 'proxy-test'],
                admin_username='vps-staff-admin', reader_username='vps-staff-reader')


class RecoveryPreparationTest(unittest.TestCase):
    def test_origin_validation_precedes_default_port_normalization(self):
        host = 'netbird-test.' + request()['test_suffix']
        for value in ['https://' + host, 'https://' + host + ':443/']:
            with self.subTest(value=value):
                self.assertEqual(prepare.isolated_origin(value, request()['test_suffix']), 'https://' + host)
        for value in ['http://' + host, 'https://' + host + ':8443',
                      'https://user:password@' + host, 'https://netbird.example.invalid:443',
                      'https://' + host + '.foreign.invalid', 'https://unsafe_host.' + request()['test_suffix']]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare.isolated_origin(value, request()['test_suffix'])

    def fixture(self, root):
        for folder in ['authentik', 'netbird-files/data', 'app-config/certbot/conf/live/shared',
                       'cms/database', 'cms/media']:
            (root / folder).mkdir(parents=True)
        (root / 'recovery.json').write_text(json.dumps({'environment': 'test', 'staff_revision': 'a' * 40}))
        (root / 'app-config/.env').write_text(
            'STAFF_OIDC_ISSUER=https://auth-test.vps-test.example.invalid/application/o/staff/\n'
            'STAFF_OIDC_REDIRECT_URI=https://staff-test.vps-test.example.invalid/api/staff/oidc/callback\n'
            'PUBLIC_SITE_ORIGIN=https://test.vps-test.example.invalid\n')
        app = {'environment': ['HOST=0.0.0.0', 'NODE_ENV=production'],
               'healthcheck': {'test': ['CMD', 'curl', '--fail', 'http://localhost:4321/api/health.json']}}
        (root / 'app-config/docker-compose.yml').write_text(yaml.safe_dump({'services': {'astro-app': app}}))
        auth = {name: {'image': 'fixture', 'ports': ['9000:9000']} for name in ['postgresql', 'authentik-server', 'authentik-worker']}
        (root / 'authentik/compose.yaml').write_text(yaml.safe_dump({'services': auth}))
        (root / 'authentik/authentik.env').write_text('POSTGRES_USER=authentik\n')
        (root / 'netbird-files/compose.yaml').write_text(yaml.safe_dump({'services': {'netbird-server': {'image': 'fixture'}, 'dashboard': {'image': 'fixture'}}}))
        (root / 'netbird-files/dashboard.env').write_text('AUTH_AUDIENCE=fixture\n')
        (root / 'netbird-files/config.yaml').write_text(yaml.safe_dump({'server': {'exposedAddress': 'https://netbird-test.vps-test.example.invalid'}}))
        cert = root / 'app-config/certbot/conf/live/shared'
        hosts = ['auth-test', 'staff-test', 'netbird-test', 'test']
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=test.vps-test.example.invalid', '-addext',
                        'subjectAltName=' + ','.join('DNS:' + host + '.vps-test.example.invalid' for host in hosts),
                        '-keyout', str(cert / 'privkey.pem'), '-out', str(cert / 'fullchain.pem')],
                       capture_output=True, check=True)
        return root

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required')
    def test_saved_netbird_default_port_matches_discovery_issuer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = self.fixture(root / 'payload')
            origin = 'https://netbird-test.' + request()['test_suffix']
            (payload / 'netbird-files/config.yaml').write_text(
                yaml.safe_dump({'server': {'exposedAddress': origin + ':443'}}))
            env = payload / 'app-config/.env'
            env.write_text(env.read_text().replace(
                'https://auth-test.vps-test.example.invalid/',
                'https://auth-test.vps-test.example.invalid:443/'))
            stage = root / 'stage'
            prepare.prepare(request(), payload, stage)
            cfg = json.loads((stage / 'drill-input.json').read_text())
            self.assertEqual(cfg['netbird_origin'] + '/oauth2', origin + '/oauth2')
            self.assertEqual(cfg['issuer'],
                             'https://auth-test.vps-test.example.invalid:443/application/o/staff/')

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required')
    def test_saved_shared_certificate_and_app_runtime_survive_preparation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = self.fixture(root / 'payload')
            stage = root / 'stage'
            report = prepare.prepare(request(), payload, stage)
            self.assertTrue(report['prepared'])
            compose = json.loads((stage / 'compose.json').read_text())
            app = compose['services']['astro-app']
            self.assertIn('HOST=0.0.0.0', app['environment'])
            self.assertEqual(app['healthcheck']['test'][-1], 'http://localhost:4321/api/health.json')
            self.assertTrue(compose['networks']['recovery']['internal'])
            for service in compose['services'].values():
                self.assertFalse(service.get('ports'))
                self.assertTrue(all(v.startswith(request()['root'] + '/') for v in service.get('volumes', [])))
            mapping = json.loads((stage / 'certificate-mapping.json').read_text())
            self.assertEqual(mapping['staff-test.vps-test.example.invalid'], 'shared')
            if shutil.which('docker'):
                local_compose = copy.deepcopy(compose)
                for service in local_compose['services'].values():
                    service['env_file'] = [str(payload / str(path).removeprefix('./payload/'))
                                           for path in service.get('env_file', [])]
                result = subprocess.run(['docker', 'compose', '-f', '-', 'config', '--format', 'json'],
                                        input=json.dumps(local_compose), text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                resolved = json.loads(result.stdout)['services']['astro-app']
                self.assertEqual(resolved['environment']['HOST'], '0.0.0.0')
                self.assertEqual(resolved['healthcheck']['test'][-1], 'http://localhost:4321/api/health.json')
            with tarfile.open(stage / 'bundle.tar') as archive:
                names = archive.getnames()
                self.assertIn('original-cms/database', names)
                self.assertIn('safe_archive.py', names)
                self.assertIn('driver.py', names)
            self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValueError):
                prepare.prepare(request(), payload, stage)
            # A private key outside the recovered certificate tree is never selected.
            key = payload / 'app-config/certbot/conf/live/shared/privkey.pem'
            outside = root / 'outside.pem'
            key.rename(outside)
            key.symlink_to(outside)
            with self.assertRaises(ValueError):
                prepare.prepare(request(), payload, root / 'unsafe-stage')
            self.assertFalse((root / 'unsafe-stage').exists())

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required')
    def test_saved_external_network_and_endpoint_cannot_escape_test(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = self.fixture(root / 'payload')
            netbird = payload / 'netbird-files/compose.yaml'
            original = yaml.safe_load(netbird.read_text())
            unsafe = copy.deepcopy(original)
            unsafe['services']['netbird-server']['network_mode'] = 'host'
            netbird.write_text(yaml.safe_dump(unsafe))
            with self.assertRaises(ValueError):
                prepare.prepare(request(), payload, root / 'escaped-network')
            self.assertFalse((root / 'escaped-network').exists())
            netbird.write_text(yaml.safe_dump(original))
            env = payload / 'app-config/.env'
            env.write_text(env.read_text().replace('https://auth-test.vps-test.example.invalid', 'https://auth.example.invalid'))
            with self.assertRaises(ValueError):
                prepare.prepare(request(), payload, root / 'production-endpoint')
            self.assertFalse((root / 'production-endpoint').exists())

    def test_production_paths_mutable_refs_and_colliding_containers_rejected(self):
        metadata = {'environment': 'test', 'staff_revision': 'a' * 40}
        for changes in [dict(environment='prod'), dict(root='/opt/production'), dict(project='production'),
                        dict(staff_revision='main'), dict(test_suffix='example.com'),
                        dict(app_source='/opt/production'), dict(primary_containers=['app-test'] * 7)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                prepare.validate({**request(), **changes}, metadata)

    def test_ansible_rejects_production_and_mismatched_restore_root(self):
        guard = yaml.safe_load((FILES.parent / 'tasks/main.yml').read_text())[0]
        inventory = {'all': {'children': {'isolated_test_vps': {'hosts': {'fixture': {}}}}}}
        variables = dict(cms_environment='test', recovery_drill_id='fixture-01',
                         recovery_drill_root=request()['root'], recovery_drill_project=request()['project'],
                         recovery_drill_staff_ref='a' * 40, recovery_drill_evidence_directory='/private/evidence',
                         recovery_drill_archive='/private/recovery.age', recovery_drill_manifest='/private/manifest.json',
                         recovery_drill_age_identity='/private/offline.agekey', recovery_drill_reference_jwks='/private/jwks.json',
                         recovery_drill_app_source='/opt/staff-test')
        plays = [dict(guard_play({**variables, **changes}, [guard], guard['name'] if changes else None), hosts='fixture')
                 for changes in [{}, {'cms_environment': 'prod'}, {'recovery_drill_staff_ref': 'main'},
                                 {'recovery_drill_root': '/opt/production'}]]
        result = run_plays(plays, inventory_source=inventory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_controller_plaintext_cleanup_runs_even_when_remote_teardown_fails(self):
        tasks = yaml.safe_load((FILES.parent / 'tasks/main.yml').read_text())
        cleanup = next(task for task in tasks if 'always' in task)['always'][0]
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp) / 'plaintext'
            staging.mkdir()
            (staging / 'secret').write_text('generated fixture only')
            block = copy.deepcopy(cleanup)
            block['block'] = [{'name': 'Fixture remote teardown failure', 'ansible.builtin.fail': {'msg': 'fixture'}}]
            result = run_plays([dict(variables={'recovery_drill_staging': {'path': str(staging)}}, tasks=[block])])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(staging.exists())

    def test_insufficient_headroom_blocks_an_additional_restore_stack(self):
        tasks = yaml.safe_load((FILES.parent / 'tasks/main.yml').read_text())
        code = next(task for task in tasks if task['name'] == 'Require headroom for the additional isolated stack')['ansible.builtin.command']['argv'][-1]
        for disk_gib, ram_gib, accepted in [(12, 6, True), (9, 6, False), (12, 3, False)]:
            with self.subTest(disk=disk_gib, ram=ram_gib), \
                 patch('os.statvfs', return_value=SimpleNamespace(f_bavail=disk_gib * 1024**3, f_frsize=1)), \
                 patch('pathlib.Path.read_text', return_value='MemAvailable: ' + str(ram_gib * 1024**2) + ' kB\n'):
                if accepted:
                    exec(code, {})
                else:
                    with self.assertRaises(AssertionError):
                        exec(code, {})


class RecoveryRuntimeTest(unittest.TestCase):
    def test_saved_fixture_hashes_are_checked_without_resetting_users_or_exposing_values(self):
        tree = ast.parse((FILES / 'identity_smoke.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_fixture_passwords')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<fixture-check>', 'exec'), namespace)
        credentials = {'TEST_ADMIN_PASSWORD': 'fixture-admin-password', 'TEST_READER_PASSWORD': 'fixture-reader-password'}
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        users = [(SimpleNamespace(password=digest(value)), key) for key, value in credentials.items()]
        before = [user.password for user, _ in users]
        verify = namespace['verify_fixture_passwords']
        checker = lambda raw, encoded: digest(raw) == encoded
        verify(users, credentials, checker)
        for supplied in [{}, {**credentials, 'TEST_ADMIN_PASSWORD': 'stale-fixture-password'},
                         {**credentials, 'TEST_READER_PASSWORD': None}]:
            with self.subTest(keys=list(supplied)), self.assertRaises(ValueError) as error:
                verify(users, supplied, checker)
            self.assertIn('selected point', str(error.exception))
            for value in [*credentials.values(), *before, 'stale-fixture-password']:
                self.assertNotIn(value, str(error.exception))
        self.assertEqual([user.password for user, _ in users], before)

    def test_fixture_credentials_use_stdin_before_any_http_login(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secrets = {'TEST_ADMIN_PASSWORD': 'synthetic-admin', 'TEST_READER_PASSWORD': 'synthetic-reader'}
            (root / 'health-input.json').write_text(json.dumps(secrets))
            (root / 'identity_smoke.py').write_text('# fixture identity verification\n')
            (root / 'start-result.json').write_text('{}')
            def execute(command, **kwargs):
                self.assertNotIn(secrets['TEST_ADMIN_PASSWORD'], ' '.join(command))
                self.assertNotIn(secrets['TEST_READER_PASSWORD'], ' '.join(command))
                payload = ast.parse(kwargs['input'].decode())
                self.assertEqual(ast.literal_eval(payload.body[0].value), secrets)
                kwargs['stdout'].write(b'RECOVERY_IDENTITY_REPORT={"fixture_passwords_match_recovery_point": true}\n')
                return subprocess.CompletedProcess(command, 0)
            with patch.object(driver, 'ROOT', root), patch.object(driver, 'config', return_value=request()), \
                 patch.object(driver.subprocess, 'run', side_effect=execute):
                self.assertTrue(driver.run('identity')['passed'])
            self.assertFalse((root / 'http-result.json').exists())
            for path in root.glob('*-report.json'):
                self.assertNotIn(secrets['TEST_ADMIN_PASSWORD'], path.read_text())
                self.assertNotIn(secrets['TEST_READER_PASSWORD'], path.read_text())

    def test_missing_fixture_inputs_latch_failure_without_starting_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'health-input.json').write_text('{}')
            (root / 'start-result.json').write_text('{}')
            with patch.object(driver, 'ROOT', root), patch.object(driver, 'config', return_value=request()), \
                 patch.object(driver.subprocess, 'run') as execute:
                with self.assertRaises(RuntimeError):
                    driver.run('identity')
                with self.assertRaises(ValueError):
                    driver.run('identity')
                execute.assert_not_called()
            self.assertEqual(json.loads((root / 'first-error.json').read_text())['phase'], 'identity')

    def runtime(self, root):
        (root / 'drill-input.json').write_text(json.dumps(request()))
        path = root / 'runtime.py'
        shutil.copyfile(FILES / 'runtime.py', path)
        return load('fixture_recovery_runtime', path)

    def test_database_query_readiness_ignores_initdb_and_app_waits_for_health(self):
        with tempfile.TemporaryDirectory() as temp:
            module = self.runtime(Path(temp))
            responses = [subprocess.CompletedProcess([], 1, b'', b'initializing'), subprocess.CompletedProcess([], 0, b'1\n', b'')]
            with patch.object(module.subprocess, 'run', side_effect=responses) as run, patch.object(module.time, 'sleep'):
                module.wait_postgresql()
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                argv = call.args[0]
                self.assertEqual(argv[argv.index('-h') + 1], '127.0.0.1')
                self.assertEqual(argv[argv.index('-d') + 1], 'authentik')
                self.assertEqual(argv[-1], 'SELECT 1')
            def state(health):
                return [{'Name': '/fixture', 'State': {'Running': True, 'Status': 'running', 'Health': {'Status': health}}}]
            with patch.object(module, 'inspect', side_effect=[state('starting'), state('healthy')]) as inspect, patch.object(module.time, 'sleep'):
                module.wait_ready(['astro-app'])
            self.assertEqual(inspect.call_count, 2)

    def test_session_cleanup_requires_stopped_writers_and_revokes_real_sqlite_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            module = self.runtime(root)
            path = root / 'payload/cms/database/forms.db'
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE staff_sessions (revoked_at INTEGER); INSERT INTO staff_sessions VALUES (NULL);')
            with patch.object(module, 'run', return_value=b'running-writer'), patch.object(module, 'sql') as sql:
                with self.assertRaises(AssertionError):
                    module.reset_restored_sessions('rejected')
                sql.assert_not_called()
            cleared = False
            def query(text):
                nonlocal cleared
                if text.startswith('BEGIN; TRUNCATE '):
                    cleared = True
                    return ''
                return '0' if cleared else '1'
            with patch.object(module, 'run', return_value=b''), patch.object(module, 'sql', side_effect=query):
                report = module.reset_restored_sessions('fixture')
            self.assertEqual(report['unrevoked_staff_rows_before'], 1)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0], 0)

    def test_failed_phase_latches_and_forbids_a_silent_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(driver, 'ROOT', root), patch.object(driver, 'config', return_value=request()), \
                 patch.object(driver.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
                with self.assertRaises(RuntimeError):
                    driver.run('build')
                with self.assertRaises(ValueError):
                    driver.run('build')
                self.assertEqual(run.call_count, 1)
                self.assertTrue((root / 'first-error.json').exists())
                self.assertFalse((root / 'build-result.json').exists())


if __name__ == '__main__':
    unittest.main()
