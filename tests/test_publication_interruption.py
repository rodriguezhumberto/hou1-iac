"""Publication crash boundaries use synthetic files and recorded service calls."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'publication_restore', ROOT / 'ansible/playbooks/roles/host_recovery/files/restore.py')
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'id': 'test-publication', 'environment': 'test'}
        self.projects = {key: (key, self.root / key) for key in ('app', 'netbird')}
        self.originals = {}
        for project, (service, ports) in {
                'app': ('nginx', ['127.0.0.1:80:80', '127.0.0.1:443:443']),
                'netbird': ('netbird-server', ['127.0.0.1:3478:3478/udp'])}.items():
            self.projects[project][1].mkdir()
            file = self.projects[project][1] / ('docker-compose.yml' if project == 'app' else 'compose.yaml')
            self.originals[file] = json.dumps({'services': {service: {'ports': ports}}})
            file.write_text(self.originals[file])
        (self.root / 'restore-report.json').write_text('{"passed":true}')
        self.firewall = self.root / 'firewall.json'
        self.firewall.write_text(json.dumps({'80/tcp': 'preexisting-owner'}))
        self.events = self.root / 'events.jsonl'
        self.events.touch()
        self.boundary = None
        for item in (patch.object(restore, 'ROOT', self.root),
                     patch.object(restore, 'PROJECTS', self.projects),
                     patch.object(restore, 'run', side_effect=self.command),
                     patch.object(restore, 'dc', side_effect=self.dc)):
            item.start()
            self.addCleanup(item.stop)

    def event(self, name):
        with self.events.open('a') as stream:
            stream.write(json.dumps(name) + '\n')
        if name == self.boundary:
            os._exit(77)

    def command(self, *args, **kwargs):
        rules = json.loads(self.firewall.read_text())
        if args == ('ufw', 'show', 'added'):
            return ('\n'.join('ufw allow ' + port + ' comment ' + comment
                              for port, comment in rules.items()) + '\n').encode()
        if args[:2] == ('ufw', 'allow'):
            self.assertNotIn(args[2], rules)
            rules[args[2]] = args[4]
            self.firewall.write_text(json.dumps(rules))
            self.event('allow-' + args[2])
            return b''
        if args[:4] == ('ufw', '--force', 'delete', 'allow'):
            self.assertEqual(rules[args[4]], args[6])
            del rules[args[4]]
            self.firewall.write_text(json.dumps(rules))
            return b''
        raise AssertionError(args)

    def dc(self, project, *args, **kwargs):
        self.event(project + '-' + args[0])
        return b''

    def assert_sealed(self):
        for file, original in self.originals.items():
            self.assertEqual(file.read_text(), original)
        self.assertEqual(json.loads(self.firewall.read_text()), {'80/tcp': 'preexisting-owner'})
        self.assertTrue((self.root / 'first-error.json').exists())
        self.assertFalse((self.root / 'publish-report.json').exists())
        self.assertTrue(json.loads((self.root / 'publication-intent.json').read_text())['cleanup']['passed'])

    def test_hard_exit_reentry_seals_each_mutation_boundary(self):
        # Real child exits bypass every exception/finally cleanup. State is in
        # files shared with its parent; no Docker, UFW or managed host executes.
        boundaries = ['intent', 'netbird-compose', 'app-compose',
                      'allow-443/tcp', 'allow-3478/udp', 'netbird-up', 'app-up', 'app-exec']
        writer = restore.publication_write
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                for name in ('publication-intent.json', 'first-error.json'):
                    (self.root / name).unlink(missing_ok=True)
                self.boundary = boundary

                def write(file, value):
                    writer(file, value)
                    if file.name == 'publication-intent.json':
                        self.event('intent')
                    elif file in self.originals:
                        self.event(file.parent.name + '-compose')

                pid = os.fork()
                if pid == 0:
                    with patch.object(restore, 'publication_write', side_effect=write):
                        restore.publish(self.cfg)
                    os._exit(0)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 77)
                self.boundary = None
                with self.assertRaisesRegex(ValueError, 'Interrupted publication sealed'):
                    restore.publish(self.cfg)
                self.assert_sealed()
                # Cleanup retries remain idempotent but never republish.
                self.assertFalse(restore.recover_publication(self.cfg)['publication_retry_allowed'])

    def test_caught_service_error_restores_private_bindings_and_owned_rules(self):
        def dc(project, *args, **kwargs):
            if args[0] == 'up':
                raise RuntimeError('synthetic failure')
            return self.dc(project, *args, **kwargs)
        with patch.object(restore, 'dc', side_effect=dc):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                restore.publish(self.cfg)
        self.assert_sealed()

    def test_cleanup_preserves_changed_firewall_ownership_and_reports_failure(self):
        self.boundary = 'netbird-up'
        pid = os.fork()
        if pid == 0:
            restore.publish(self.cfg)
            os._exit(0)
        os.waitpid(pid, 0)
        self.boundary = None
        rules = json.loads(self.firewall.read_text())
        rules['443/tcp'] = 'another-owner'
        self.firewall.write_text(json.dumps(rules))
        with self.assertRaisesRegex(RuntimeError, 'cleanup incomplete'):
            restore.recover_publication(self.cfg)
        self.assertEqual(json.loads(self.firewall.read_text())['443/tcp'], 'another-owner')
        self.assertFalse(json.loads((self.root / 'publication-intent.json').read_text())['cleanup']['passed'])

    def test_success_checkpoint_prevents_cleanup_and_republication(self):
        self.assertTrue(restore.publish(self.cfg)['passed'])
        with self.assertRaisesRegex(ValueError, 'Successful publication'):
            restore.recover_publication(self.cfg)
        with self.assertRaisesRegex(ValueError, 'Successful publication'):
            restore.publish(self.cfg)

    def test_quarantine_and_changed_request_cannot_cleanup(self):
        restore.publish(self.cfg)
        with self.assertRaisesRegex(ValueError, 'Quarantined'):
            restore.recover_publication({**self.cfg, 'quarantine': True})
        with self.assertRaisesRegex(ValueError, 'differs'):
            restore.recover_publication({**self.cfg, 'id': 'different'})

    def test_explicit_cleanup_entrypoint_preserves_latched_error(self):
        self.boundary = 'netbird-up'
        pid = os.fork()
        if pid == 0:
            restore.publish(self.cfg)
            os._exit(0)
        os.waitpid(pid, 0)
        self.boundary = None
        # Installed entrypoint accepts cleanup despite a previous error, while
        # publication itself still refuses that error latch.
        cfg = {**self.cfg, 'id': self.root.name}
        path = self.root / 'publication-intent.json'
        journal = json.loads(path.read_text())
        journal['config_sha256'] = restore.publication_identity(cfg)
        old_comment = journal['firewall_comment']
        journal['firewall_comment'] = 'hou1-recovery-' + cfg['id']
        path.write_text(json.dumps(journal))
        rules = json.loads(self.firewall.read_text())
        self.firewall.write_text(json.dumps({port: journal['firewall_comment'] if value == old_comment else value
                                            for port, value in rules.items()}))
        (self.root / 'request.json').write_text(json.dumps(cfg))
        (self.root / 'first-error.json').write_text('{"action":"publish"}')
        with patch.object(restore, 'RECOVERY_BASE', self.root.parent), patch.object(restore, 'configure'):
            self.assertTrue(restore.main('recover-publication')['passed'])
            self.assertEqual((self.root / 'first-error.json').read_text(), '{"action":"publish"}')
            with self.assertRaisesRegex(ValueError, 'Do not resume'):
                restore.main('publish')
        self.assert_sealed()

    def test_failed_stop_keeps_cleanup_retryable_without_republishing(self):
        def stop(project, *args, **kwargs):
            if args[0] in ('up', 'stop'):
                raise RuntimeError('synthetic service failure')
            return b''
        with patch.object(restore, 'dc', side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, 'cleanup incomplete'):
                restore.publish(self.cfg)
        self.assertFalse(json.loads((self.root / 'publication-intent.json').read_text())['cleanup']['passed'])
        self.assertTrue(restore.recover_publication(self.cfg)['passed'])
        self.assert_sealed()


if __name__ == '__main__':
    unittest.main()
