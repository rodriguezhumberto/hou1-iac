"""Artifact identity, Compose migration and temporary registry credential boundaries."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import ROLES, guard_play, run_plays
import test_host_recovery as host
import test_recovery_drill as drill

spec = importlib.util.spec_from_file_location('artifact', ROLES / 'application_image/files/artifact.py')
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)
REVISION = 'a' * 40
IMAGE = artifact.REPOSITORY + '@sha256:' + 'b' * 64
IMAGE_ID = 'sha256:' + 'c' * 64


def receipt(revision=REVISION):
    return dict(schema=1, image=IMAGE, revision=revision, platform='linux/amd64', image_id=IMAGE_ID)


def image_info():
    return dict(Id=IMAGE_ID, Architecture='amd64', Os='linux', RepoDigests=[IMAGE],
                Config=dict(User='appuser', Labels={
                    'org.opencontainers.image.revision': REVISION,
                    'org.opencontainers.image.source': artifact.SOURCE,
                    'io.pearlandpaws.staff-offboarding-schema': '1'}))


def add_receipt(payload, revision):
    metadata = json.loads((payload / 'recovery.json').read_text())
    metadata['application_image'] = receipt(revision)
    (payload / 'recovery.json').write_text(json.dumps(metadata))
    (payload / 'app-config/application-image.json').write_text(json.dumps(receipt(revision)))
    compose_path = payload / 'app-config/docker-compose.yml'
    config = yaml.safe_load(compose_path.read_text())
    config['services']['astro-app'].pop('build', None)
    config['services']['astro-app']['image'] = IMAGE
    compose_path.write_text(yaml.safe_dump(config))
    (payload / 'app-config/docker-compose.override.yml').write_text('services: {}\n')


class ApplicationArtifactTest(unittest.TestCase):
    def test_recovered_single_branch_checkout_can_fetch_the_next_exact_release(self):
        task = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; checkout = root / 'checkout'; mirror = root / 'mirror.git'
            def git(*args):
                return subprocess.check_output(['git', *map(str, args)], stderr=subprocess.DEVNULL, text=True).strip()
            git('init', '--initial-branch=recovery', source)
            git('-C', source, 'config', 'user.name', 'Release fixture')
            git('-C', source, 'config', 'user.email', 'fixture@example.invalid')
            (source / 'README').write_text('old recovery release\n')
            git('-C', source, 'add', 'README'); git('-C', source, 'commit', '-m', 'old')
            git('clone', '--single-branch', '--branch', 'recovery', source, checkout)
            (source / 'README').write_text('new CI release\n')
            git('-C', source, 'commit', '-am', 'new')
            git('-C', source, 'branch', '-m', 'release')
            revision = git('-C', source, 'rev-parse', 'HEAD')
            git('clone', '--bare', source, mirror)
            self.assertIn('refs/heads/recovery:', git('-C', checkout, 'config', '--get', 'remote.origin.fetch'))
            result = run_plays([dict(variables={'app_repo_pat': str(mirror), 'app_directory': str(checkout),
                                                'application_image_revision': revision}, tasks=[task])])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(git('-C', checkout, 'rev-parse', 'HEAD'), revision)

    def test_existing_node_image_update_preserves_runtime_and_rejects_missing_file(self):
        tasks = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())
        names = ['Check the retained runtime file for an image-only update',
                 'Require an existing regular runtime file before preserving it',
                 'Inject production .env file for Astro']
        selected = [task for task in tasks if task['name'] in names]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / '.env'; original = b'FIXTURE=preserve-existing-runtime\n'
            env.write_bytes(original)
            variables = dict(app_directory=directory, webstack_preserve_runtime_environment=True)
            result = run_plays([dict(variables=variables, tasks=selected)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(env.read_bytes(), original)
            env.unlink()
            result = run_plays([dict(variables=variables, tasks=selected)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Require an existing regular runtime file before preserving it', result.stdout)

    def test_tagged_webstack_deployment_cannot_skip_artifact_verification(self):
        task = yaml.safe_load((ROLES / 'webstack/tasks/main.yml').read_text())[0]
        task['tags'] = ['deploy']
        variables = dict(application_image_reference='mutable:latest', application_image_revision='main',
                         deploy_ref='main', application_image_registry_secret_source='/fixture/pull.enc.json',
                         application_image_registry_age_key_file='/fixture/scoped.agekey')
        result = run_plays([dict(variables=variables, tasks=[task])], extra_args=['--tags', 'deploy'])
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Require a CI digest and matching immutable source selection', result.stdout)

    def test_ansible_rejects_mutable_or_mismatched_source_selections_before_pull(self):
        task = yaml.safe_load((ROLES / 'application_image/tasks/main.yml').read_text())[0]
        valid = dict(application_image_reference=IMAGE, application_image_revision=REVISION, deploy_ref=REVISION,
                     application_image_registry_secret_source='/private/pull.enc.json',
                     application_image_registry_age_key_file='/private/scoped.agekey')
        scenarios = [(valid, None)] + [({**valid, **change}, task['name']) for change in [
            {'application_image_reference': artifact.REPOSITORY + ':latest'},
            {'application_image_revision': 'main'}, {'deploy_ref': 'd' * 40},
            {'application_image_registry_secret_source': ''}]]
        result = run_plays([guard_play(values, [task], expected) for values, expected in scenarios])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mutable_and_untrusted_references_fail_before_any_docker_call(self):
        with patch.object(artifact, 'run') as run:
            for image, revision in [(artifact.REPOSITORY + ':latest', REVISION),
                                    (IMAGE.replace('ghcr.io', 'registry.example.invalid'), REVISION),
                                    (IMAGE + '\n', REVISION), (IMAGE, 'main')]:
                with self.subTest(image=image), self.assertRaises(ValueError):
                    artifact.verify(image, revision, pull=True)
            run.assert_not_called()

    def test_actual_image_identity_and_runtime_contract_are_checked(self):
        info = image_info()
        with patch.object(artifact, 'run', return_value=json.dumps([info]).encode()):
            self.assertEqual(artifact.verify(IMAGE, REVISION), receipt())
        scenarios = []
        for key, value in [('Architecture', 'arm64'), ('RepoDigests', [])]:
            bad = copy.deepcopy(info); bad[key] = value; scenarios.append(bad)
        for key, value in [('User', 'root'), ('Labels', {'org.opencontainers.image.revision': 'd' * 40})]:
            bad = copy.deepcopy(info); bad['Config'][key] = value; scenarios.append(bad)
        for bad in scenarios:
            with patch.object(artifact, 'run', return_value=json.dumps([bad]).encode()), self.assertRaises(ValueError):
                artifact.verify(IMAGE, REVISION)

    def test_backup_rejects_a_running_artifact_different_from_the_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'application-image.json').write_text(json.dumps(receipt()))
            (root / 'docker-compose.yml').write_text(yaml.safe_dump({'services': {'astro-app': {'image': IMAGE}}}))
            (root / 'docker-compose.override.yml').write_text('services: {}\n')
            def command(*argv):
                if argv[0] == 'git': return REVISION.encode()
                if 'compose' in argv: return b'container-id'
                if argv[1] == 'image': return json.dumps([image_info()]).encode()
                return json.dumps([dict(Image='sha256:' + 'd' * 64, Config={'Image': IMAGE})]).encode()
            with patch.object(artifact, 'run', side_effect=command), self.assertRaises(ValueError):
                artifact.running(root)
            (root / 'application-image.json').unlink()
            with patch.object(artifact, 'run', side_effect=command), self.assertRaises(ValueError):
                artifact.running(root)

    def test_recovery_preserves_manifest_identity_across_docker_image_stores(self):
        info = image_info(); info['Id'] = IMAGE.rsplit('@', 1)[1]
        with patch.object(artifact, 'run', return_value=json.dumps([info]).encode()) as docker:
            local = artifact.verify_saved(receipt(), REVISION)
        self.assertEqual(local, {**receipt(), 'image_id': info['Id']})
        self.assertEqual(docker.call_args.args, ('docker', 'image', 'inspect', IMAGE))
        info['RepoDigests'] = [artifact.REPOSITORY + '@sha256:' + 'd' * 64]
        with patch.object(artifact, 'run', return_value=json.dumps([info]).encode()), self.assertRaises(ValueError):
            artifact.verify_saved(receipt(), REVISION)

    def test_image_compose_preserves_storage_ingress_and_other_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = dict(services={
                'astro-app': dict(build={'context': '.'}, volumes=['./database:/var/data'],
                                  environment=['HOST=0.0.0.0'], networks=['app-network']),
                'nginx': dict(image='nginx:1.28.0-alpine', ports=['443:443'], networks=['app-network'])},
                networks={'app-network': {'driver': 'bridge'}})
            path = root / 'docker-compose.yml'; path.write_text(yaml.safe_dump(config))
            result = run_plays([dict(variables={'app_directory': directory, 'application_image_reference': IMAGE},
                                    tasks=[{'ansible.builtin.import_tasks': str(ROLES / 'webstack/tasks/image-compose.yml')}])])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected = copy.deepcopy(config)
            expected['services']['astro-app'].pop('build')
            expected['services']['astro-app']['image'] = IMAGE
            self.assertEqual(yaml.safe_load(path.read_text()), expected)

    def test_registry_credentials_are_removed_even_when_the_operation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in {
                'sops': '#!/bin/sh\nprintf \'{"GHCR_USERNAME":"fixture","GHCR_TOKEN":"private-fixture-token"}\'\n',
                'docker': '#!/bin/sh\ncat > /dev/null\nprintf "%s" "$DOCKER_CONFIG" > "$FIXTURE_RECEIPT"\nprintf "fixture-auth" > "$DOCKER_CONFIG/config.json"\n',
            }.items():
                path = root / name; path.write_text(body); path.chmod(0o755)
            capture = root / 'auth-path'
            tasks = [{'block': [{'ansible.builtin.include_role': {'name': 'application_image', 'tasks_from': 'registry-command'}}],
                      'rescue': [{'ansible.builtin.assert': {'that': "ansible_failed_task.name == 'Run the fixed artifact operation with temporary registry access'"}}],
                      'environment': {'PATH': directory + ':' + os.environ['PATH'], 'FIXTURE_RECEIPT': str(capture)}}]
            result = run_plays([dict(variables={
                'application_image_registry_secret_source': '/fixture/secrets.enc.json',
                'application_image_registry_age_key_file': '/fixture/scoped.agekey',
                'application_image_command_argv': ['false']}, tasks=tasks)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('private-fixture-token', result.stdout + result.stderr)
            self.assertTrue(capture.is_file())
            self.assertFalse(Path(capture.read_text()).exists())

    def test_digest_backed_replacement_needs_no_build_base_and_never_prepares_a_dockerfile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, payload, bundle, lock_path = host.HostRecoveryPreparationTest().fixture(root)
            add_receipt(payload, cfg['staff_revision'])
            lock = json.loads(lock_path.read_text()); lock.pop('node'); lock['astro-app'] = IMAGE
            lock_path.write_text(json.dumps(lock))
            output = root / 'prepared'
            host.prepare.prepare(cfg, payload, bundle, lock_path, output)
            self.assertFalse((output / 'Dockerfile.recovery').exists())
            request = json.loads((output / 'request.json').read_text())
            self.assertEqual(request['application_image'], receipt(cfg['staff_revision']))
            self.assertEqual(json.loads((output / 'app.compose.json').read_text())['services']['astro-app']['image'], IMAGE)
            metadata_path = payload / 'recovery.json'
            metadata = json.loads(metadata_path.read_text()); metadata.pop('application_image')
            metadata_path.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                artifact.saved(payload, cfg['staff_revision'])

    def test_component_recovery_uses_the_saved_artifact_and_rejects_source_build_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = drill.RecoveryPreparationTest().fixture(root / 'payload')
            add_receipt(payload, REVISION)
            output = root / 'prepared'
            drill.prepare.prepare(drill.request(), payload, output)
            self.assertEqual(json.loads((output / 'drill-input.json').read_text())['application_image'], receipt())
            self.assertEqual(json.loads((output / 'compose.json').read_text())['services']['astro-app']['image'], IMAGE)
            (payload / 'app-config/docker-compose.override.yml').write_text('services:\n  astro-app:\n    build: .\n')
            with self.assertRaises(ValueError):
                artifact.saved(payload, REVISION)

    def test_digest_component_phase_pulls_the_saved_image_without_any_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['build.py', 'runtime.py']:
                (root / name).write_text((ROLES / 'recovery_drill/files' / name).read_text())
            (root / 'artifact.py').write_text((ROLES / 'application_image/files/artifact.py').read_text())
            (root / 'drill-input.json').write_text(json.dumps(dict(project='fixture', staff_revision=REVISION, application_image=receipt())))
            (root / 'compose.json').write_text(json.dumps({'services': {'astro-app': {'image': IMAGE}}}))
            destination_image = image_info(); destination_image['Id'] = IMAGE.rsplit('@', 1)[1]
            response = root / 'image.json'; response.write_text(json.dumps([destination_image]))
            log = root / 'commands'
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\ncase "$1 $2" in\n "pull --platform") exit 0;;\n "image inspect") cat "$IMAGE_RESPONSE";;\n *) exit 91;;\nesac\n')
            docker.chmod(0o755)
            env = {**os.environ, 'PATH': directory + ':' + os.environ['PATH'], 'COMMAND_LOG': str(log), 'IMAGE_RESPONSE': str(response)}
            result = subprocess.run([sys.executable, str(root / 'build.py')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((root / 'build-report.json').read_text())
            self.assertFalse(report['host_build'])
            self.assertEqual(report['application_image'], {**receipt(), 'image_id': destination_image['Id']})
            self.assertEqual(json.loads((root / 'drill-input.json').read_text())['app_image'], destination_image['Id'])
            self.assertEqual(log.read_text().splitlines(), ['pull --platform linux/amd64 ' + IMAGE, 'image inspect ' + IMAGE])
            self.assertFalse((root / 'build').exists())
