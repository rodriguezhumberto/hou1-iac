"""Read real files through append, rotation, corrupt input, and bounded ingestion."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('requests_collector',ROOT/'ansible/playbooks/roles/monitoring_exporter/files/request_metrics.py')
metrics=importlib.util.module_from_spec(spec);spec.loader.exec_module(metrics)


def record(result='success',duration=0.25,status=302,service='staff',route='callback'):
    return json.dumps(dict(v=1,time=100,service=service,route=route,status=status,duration=duration,result=result))+'\n'


class RequestMetricsTest(unittest.TestCase):
    def test_append_rotation_histograms_consecutive_failures_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=root/'metrics.jsonl';state=root/'state.json'
            log.write_text(record()+record('failure',3)+record('unknown',0.5)+record('none',0.1,502,'authentik','request'))
            first=metrics.collect(log,state)
            self.assertIn('phase="callback",result="success"} 1',first)
            self.assertIn('hou1_staff_login_consecutive_failures 2',first)
            self.assertIn('le="2"} 2',first)
            self.assertIn('service="authentik",status_class="5xx"} 1',first)
            self.assertEqual(metrics.collect(log,state),first)
            log.rename(root/'metrics.jsonl.1');log.write_text(record())
            next_text=metrics.collect(log,state)
            self.assertIn('result="success"} 2',next_text)
            self.assertIn('hou1_staff_login_consecutive_failures 0',next_text)
            self.assertEqual(state.stat().st_mode & 0o777,0o600)
            with (root/'metrics.jsonl.1').open('a') as stream:stream.write(record('failure'))
            final = metrics.collect(log,state)
            self.assertIn('result="failure"} 2', final)
            self.assertEqual(metrics.collect(log,state), final)

    def test_unread_rotation_tail_is_drained_and_partial_current_line_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=root/'metrics.jsonl';state=root/'state.json'
            log.write_text(record());metrics.collect(log,state)
            with log.open('a') as stream:stream.write(record('failure'))
            log.rename(root/'metrics.jsonl.1');log.write_text(record()[:-2])
            text=metrics.collect(log,state);self.assertIn('result="failure"} 1',text)
            with log.open('a') as stream:stream.write('}\n')
            text=metrics.collect(log,state);self.assertIn('result="success"} 2',text)

    def test_bad_schema_unbounded_data_symlinks_and_lost_rotations_fail_without_advancing(self):
        for bad in [record().replace('"duration": 0.25','"duration": -1'),record().replace('"time": 100','"time": NaN'),record().replace('"staff"','"email@example.invalid"'),record().replace('"v": 1','"token": "secret", "v": 1'),'x'*1025+'\n']:
            with self.subTest(bad=bad[:20]),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);log=root/'metrics.jsonl';state=root/'state.json';log.write_text(record());metrics.collect(log,state);before=state.read_bytes()
                with log.open('a') as stream:stream.write(bad)
                with self.assertRaises(ValueError):metrics.collect(log,state)
                self.assertEqual(state.read_bytes(),before)
                self.assertNotIn('secret',before.decode())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=root/'metrics.jsonl';state=root/'state.json';log.write_text(record());metrics.collect(log,state)
            log.rename(root/'lost');log.write_text(record())
            with self.assertRaises(ValueError):metrics.collect(log,state)
            state.unlink();log.unlink();log.symlink_to(root/'lost')
            with self.assertRaises(ValueError):metrics.collect(log,state)
