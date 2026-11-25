"""Constrained recovery bounds memory and stops only the isolated project."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT

FILES = ROOT / 'ansible/playbooks/roles/recovery_drill/files'
spec = importlib.util.spec_from_file_location('drill_resources', FILES / 'resources.py')
resources = importlib.util.module_from_spec(spec); spec.loader.exec_module(resources)


class ConstrainedResourcesTest(unittest.TestCase):
    def test_explicit_profile_bounds_every_service_and_swap_without_changing_standard(self):
        compose = {'services': {name: {'image': 'synthetic'} for name in resources.LIMITS}}
        resources.apply({}, compose)
        self.assertTrue(all('mem_limit' not in value for value in compose['services'].values()))
        resources.apply({'resource_profile': 'constrained'}, compose)
        self.assertEqual(sum(s['mem_limit'] for s in compose['services'].values()), 2 * 1024**3)
        self.assertEqual(sum(s['memswap_limit'] - s['mem_limit'] for s in compose['services'].values()), 512 * 1024**2)
        with self.assertRaises(ValueError): resources.profile({'resource_profile': 'unknown'})

    def test_pressure_aborts_phase_reaps_children_and_stops_only_selected_project(self):
        cfg = {'project': 'hou1-recovery-drill-fixture-01'}
        process = Mock(pid=1234); process.poll.side_effect = [None, None]
        process.__enter__ = Mock(return_value=process); process.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(resources, 'primary'), patch.object(resources, 'sample', return_value=(128 * 1024**2, 0)), \
                patch.object(resources.subprocess, 'Popen', return_value=process), \
                patch.object(resources.os, 'killpg') as kill, patch.object(resources.subprocess, 'run') as stop:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'memory pressure'):
                resources.execute(cfg, root, ['python3', 'synthetic.py'], io.BytesIO())
            kill.assert_called_once(); process.wait.assert_called_once()
            self.assertEqual(stop.call_args.args[0], ['docker', 'compose', '--project-directory', str(root),
                '-p', cfg['project'], '-f', str(root/'compose.json'), 'stop', '--timeout', '10'])
            self.assertTrue(json.loads((root/'resource-report.json').read_text())['aborted'])

    def test_oom_and_limit_drift_are_fatal(self):
        item = {'Config': {'Labels': {'com.docker.compose.service': 'astro-app'}},
                'HostConfig': {'Memory': 256 * resources.MIB, 'MemorySwap': 320 * resources.MIB},
                'State': {'OOMKilled': False}}
        with patch.object(resources.subprocess, 'check_output', return_value=b'fixture-id'), \
                patch.object(resources, 'inspect', return_value=[item]):
            resources.containers({'project': 'fixture'})
            item['State']['OOMKilled'] = True
            with self.assertRaisesRegex(RuntimeError, 'OOM-killed'): resources.containers({'project': 'fixture'})
            item['State']['OOMKilled'] = False; item['HostConfig']['Memory'] = 0
            with self.assertRaisesRegex(RuntimeError, 'limits differ'): resources.containers({'project': 'fixture'})

    def test_success_retains_memory_observations_and_does_not_stop_project(self):
        process = Mock(returncode=0); process.poll.side_effect = [None, 0, 0]
        process.__enter__ = Mock(return_value=process); process.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder, patch.object(resources, 'primary') as primary, \
                patch.object(resources, 'sample', return_value=(800 * resources.MIB, 0)), \
                patch.object(resources, 'containers') as containers, patch.object(resources.time, 'sleep'), \
                patch.object(resources.subprocess, 'Popen', return_value=process), \
                patch.object(resources.subprocess, 'run') as stop:
            root = Path(folder)
            self.assertEqual(resources.execute({'project':'fixture'},root,['fixture'],io.BytesIO()),0)
            self.assertEqual(primary.call_count,2); self.assertEqual(containers.call_count,2)
            stop.assert_not_called()
            report = json.loads((root/'resource-report.json').read_text())
            self.assertFalse(report['aborted']); self.assertEqual(report['minimum_available_bytes'],800*resources.MIB)

    def test_primary_baseline_is_immutable_and_stopped_primary_refuses_attempt(self):
        cfg={'primary_containers':['primary-test']}
        live=[{'Name':'/primary-test','Id':'original','State':{'Running':True,'StartedAt':'before','OOMKilled':False}}]
        with tempfile.TemporaryDirectory() as folder, patch.object(resources,'inspect',return_value=live):
            root=Path(folder);resources.primary(cfg,root)
            saved=(root/'live-baseline.json').read_bytes()
            live[0]['Id']='changed'
            with self.assertRaisesRegex(RuntimeError,'changed'):resources.primary(cfg,root)
            self.assertEqual((root/'live-baseline.json').read_bytes(),saved)
            live[0]['State']['Running']=False
            with self.assertRaisesRegex(RuntimeError,'stopped'):resources.primary(cfg,root)
