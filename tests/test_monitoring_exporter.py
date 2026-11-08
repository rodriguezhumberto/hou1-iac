"""Private metrics policy and read-only aggregate behavior."""
from pathlib import Path
import importlib.util
import json
import runpy
import tempfile
import unittest
from unittest.mock import patch
import yaml
from helpers import guard_play, run_plays

ROOT=Path(__file__).resolve().parents[1]
POLICY=runpy.run_path(str(ROOT/'ansible/playbooks/filter_plugins/netbird_policies.py'))
plan=POLICY['monitoring_plan']
spec=importlib.util.spec_from_file_location('collector',ROOT/'ansible/playbooks/roles/monitoring_exporter/files/collect.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)


class MonitoringExporterTest(unittest.TestCase):
    def test_deployment_guard_rejects_public_and_production_before_api_or_host_changes(self):
        task=yaml.safe_load((ROOT/'ansible/playbooks/roles/monitoring_exporter/tasks/main.yml').read_text())[0]
        base=dict(hou1_monitoring_enabled=True,cms_environment='test',netbird_subnet='100.91.0.0/16',
                  netbird_ip='100.91.0.10',ansible_host='100.91.0.10',node_bootstrap=False,
                  hou1_monitoring_peer_ip='100.91.0.22',hou1_monitoring_exporter_port=9100,
                  hou1_monitoring_backup_directory='/var/backups/hou1-authentik/test')
        cases=[({},None),({'cms_environment':'production'},task['name']),
               ({'ansible_host':'203.0.113.1'},task['name']),({'node_bootstrap':True},task['name']),
               ({'hou1_monitoring_peer_ip':'203.0.113.1'},task['name']),
               ({'hou1_monitoring_enabled':False},task['name'])]
        result=run_plays([guard_play({**base,**extra},[task],guard) for extra,guard in cases],
                         inventory_source={'all':{'children':{'isolated_test_vps':{'hosts':{'localhost':{}}}}}})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def setUp(self):
        self.peers=[{'id':'monitor','ip':'100.91.0.22'},{'id':'server','ip':'100.91.0.10'}]
        self.groups=[{'id':'environment','name':'test-servers','peers':['monitor','server']}]

    def test_policy_is_one_source_one_target_and_one_port(self):
        before=json.loads(json.dumps(self.groups))
        result=plan(self.groups,self.peers,[],'100.91.0.22','100.91.0.10')
        self.assertIsNone(result['policy'])
        groups=self.groups+[dict(id='group-'+str(i),**g['body']) for i,g in enumerate(result['groups'])]
        policy=plan(groups,self.peers,[],'100.91.0.22','100.91.0.10')['policy']['body']
        rule=policy['rules'][0]
        self.assertEqual(rule['sources'],['group-0']);self.assertEqual(rule['destinations'],['group-1'])
        self.assertEqual(rule['ports'],['9100']);self.assertEqual(rule['protocol'],'tcp')
        self.assertFalse(rule['bidirectional'])
        self.assertFalse(plan(groups,self.peers,[dict(id='policy',**policy)],'100.91.0.22','100.91.0.10')['policy']['changed'])
        self.assertEqual(self.groups,before)
        groups[-1]['peers'].append('unrelated')
        with self.assertRaises(Exception):plan(groups,self.peers,[],'100.91.0.22','100.91.0.10')

    def test_unknown_or_public_peers_cannot_gain_metrics_access(self):
        for source,target in [('203.0.113.1','100.91.0.10'),('100.91.0.23','100.91.0.10'),('100.91.0.22','100.91.0.22')]:
            with self.assertRaises(Exception):plan(self.groups,self.peers,[],source,target)

    def test_aggregates_exclude_row_data_and_failed_sources_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=root/'authentik-fixture.tar.gz.age';archive.write_bytes(b'fixture')
            Path(str(archive)+'.sha256').write_text('fixture checksum')
            marker=Path(str(archive)+'.success');marker.touch()
            config={'containers':{'postgresql':{'name':'pg-test-postgresql-1','project':'pg-test'}},'authentik_backup_directory':str(root)}
            def run(args):
                if args[1]=='inspect':return json.dumps({'running':True,'health':'healthy','project':'pg-test'})
                self.assertIn('BEGIN READ ONLY',args[-1]);self.assertNotIn('password',args[-1])
                return json.dumps(dict(connections=3,commits=5,rollbacks=0,blocks_read=2,blocks_hit=7,deadlocks=0,database_size=1234))
            with patch.object(collector,'command',side_effect=run):
                text,success=collector.collect(config,now=100)
            self.assertTrue(success);self.assertIn('hou1_postgresql_up 1.0',text)
            self.assertIn('hou1_postgresql_database_size_bytes 1234.0',text)
            self.assertIn('hou1_backup_last_attempt_success{backup="authentik-local"} 1.0',text)
            with patch.object(collector,'command',side_effect=RuntimeError('private failure')):
                text,success=collector.collect(config,now=101)
            self.assertFalse(success);self.assertIn('hou1_postgresql_up 0.0',text)
            self.assertIn('hou1_metrics_collection_success 0.0',text)
            self.assertNotIn('private failure',text)
            archive.unlink()
            with patch.object(collector,'command',side_effect=run):text,_=collector.collect(config,now=102)
            self.assertIn('hou1_backup_last_success_timestamp_seconds{backup="authentik-local"} 0.0',text)
