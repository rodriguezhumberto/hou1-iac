"""Source checkout migration, managed assets and portable backup identity."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import ROLES, ROOT, run_play
import test_application_image as images
import test_backup_protocol as backups

spec = importlib.util.spec_from_file_location('install_source', ROLES / 'webstack/files/install-source.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
artifact = images.artifact


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL,
                                   text=True).strip()


def source_fixture(root):
    source = root / 'repository'
    source.mkdir()
    git(source, 'init', '--initial-branch=main')
    git(source, 'config', 'user.name', 'Source fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'nginx').mkdir()
    (source / 'nginx/nginx.conf.template').write_text('first template\n')
    (source / 'nginx/retired.conf').write_text('retained until next revision\n')
    (source / 'landing-page').mkdir()
    (source / 'landing-page/index.html').write_text('first landing\n')
    (source / 'README').write_text('source only\n')
    (source / 'docker-compose.yml').write_text(yaml.safe_dump({'services': {
        'astro-app': {'build': {'context': '.'}, 'volumes': ['./database:/var/data']},
        'nginx': {'image': 'nginx:alpine', 'volumes': ['./nginx/nginx.conf.template:/etc/nginx/templates/default.conf.template:ro']}}}))
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'initial')
    return source, git(source, 'rev-parse', 'HEAD')


class SourceRuntimeTest(unittest.TestCase):
    def test_migration_update_removal_and_repeat_preserve_runtime_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, revision = source_fixture(root)
            runtime = root / 'existing-project'
            subprocess.run(['git', 'clone', str(source), str(runtime)], check=True, capture_output=True)
            (runtime / '.env').write_text('SECRET=runtime-only\n')
            (runtime / 'docker-compose.override.yml').write_text('# retained ingress\n')
            (runtime / 'docker-compose.yml').write_text('generated image-only config\n')
            (runtime / 'database').mkdir()
            (runtime / 'database/forms.db').write_bytes(b'database retained')
            self.assertTrue(installer.install(source, runtime, revision))
            self.assertFalse(installer.install(source, runtime, revision))
            self.assertEqual((runtime / 'application-source.json').stat().st_mode & 0o777, 0o644)
            self.assertEqual(git(runtime, 'rev-parse', 'HEAD'), revision)
            (source / 'nginx/nginx.conf.template').write_text('second template\n')
            (source / 'nginx/retired.conf').unlink()
            git(source, 'add', '-A'); git(source, 'commit', '-m', 'updated assets')
            next_revision = git(source, 'rev-parse', 'HEAD')
            self.assertTrue(installer.install(source, runtime, next_revision))
            self.assertFalse(installer.install(source, runtime, next_revision))
            self.assertEqual((runtime / 'nginx/nginx.conf.template').read_text(), 'second template\n')
            self.assertFalse((runtime / 'nginx/retired.conf').exists())
            self.assertEqual((runtime / '.env').read_text(), 'SECRET=runtime-only\n')
            self.assertEqual((runtime / 'database/forms.db').read_bytes(), b'database retained')
            self.assertEqual((runtime / 'docker-compose.override.yml').read_text(), '# retained ingress\n')
            self.assertEqual(artifact.source_directory(runtime), source)
            self.assertEqual(git(artifact.source_directory(runtime), 'rev-parse', 'HEAD'), next_revision)
            self.assertNotEqual(git(runtime, 'rev-parse', 'HEAD'), next_revision)

    def test_fresh_install_and_base_compose_are_regular_and_source_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, revision = source_fixture(root)
            runtime = root / 'runtime'
            installer.install(source, runtime, revision)
            result = run_play(dict(app_directory=str(runtime), app_source_directory=str(source),
                                   application_image_reference=images.IMAGE),
                              [{'ansible.builtin.import_tasks': str(ROLES / 'webstack/tasks/image-compose.yml')}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rendered = yaml.safe_load((runtime / 'docker-compose.yml').read_text())
            self.assertEqual(rendered['services']['astro-app']['image'], images.IMAGE)
            self.assertNotIn('build', rendered['services']['astro-app'])
            self.assertIn('build', yaml.safe_load((source / 'docker-compose.yml').read_text())['services']['astro-app'])
            self.assertEqual(git(source, 'status', '--porcelain'), '')
            self.assertFalse((runtime / '.git').exists())
            self.assertFalse((runtime / 'docker-compose.yml').is_symlink())
            self.assertFalse((runtime / 'nginx/nginx.conf.template').is_symlink())
            self.assertFalse((source / '.env').exists())

    def test_operator_changes_and_source_owned_override_fail_before_copying(self):
        for case in ['legacy-edit', 'asset-edit', 'unmanaged', 'symlink', 'source-override']:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source, revision = source_fixture(root); runtime = root / 'runtime'
                if case == 'legacy-edit':
                    subprocess.run(['git', 'clone', str(source), str(runtime)], check=True, capture_output=True)
                    (runtime / 'README').write_text('operator edit')
                elif case == 'asset-edit':
                    installer.install(source, runtime, revision)
                    (runtime / 'nginx/nginx.conf.template').write_text('operator edit')
                elif case == 'unmanaged':
                    (runtime / 'nginx').mkdir(parents=True)
                    (runtime / 'nginx/nginx.conf.template').write_text('operator edit')
                elif case == 'symlink':
                    runtime.mkdir()
                    (runtime / '.env').symlink_to(source / 'README')
                else:
                    (source / 'docker-compose.override.yml').write_text('application-owned')
                    git(source, 'add', '.'); git(source, 'commit', '-m', 'source override')
                    revision = git(source, 'rev-parse', 'HEAD')
                before = {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob('*') if p.is_file()} if runtime.exists() else {}
                with self.assertRaises(ValueError):
                    installer.install(source, runtime, revision)
                after = {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob('*') if p.is_file()} if runtime.exists() else {}
                self.assertEqual(before, after)

    def test_interrupted_asset_copy_and_removal_resume_without_accepting_operator_edits(self):
        for legacy in (True, False):
            for failure in ('copy', 'record'):
                with self.subTest(legacy=legacy, failure=failure), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); source, revision = source_fixture(root); runtime = root / 'runtime'
                    if legacy:
                        subprocess.run(['git', 'clone', str(source), str(runtime)], check=True, capture_output=True)
                    else:
                        installer.install(source, runtime, revision)
                    (source / 'nginx/nginx.conf.template').write_text('second template')
                    (source / 'landing-page/index.html').write_text('second landing')
                    (source / 'nginx/retired.conf').unlink()
                    git(source, 'add', '-A'); git(source, 'commit', '-m', 'next')
                    revision = git(source, 'rev-parse', 'HEAD')
                    copy = installer.shutil.copyfile
                    atomic = installer.atomic_record
                    def failing_copy(src, dst):
                        if Path(src).name == 'index.html':
                            raise OSError('interrupted between runtime assets')
                        return copy(src, dst)
                    def failing_record(path, data, mode):
                        if path.name == installer.MARKER:
                            raise OSError('interrupted after removal before record')
                        return atomic(path, data, mode)
                    with patch.object(installer.shutil, 'copyfile', side_effect=failing_copy if failure == 'copy' else copy), patch.object(
                            installer, 'atomic_record', side_effect=failing_record if failure == 'record' else atomic):
                        with self.assertRaises(OSError): installer.install(source, runtime, revision)
                    self.assertTrue((runtime / installer.PENDING).is_file())
                    installer.guard(runtime)
                    with self.assertRaises(ValueError): artifact.source_directory(runtime)
                    # A journal authorizes only the exact before/after bytes.
                    path = runtime / 'nginx/nginx.conf.template'
                    expected = path.read_bytes()
                    path.write_text('operator edit after interruption')
                    with self.assertRaises(ValueError): installer.guard(runtime)
                    path.write_bytes(expected)
                    self.assertTrue(installer.install(source, runtime, revision))
                    self.assertFalse(installer.install(source, runtime, revision))
                    self.assertFalse((runtime / installer.PENDING).exists())
                    self.assertFalse((runtime / 'nginx/retired.conf').exists())
                    self.assertEqual((runtime / 'landing-page/index.html').read_text(), 'second landing')

    def test_default_backup_configuration_discovers_legacy_source_instead_of_missing_sibling(self):
        from jinja2 import Environment
        from helpers import DEFAULTS
        environment = Environment()
        environment.filters['combine'] = lambda value, extra: {**value, **extra}
        environment.filters['to_nice_json'] = json.dumps
        values = {**DEFAULTS, 'hou1_backup_environment': 'test', 'hou1_backup_recipient': 'fixture',
                  'authentik_environment': 'test', 'authentik_compose_project_prefix': 'auth',
                  'authentik_project_root_base': '/opt/auth', 'authentik_data_root_base': '/var/lib/auth',
                  'netbird_control_plane_project': 'vpn', 'netbird_control_plane_directory': '/opt/vpn',
                  'hou1_backup_app_project': 'app', 'cms_database_directory': '/opt/app/database',
                  'cms_media_directory': '/opt/media', 'hou1_backup_recovery_files': []}
        template = environment.from_string((ROLES / 'backup_source/templates/source.json.j2').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, _ = source_fixture(root)
            values['app_directory'] = str(source)
            rendered = json.loads(template.render(**values))
            self.assertNotIn('source_root', rendered['app'])
            self.assertEqual(artifact.source_directory(source, rendered['app'].get('source_root')), source)
            rendered = json.loads(template.render(**{**values, 'hou1_backup_app_source_directory': '/opt/declared-source'}))
            self.assertEqual(rendered['app']['source_root'], '/opt/declared-source')

    def test_running_artifact_uses_recorded_source_and_keeps_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, revision = source_fixture(root); runtime = root / 'runtime'
            installer.install(source, runtime, revision)
            receipt = images.receipt(revision)
            (runtime / 'application-image.json').write_text(json.dumps(receipt))
            (runtime / 'docker-compose.yml').write_text(yaml.safe_dump({'services': {'astro-app': {'image': images.IMAGE}}}))
            (runtime / 'docker-compose.override.yml').write_text('services: {}\n')
            def command(*argv):
                if argv[0] == 'git':
                    self.assertEqual(argv[2], str(source))
                    return (revision + '\n').encode()
                if argv[0:2] == ('docker', 'inspect'):
                    return json.dumps([{'Image': images.IMAGE_ID, 'Config': {'Image': images.IMAGE}}]).encode()
                return b'fixture-container\n'
            with patch.object(artifact, 'run', side_effect=command), patch.object(artifact, 'verify', return_value=receipt):
                self.assertEqual(artifact.running(runtime), receipt)
                self.assertEqual(artifact.running(runtime, source), receipt)
            (runtime / 'application-source.json').unlink()
            self.assertEqual(artifact.source_directory(runtime), runtime)

    @unittest.skipUnless(shutil.which('age-keygen'), 'age-keygen required for snapshot fixture')
    def test_legacy_backup_config_discovers_new_source_without_archive_format_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = backups.BackupProtocolTest().fixture(root)
            source, revision = source_fixture(root)
            runtime = Path(config['app']['root'])
            installer.install(source, runtime, revision)
            receipt = images.receipt(revision)
            (runtime / 'application-image.json').write_text(json.dumps(receipt))
            events = []
            def command(argv, output=None, timeout=300):
                events.append(argv)
                if '--format' in argv: return backups.writer_status()
                if 'ps' in argv:
                    return b'authentik-server\nauthentik-worker\nastro-app\nnetbird-server\n'
                if 'pg_dump' in argv:
                    output.write(b'CREATE TABLE restored (value text);')
                if argv[0] == 'git':
                    self.assertEqual(argv[2], str(source))
                    return (revision + '\n').encode()
                return b''
            work = root / 'snapshot'; work.mkdir()
            Path(config['work_root']).mkdir()
            with patch.object(backups.source, 'command', side_effect=command), patch.object(
                    backups.source, 'application_receipt', return_value=receipt) as observed:
                backups.source.snapshot(config, work)
            observed.assert_called_once_with(str(runtime), str(source))
            metadata = json.loads((work / 'recovery.json').read_text())
            self.assertEqual(metadata['staff_revision'], revision)
            self.assertEqual(metadata['schema'], 1)
            self.assertEqual(metadata['application_image'], receipt)
            self.assertNotIn('source_root', metadata)

    def test_source_record_rejects_symlink_relative_and_writable_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, revision = source_fixture(root); runtime = root / 'runtime'
            installer.install(source, runtime, revision)
            marker = runtime / 'application-source.json'
            original = json.loads(marker.read_text())
            marker.chmod(0o666)
            with self.assertRaises(ValueError): artifact.source_directory(runtime)
            marker.chmod(0o644)
            marker.write_text(json.dumps({**original, 'source_root': '../source'}))
            with self.assertRaises(ValueError): artifact.source_directory(runtime)
            marker.unlink(); marker.symlink_to(source / 'README')
            with self.assertRaises(ValueError): artifact.source_directory(runtime)

    def test_migration_updates_installed_backup_reader_before_changing_source(self):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/application.yml').read_text())
        names = [task['name'] for task in tasks]
        self.assertLess(names.index('Keep existing backup capture compatible with the source layout'),
                        names.index('Pull the verified source revision into its separate checkout'))
        layout = yaml.safe_load((ROLES / 'backup_source/tasks/source-layout.yml').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = root / 'source.json'; exporter = root / 'prepare'
            layout[0]['ansible.builtin.stat']['path'] = str(config)
            copy = layout[-1]['ansible.builtin.copy']
            copy['src'] = str(ROLES / 'backup_source/files/export.py')
            copy['dest'] = str(exporter)
            copy.pop('owner'); copy.pop('group')
            helper = layout[-2]['ansible.builtin.copy']
            helper['src'] = str(ROLES / 'backup_source/files/operation_lock.py')
            helper['dest'] = str(root / 'hou1-backup-operation-lock.py')
            helper.pop('owner'); helper.pop('group')
            variables = {}
            result = run_play(variables, layout)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(exporter.exists())
            self.assertFalse((root / 'hou1-backup-operation-lock.py').exists())
            config.write_text('existing installed private configuration')
            exporter.write_text('old revision reader')
            result = run_play(variables, layout)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(exporter.read_bytes(), (ROLES / 'backup_source/files/export.py').read_bytes())
            self.assertEqual((root / 'hou1-backup-operation-lock.py').read_bytes(),
                             (ROLES / 'backup_source/files/operation_lock.py').read_bytes())
            self.assertEqual(config.read_text(), 'existing installed private configuration')

    def test_shared_source_default_is_a_sibling_and_public_runtime_root_stays_unchanged(self):
        shared = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
        self.assertEqual(shared['app_source_directory'], '{{ app_directory }}-source')
        template = (ROLES / 'backup_source/templates/source.json.j2').read_text()
        self.assertIn("'root': app_directory", template)
        self.assertIn("'source_root': hou1_backup_app_source_directory", template)
