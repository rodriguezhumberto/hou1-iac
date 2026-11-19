"""Recovery keeps provider and host isolation ownership explicit and target-bound."""

import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from helpers import guard_play, run_play, run_plays
import test_host_recovery as host

TASKS = host.FILES.parent / 'tasks'


def provider_request(cfg=None):
    request = deepcopy(cfg if cfg is not None else host.inputs())
    request.update(isolation_mode='provider', bootstrap_cidr='192.168.50.20/32',
                   deny_cidrs=[], test_addresses=[], allow_local_cidrs=[], allow_update_cidrs=[],
                   provider_isolation=dict(address='192.168.50.11', mac='02:00:00:00:00:11',
                                           policy_sha256='a' * 64, verified=True))
    return request


class RecoveryIsolationTest(unittest.TestCase):
    def test_legacy_and_provider_contracts_have_distinct_owners(self):
        self.assertEqual(host.prepare.contract.isolation(host.inputs()), 'host')
        cfg = provider_request()
        self.assertEqual(host.prepare.contract.isolation(cfg, '192.168.50.11', '02:00:00:00:00:11'), 'provider')
        scenarios = [dict(environment='production'), dict(quarantine=True), dict(isolation_mode='unknown'),
                     dict(isolation_mode='host'), dict(deny_cidrs=['192.0.2.0/24'])]
        for change in scenarios:
            with self.subTest(change=change), self.assertRaises(ValueError):
                host.prepare.contract.isolation({**cfg, **change})
        for change in [dict(verified=False), dict(verified='true'), dict(policy_sha256='short'),
                       dict(address='203.0.113.11'), dict(mac='ff:ff:ff:ff:ff:ff'),
                       dict(mac='00:00:00:00:00:00'), dict(extra='unreviewed')]:
            with self.subTest(boundary=change), self.assertRaises(ValueError):
                host.prepare.contract.isolation({**cfg, 'provider_isolation': {**cfg['provider_isolation'], **change}})
        for address, mac in [('192.168.50.12', cfg['provider_isolation']['mac']),
                             (cfg['provider_isolation']['address'], '02:00:00:00:00:12')]:
            with self.subTest(address=address, mac=mac), self.assertRaises(ValueError):
                host.prepare.contract.isolation(cfg, address, mac)

    def test_provider_preparation_keeps_source_and_omits_legacy_policy_program(self):
        fixture = host.HostRecoveryPreparationTest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, payload, bundle, images = fixture.fixture(root)
            source = (payload / 'app-config/docker-compose.yml').read_bytes()
            for mode, request in [('host', cfg), ('provider', provider_request(cfg))]:
                output = root / mode
                host.prepare.prepare(request, payload, bundle, images, output)
                self.assertEqual((output / 'isolation.py').exists(), mode == 'host')
                saved = json.loads((output / 'request.json').read_text())
                self.assertEqual(saved.get('isolation_mode', 'host'), mode)
                if mode == 'provider':
                    self.assertEqual(saved['provider_isolation'], request['provider_isolation'])
            self.assertEqual((payload / 'app-config/docker-compose.yml').read_bytes(), source)

    def test_runtime_only_applies_host_policy_for_the_legacy_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(host.restore, 'ROOT', root), patch.object(host.restore, 'run') as run:
                host.restore.apply_test_isolation(provider_request())
                run.assert_not_called()
                self.assertFalse((root / 'isolation.json').exists())
                host.restore.apply_test_isolation(host.inputs())
                run.assert_called_once_with('python3', str(root / 'isolation.py'), str(root / 'isolation.json'))
                self.assertEqual(json.loads((root / 'isolation.json').read_text())['deny_cidrs'], host.inputs()['deny_cidrs'])
                run.reset_mock()
                with self.assertRaises(ValueError):
                    host.restore.apply_test_isolation({**provider_request(), 'environment': 'production'})
                run.assert_not_called()

    def test_target_guard_uses_actual_lan_address_and_mac(self):
        tasks = yaml.safe_load((TASKS / 'provider-isolation.yml').read_text())
        facts = tasks[1]['block'][0]
        self.assertIn('ansible.builtin.setup', facts)
        # Substitute only fact collection; execute the maintained owner/target guards.
        facts.pop('ansible.builtin.setup')
        facts['ansible.builtin.set_fact'] = {'ansible_facts': '{{ fixture_facts }}'}
        cfg = provider_request()
        base = dict(host_recovery_isolation_request=cfg, test_vps_public_ip='192.168.50.11',
                    fixture_facts={'default_ipv4': {'address': '192.168.50.11', 'macaddress': '02:00:00:00:00:11'}})
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'restore-started'
            pipeline = tasks + [{'ansible.builtin.file': {'path': str(marker), 'state': 'touch'}}]
            result = run_play(base, pipeline)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            marker.unlink()
            for field, value in [('address', '192.168.50.12'), ('macaddress', '02:00:00:00:00:12')]:
                changed = deepcopy(base); changed['fixture_facts']['default_ipv4'][field] = value
                result = run_play(changed, pipeline)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())

    def test_publication_and_resume_refuse_changed_owner_or_policy_receipt(self):
        tasks = yaml.safe_load((TASKS / 'publication-checkpoint.yml').read_text())
        guard = next(task for task in tasks if task['name'] ==
                     'Bind publication to the restored recovery ID, environment and VPN address')
        cfg = provider_request()
        base = dict(host_recovery_id=cfg['id'], cms_environment='test', netbird_ip=cfg['server_ip'],
                    host_recovery_isolation_mode='provider', host_recovery_provider_isolation=cfg['provider_isolation'],
                    host_recovery_publication_request={'content': base64.b64encode(json.dumps(cfg).encode()).decode()})
        cases = [({}, True), ({'host_recovery_isolation_mode': 'host'}, False),
                 ({'host_recovery_provider_isolation': {**cfg['provider_isolation'], 'policy_sha256': 'b' * 64}}, False)]
        plays = [guard_play({**base, **change, 'host_recovery_resume': resume}, [guard], None if valid else guard['name'])
                 for resume in [False, True] for change, valid in cases]
        result = run_plays(plays)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_provider_publication_leaves_legacy_monitoring_and_isolation_unmanaged(self):
        tasks = yaml.safe_load((TASKS / 'publish.yml').read_text())
        gates = next(task for task in tasks if task.get('ansible.builtin.include_role', {}).get('name') == 'rehearsal_gates')
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'legacy-gates-applied'
            gates.pop('ansible.builtin.include_role')
            gates['ansible.builtin.file'] = {'path': str(marker), 'state': 'touch'}
            for owner in ['provider', 'host']:
                result = run_play(dict(cms_environment='test', host_recovery_effective_isolation_mode=owner), [gates])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(marker.exists(), owner == 'host')


if __name__ == '__main__':
    unittest.main()
