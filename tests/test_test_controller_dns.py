"""Bare test-container DNS must preserve both private staff and public IdP access."""
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from helpers import ROOT

SCRIPT = runpy.run_path(str(ROOT / 'scripts/reconnect-test-controller.py'))
REMOTE = {'__name__':'fixture'}
exec(SCRIPT['REMOTE'], REMOTE)


class TestControllerDnsTest(unittest.TestCase):
    def test_private_pins_preserve_unmanaged_hosts_and_restore_bootstrap_dns(self):
        original = '# Docker generated\nnameserver 1.1.1.1\nnameserver 8.8.8.8\nsearch example.invalid\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver, receipt, hosts = root/'resolver', root/'receipt', root/'hosts'
            resolver.write_text(original);hosts.write_text('127.0.0.1 localhost\n# operator entry\n\n')
            original_hosts = hosts.read_text();inode = hosts.stat().st_ino
            with patch.dict(REMOTE, RESOLVER=resolver, RECEIPT=receipt, HOSTS=hosts):
                REMOTE['prepare']()
                REMOTE['pin_hosts'](REMOTE['PRIVATE_HOSTS'])
                self.assertIn('100.91.0.10 staff-test.vps-test.pearlandpaws.com', hosts.read_text())
                REMOTE['pin_hosts'](REMOTE['PRIVATE_HOSTS'])
                self.assertEqual(hosts.read_text().count(REMOTE['BEGIN']), 1)
                resolver.write_text('nameserver 100.91.205.101\n')
                REMOTE['prepare']()
                self.assertEqual(resolver.read_text(), original)
                self.assertEqual(hosts.read_text(), original_hosts)
                self.assertEqual(hosts.stat().st_ino, inode)
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def state(self):
        return {'management':{'connected':True,'url':'https://netbird-test.vps-test.pearlandpaws.com'},
                'netbirdIp':'100.91.205.101/16', 'dnsServers':[{'enabled':True,'domains':['staff-test.vps-test.pearlandpaws.com']}]}

    def test_wrong_management_or_private_answers_cannot_install_pins(self):
        for fault in ['management','answer','dns-unavailable']:
            state = self.state()
            if fault == 'management':state['management']['url']='https://production.example.invalid'
            replies = [subprocess.CompletedProcess([],0,json.dumps(state)),
                       subprocess.CompletedProcess([],1 if fault=='dns-unavailable' else 0,'Address: 8.8.8.8\n')]
            with self.subTest(fault=fault), patch.object(REMOTE['subprocess'],'run',side_effect=replies), \
                    patch.dict(REMOTE, pin_hosts=lambda _:self.fail('Unsafe DNS installed hosts pins')):
                if fault == 'dns-unavailable':self.assertEqual(REMOTE['configure'](), {'ready':False})
                else:
                    with self.assertRaises(AssertionError):REMOTE['configure']()

    def test_failed_public_resolution_removes_only_managed_pins(self):
        state = self.state()
        def process(argv, **kwargs):
            if argv[0]=='netbird':return subprocess.CompletedProcess(argv,0,json.dumps(state))
            return subprocess.CompletedProcess(argv,0,'Address: '+REMOTE['PRIVATE_HOSTS'][argv[2]]+'\n')
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory)/'hosts';hosts.write_text('127.0.0.1 localhost\n')
            def dns(host, *args, **kwargs):
                if host.startswith('auth-'):raise OSError('No public DNS')
                return [(2,1,6,'',(REMOTE['PRIVATE_HOSTS'][host],443))]
            with patch.dict(REMOTE, HOSTS=hosts), patch.object(REMOTE['subprocess'],'run',side_effect=process), \
                    patch.object(REMOTE['socket'],'getaddrinfo',side_effect=dns):
                with self.assertRaises(OSError):REMOTE['configure']()
                self.assertEqual(hosts.read_text(), '127.0.0.1 localhost\n')
