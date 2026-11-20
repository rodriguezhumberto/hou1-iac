"""Cold recovery prepares lab DNS after networks/firewall, before writer startup."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_host_recovery as host


class RecoveryDnsTest(unittest.TestCase):
    def test_dns_reconciles_actual_networks_after_creation_without_starting_services(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / 'policy'; policy.touch()
            operations = []
            services = {'auth': ['postgresql', 'authentik-server', 'authentik-worker'],
                        'netbird': ['netbird-server', 'dashboard'], 'app': ['astro-app', 'nginx']}
            def compose(project, *args):
                operations.append((project, *args))
                if args[0] == 'config':
                    return json.dumps({'services': {name: {'networks': {'runtime': {}}}
                        for name in services[project]}, 'networks': {'runtime': {'name': project + '-actual-network'}}}).encode()
                return b''
            with patch.object(host.restore, 'LAB_DNS_POLICY', policy), \
                    patch.object(host.restore, 'dc', side_effect=compose), \
                    patch.object(host.restore, 'run', side_effect=lambda *args: operations.append(args)):
                host.restore.prepare_container_dns(host.inputs())
            creates = [op for op in operations if len(op) > 1 and op[1] == 'create']
            self.assertEqual(len(creates), 3)
            self.assertTrue(all(op[2:6] == ('--no-build', '--no-recreate', '--pull', 'never') for op in creates))
            self.assertEqual(set(creates[-1][6:]), {'astro-app', 'nginx'})
            self.assertNotIn('uptime-kuma', repr(operations))
            self.assertEqual(operations[-1], (host.restore.LAB_DNS_HELPER, '--require-networks',
                'app-actual-network', 'auth-actual-network', 'netbird-actual-network'))

    def test_production_quarantine_and_unmanaged_hosts_do_not_prepare_dns(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / 'policy'
            for cfg, present in [(host.inputs(), False), ({**host.inputs(), 'quarantine': True}, True),
                                 ({**host.inputs(), 'environment': 'production'}, True)]:
                if present: policy.touch()
                with self.subTest(cfg=cfg['environment'], quarantine=cfg.get('quarantine')), \
                        patch.object(host.restore, 'LAB_DNS_POLICY', policy), \
                        patch.object(host.restore, 'dc') as dc, patch.object(host.restore, 'run') as run:
                    host.restore.prepare_container_dns(cfg)
                    dc.assert_not_called(); run.assert_not_called()

    def test_dns_failure_stops_restore_before_any_writer_starts(self):
        with patch.object(host.restore, 'install'), patch.object(host.restore, 'dc') as dc, \
                patch.object(host.restore, 'PROJECTS', {name: None for name in ['auth', 'netbird', 'app']}), \
                patch.object(host.restore, 'prepare_container_dns', side_effect=RuntimeError('DNS failed')):
            with self.assertRaisesRegex(RuntimeError, 'DNS failed'):
                host.restore.restore(host.inputs())
            self.assertEqual(dc.call_count, 3)
            self.assertTrue(all(call.args[1:] == ('config', '--quiet') for call in dc.call_args_list))


if __name__ == '__main__':
    unittest.main()
