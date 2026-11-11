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
    def test_guards_accept_both_environments_and_reject_mismatches_before_changes(self):
        role=ROOT/'ansible/playbooks/roles/monitoring_exporter'
        tasks=yaml.safe_load((role/'tasks/main.yml').read_text())[:2]
        defaults=yaml.safe_load((role/'defaults/main.yml').read_text())
        for environment, profile, subnet, group in [('test','test','100.91.0.0/16','test-servers'),
                                                    ('production','prod','100.90.0.0/16','prod-servers')]:
            with self.subTest(environment=environment):
                prefix=subnet.split('0.0/')[0]
                base={**defaults, 'app_directory':'/opt/application',
                      'hou1_monitoring_enabled':True,'cms_environment':environment,
                      'hou1_monitoring_identity_profile':profile,
                      'hou1_monitoring_inventory_group':'metrics-target',
                      'netbird_subnet':subnet, 'netbird_server_group':group,
                      'netbird_ip':prefix+'0.10','ansible_host':prefix+'0.10','node_bootstrap':False,
                      'hou1_monitoring_peer_ip':prefix+'0.22'}
                cases=[({},None),({'cms_environment':'unknown'},tasks[0]['name']),
                       ({'hou1_monitoring_identity_profile':'unknown'},tasks[0]['name']),
                       ({'hou1_monitoring_identity_profile':'prod' if profile=='test' else 'test'},tasks[1]['name']),
                       ({'netbird_server_group':'prod-servers' if group=='test-servers' else 'test-servers'},tasks[0]['name']),
                       ({'netbird_subnet':'100.64.0.0/10'},tasks[1]['name']),
                       ({'hou1_monitoring_inventory_group':'absent'},tasks[0]['name']),
                       ({'ansible_host':'203.0.113.1'},tasks[0]['name']),
                       ({'node_bootstrap':True},tasks[0]['name']),
                       ({'hou1_monitoring_peer_ip':'203.0.113.1'},tasks[0]['name']),
                       ({'hou1_monitoring_peer_ip':prefix+'0.10'},tasks[0]['name']),
                       ({'hou1_monitoring_peer_ip':prefix+'0.22/32'},tasks[0]['name']),
                       ({'hou1_monitoring_enabled':False},tasks[0]['name']),
                       ({'hou1_monitoring_exporter_port':22},tasks[0]['name']),
                       ({'hou1_request_metrics_log':'/opt/other/metrics.jsonl'},tasks[1]['name']),
                       ({'hou1_monitoring_containers':{}},tasks[1]['name'])]
                children={'metrics-target':{'hosts':{'localhost':{}}}}
                if environment=='production':children['prod_env']={'hosts':{'localhost':{}}}
                result=run_plays([guard_play({**base,**extra},tasks,guard) for extra,guard in cases],
                                 inventory_source={'all':{'children':children}})
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_ambiguous_targets_and_wrong_inventory_classification_are_refused(self):
        role=ROOT/'ansible/playbooks/roles/monitoring_exporter'
        task=yaml.safe_load((role/'tasks/main.yml').read_text())[0]
        base=dict(hou1_monitoring_enabled=True,cms_environment='test',
                  hou1_monitoring_inventory_group='metrics-target',hou1_monitoring_identity_profile='test',
                  netbird_server_group='test-servers',netbird_subnet='100.91.0.0/16',
                  netbird_ip='100.91.0.10',ansible_host='100.91.0.10',node_bootstrap=False,
                  hou1_monitoring_peer_ip='100.91.0.22',hou1_monitoring_exporter_port=9100,
                  hou1_request_metrics_enabled=False)
        scenarios=[({}, {'metrics-target':{'hosts':{'localhost':{},'other':{}}}}),
                   ({}, {'metrics-target':{'hosts':{'localhost':{}}},'prod_env':{'hosts':{'localhost':{}}}}),
                   ({'cms_environment':'production','netbird_server_group':'prod-servers',
                     'hou1_monitoring_identity_profile':'prod'}, {'metrics-target':{'hosts':{'localhost':{}}}})]
        for extra,children in scenarios:
            result=run_plays([guard_play({**base,**extra},[task],task['name'])],
                             inventory_source={'all':{'children':children}})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def setUp(self):
        self.peers=[{'id':'monitor','ip':'100.91.0.22'},{'id':'server','ip':'100.91.0.10'}]
        self.groups=[{'id':'environment','name':'test-servers','peers':['monitor','server']}]

    def test_policy_is_one_source_one_target_and_one_port(self):
        before=json.loads(json.dumps(self.groups))
        result=plan(self.groups,self.peers,[],'100.91.0.22','100.91.0.10','100.91.0.0/16','test-servers')
        self.assertIsNone(result['policy'])
        groups=self.groups+[dict(id='group-'+str(i),**g['body']) for i,g in enumerate(result['groups'])]
        policy=plan(groups,self.peers,[],'100.91.0.22','100.91.0.10','100.91.0.0/16','test-servers')['policy']['body']
        rule=policy['rules'][0]
        self.assertEqual(rule['sources'],['group-0']);self.assertEqual(rule['destinations'],['group-1'])
        self.assertEqual(rule['ports'],['9100']);self.assertEqual(rule['protocol'],'tcp')
        self.assertFalse(rule['bidirectional'])
        self.assertFalse(plan(groups,self.peers,[dict(id='policy',**policy)],'100.91.0.22','100.91.0.10','100.91.0.0/16','test-servers')['policy']['changed'])
        self.assertEqual(self.groups,before)
        groups[-1]['peers'].append('unrelated')
        with self.assertRaises(Exception):plan(groups,self.peers,[],'100.91.0.22','100.91.0.10','100.91.0.0/16','test-servers')

    def test_unknown_or_public_peers_cannot_gain_metrics_access(self):
        for source,target in [('203.0.113.1','100.91.0.10'),('100.91.0.23','100.91.0.10'),('100.91.0.22','100.91.0.22')]:
            with self.assertRaises(Exception):plan(self.groups,self.peers,[],source,target,'100.91.0.0/16','test-servers')

    def test_aggregates_exclude_row_data_and_failed_sources_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=root/'authentik-fixture.tar.gz.age';archive.write_bytes(b'fixture')
            Path(str(archive)+'.sha256').write_text('fixture checksum')
            marker=Path(str(archive)+'.success');marker.touch()
            config={'containers':{'postgresql':{'directory':'/opt/idp','service':'postgresql'}},'authentik_backup_directory':str(root)}
            def run(args):
                if args[1]=='ps':return 'a'*64+'\n'
                if args[1]=='inspect':return json.dumps(self.container_state('/opt/idp','postgresql'))
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

    @staticmethod
    def container_state(directory, service):
        return {'running':True,'health':'healthy','labels':{
            'com.docker.compose.project.working_dir':directory,
            'com.docker.compose.service':service,
            'com.docker.compose.project':'deployment',
            'com.docker.compose.oneoff':'False'}}

    def test_container_discovery_rejects_duplicates_wrong_labels_and_oneoffs(self):
        source={'directory':'/opt/idp','service':'postgresql'}
        state=self.container_state(**source)
        with patch.object(collector,'command',side_effect=['a'*64,json.dumps(state)]):
            self.assertEqual(collector.source_container(source)[0],'a'*64)
        for ids in ['', 'a'*64+'\n'+'b'*64, 'container-name']:
            with patch.object(collector,'command',return_value=ids):
                with self.assertRaises(ValueError):collector.source_container(source)
        for key,value in [('working_dir','/opt/other'),('service','other'),('oneoff','True'),('', '')]:
            wrong=json.loads(json.dumps(state))
            label='com.docker.compose.project.'+key if key=='working_dir' else 'com.docker.compose.'+key
            wrong['labels'][label]=value
            if not key:wrong['labels']['com.docker.compose.project']=''
            with patch.object(collector,'command',side_effect=['a'*64,json.dumps(wrong)]):
                with self.assertRaises(ValueError):collector.source_container(source)

    def test_log_rotation_reopens_only_the_discovered_running_nginx(self):
        config={'containers':{'nginx':{'directory':'/opt/application','service':'nginx'}}}
        with patch.object(collector,'source_container',return_value=('a'*64,{'running':True})), \
                patch.object(collector,'command') as command:
            collector.reopen_nginx(config)
            command.assert_called_once_with(['docker','exec','a'*64,'nginx','-s','reopen'])
        with patch.object(collector,'source_container',return_value=('a'*64,{'running':False})), \
                patch.object(collector,'command') as command:
            with self.assertRaises(ValueError):collector.reopen_nginx(config)
            command.assert_not_called()

    def test_production_policy_is_isolated_idempotent_and_operator_owned_state_refused(self):
        peers=[{'id':'monitor','ip':'100.90.0.22'},{'id':'server','ip':'100.90.0.10'}]
        groups=[{'id':'environment','name':'prod-servers','peers':['monitor','server']}]
        args=('100.90.0.22','100.90.0.10','100.90.0.0/16','prod-servers')
        result=plan(groups,peers,[],*args)
        groups += [dict(id='group-'+str(i),**g['body']) for i,g in enumerate(result['groups'])]
        body=plan(groups,peers,[],*args)['policy']['body']
        self.assertEqual(body['name'],'hou1-monitoring-production-metrics')
        self.assertEqual(body['rules'][0]['ports'],['9100'])
        self.assertFalse(body['rules'][0]['bidirectional'])
        self.assertFalse(plan(groups,peers,[dict(id='policy',**body)],*args)['policy']['changed'])
        for policies in [[dict(id='policy',**{**body,'description':'operator'})],
                         [dict(id='policy1',**body),dict(id='policy2',**body)]]:
            with self.assertRaises(Exception):plan(groups,peers,policies,*args)
        with self.assertRaises(Exception):plan(self.groups,self.peers,[],*args)
        with self.assertRaises(Exception):plan(groups,peers,[],*args[:-1],'test-servers')
        groups[-1]['peers'].append('unrelated')
        with self.assertRaises(Exception):plan(groups,peers,[],*args)

    def test_collector_configuration_rejects_unknown_environment_and_unsafe_sources(self):
        config={'environment':'production','containers':{'postgresql':{'directory':'/opt/idp','service':'postgresql'}},
                'authentik_backup_directory':'/var/backups/idp','request_metrics_enabled':False,
                'request_metrics_log':'/opt/app/nginx-logs/metrics.jsonl'}
        collector.validate_config(config)
        collector.validate_config({**config,'environment':'test'})
        for override in [{'environment':'prod'}, {'request_metrics_enabled':'false'},
                         {'request_metrics_log':'/opt/app/log\n/other'},
                         {'containers':{'postgresql':{'directory':'/opt/../other','service':'postgresql'}}},
                         {'containers':{'postgresql':{'name':'legacy','project':'legacy'}}}]:
            with self.assertRaises(ValueError):collector.validate_config({**config,**override})
