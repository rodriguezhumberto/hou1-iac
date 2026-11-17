"""Docker DNS ownership, interruption recovery and removal on private files."""

import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from helpers import ROLES

dns = runpy.run_path(str(ROLES / 'docker/files/dns.py'))


class DockerDnsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'daemon.json'
        self.receipt = self.root / 'state/dns.json'
        self.servers = ['192.168.50.11']

    def update(self, state='present', **options):
        return dns['update'](state, self.servers, self.config, self.receipt, **options)

    def ack(self):
        dns['acknowledge'](self.receipt)

    def test_adopt_existing_lab_dns_without_restart_then_remove_only_owned_field(self):
        original = {'dns': self.servers, 'log-driver': 'local', 'live-restore': True}
        self.config.write_text(json.dumps(original))
        self.assertEqual(self.update(), {'changed': False, 'restart_pending': False})
        self.assertEqual(self.update(), {'changed': False, 'restart_pending': False})
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(self.update('absent'), {'changed': True, 'restart_pending': True})
        self.assertEqual(json.loads(self.config.read_text()), {'log-driver': 'local', 'live-restore': True})
        self.ack(); self.assertFalse(self.receipt.exists())
        self.assertFalse(self.update('absent')['changed'])

    def test_preserve_operator_dns_and_unrelated_settings_across_enable_and_disable(self):
        original = {'dns': ['192.168.50.53'], 'registry-mirrors': ['https://registry.example.invalid']}
        self.config.write_text(json.dumps(original))
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as validate:
            self.assertTrue(self.update()['restart_pending'])
            self.assertEqual(validate.call_args.args[0][:3], ['dockerd', '--validate', '--config-file'])
            self.ack()
            changed = json.loads(self.config.read_text()); changed['log-driver'] = 'local'
            self.config.write_text(json.dumps(changed))
            self.update('absent'); self.ack()
        self.assertEqual(json.loads(self.config.read_text()), {**original, 'log-driver': 'local'})

    def test_failed_restart_is_retried_even_after_configuration_was_written(self):
        self.config.write_text('{}')
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.assertTrue(self.update()['restart_pending'])
            # Model interruption after installation, before restart/acknowledgment.
            self.assertEqual(self.update(), {'changed': False, 'restart_pending': True})
            self.ack()
            self.assertEqual(self.update(), {'changed': False, 'restart_pending': False})
            self.update('absent')
            self.assertEqual(self.update('absent'), {'changed': False, 'restart_pending': True})
            self.ack()
        self.assertEqual(json.loads(self.config.read_text()), {})

    def test_invalid_native_configuration_preserves_previous_files(self):
        self.config.write_text('{"log-driver":"local"}')
        original = self.config.read_bytes()
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'dockerd')):
            with self.assertRaises(subprocess.CalledProcessError): self.update()
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(self.receipt.exists())

    def test_interrupted_receipt_before_config_write_and_operator_drift(self):
        self.config.write_text('{}')
        actual_write = dns['update'].__globals__['write_json']
        def interrupted(path, value, **kwargs):
            if path == self.config: raise OSError('interrupted write')
            return actual_write(path, value, **kwargs)
        with patch.dict(dns['update'].__globals__, write_json=interrupted), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaises(OSError): self.update()
        self.assertTrue(self.receipt.exists())
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.assertTrue(self.update()['restart_pending']); self.ack()
        self.config.write_text('{"dns":["192.168.50.99"]}')
        with self.assertRaises(ValueError): self.update('absent')
        self.assertEqual(json.loads(self.config.read_text())['dns'], ['192.168.50.99'])


if __name__ == '__main__':
    unittest.main()
