"""Consistent snapshots, offline age recovery, restart safety and narrow transport policies."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


source=load('backup_export',ROOT/'ansible/playbooks/roles/backup_source/files/export.py')
reader=load('backup_reader',ROOT/'ansible/playbooks/roles/backup_source/files/serve.py')
gateway=load('backup_gateway',ROOT/'ansible/playbooks/roles/backup_source/files/gateway.py')
recovery=load('backup_recovery',ROOT/'scripts/verify-recovery.py')
policies=load('backup_policies',ROOT/'ansible/playbooks/filter_plugins/netbird_policies.py')


class BackupProtocolTest(unittest.TestCase):
    def fixture(self,root):
        app,auth,data,netbird=[root/p for p in ['app','auth','authdata','netbird']]
        for p in [app/'database',app/'media',auth,data/'media',data/'templates',data/'certs',data/'config',netbird/'data']:p.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(app/'database/forms.db') as db:
            db.executescript('CREATE TABLE fixture (value text); INSERT INTO fixture VALUES ("recover-me");')
        (app/'media/photo.jpg').write_bytes(b'fixture-media')
        for p,names in [(auth,['compose.yaml','authentik.env','oidc-clients.env','secrets.enc.yaml']),(app,['.env','docker-compose.yml','docker-compose.override.yml']),(netbird,['compose.yaml','config.yaml','dashboard.env'])]:
            for name in names:(p/name).write_text('synthetic-private-config')
        (app/'certbot/conf').mkdir(parents=True)
        (app/'certbot/conf/cert.pem').write_text('synthetic-certificate')
        # Certbot's relative renewal symlinks must survive safe offline extraction.
        (app/'certbot/conf/link.pem').symlink_to('cert.pem')
        (netbird/'data/state.db').write_bytes(b'netbird-fixture-state')
        identity=root/'offline.agekey'
        subprocess.run(['age-keygen','-o',str(identity)],check=True,capture_output=True)
        recipient=subprocess.run(['age-keygen','-y',str(identity)],check=True,capture_output=True,text=True).stdout.strip()
        hostkey=root/'host.agekey';hostkey.write_text('synthetic-host-runtime-key')
        return dict(environment='test',recipient=recipient,work_root=str(root/'work'),minimum_free_bytes=0,max_archive_bytes=10000000,
            authentik=dict(root=str(auth),data=str(data),name='auth-test'),app=dict(root=str(app),name='app-test',database=str(app/'database'),media=str(app/'media')),
            netbird=dict(root=str(netbird),name='netbird-test'),recovery_files=[str(hostkey)]),identity

    @unittest.skipUnless(shutil.which('age'),'age required')
    def test_snapshot_offline_encrypt_decrypt_and_sqlite_media_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,key=self.fixture(root);events=[]
            def command(argv,output=None,timeout=300):
                events.append(argv)
                if argv[-4:]==['ps','--status','running','--services']:
                    return b'authentik-server\nauthentik-worker\nastro-app\nnetbird-server\n'
                if 'pg_dump' in argv:output.write(b'CREATE TABLE recovered (value text);\n');return b''
                if argv[0]=='git':return b'0123456789012345678901234567890123456789\n'
                return b''
            stream=io.BytesIO()
            archive_tree=source.archive_tree
            def archive_after_restart(output,directory,names):
                if Path(output).name=='netbird.tar.gz':
                    self.assertEqual(events[-1][-2:],['start','netbird-server'])
                return archive_tree(output,directory,names)
            with patch.object(source,'command',side_effect=command),patch.object(source,'archive_tree',side_effect=archive_after_restart):
                source.prepare(config)
            reader.serve(config,stream)
            stream.seek(0);manifest=json.loads(stream.readline());archive=root/'recovery.age';archive.write_bytes(stream.read())
            self.assertNotIn(b'synthetic-private-config',archive.read_bytes())
            metadata=root/'manifest.json';metadata.write_text(json.dumps(manifest));output=root/'restore'
            report=recovery.verify(archive,metadata,key,output,'test')
            self.assertTrue(report['offline_decryption'])
            with sqlite3.connect(output/'cms/database/forms.db') as db:
                self.assertEqual(db.execute('SELECT value FROM fixture').fetchone(),('recover-me',))
            self.assertEqual((output/'cms/media/photo.jpg').read_bytes(),b'fixture-media')
            self.assertTrue((output/'app-config/certbot/conf/link.pem').is_symlink())
            self.assertEqual((output/'netbird-files/data/state.db').read_bytes(),b'netbird-fixture-state')
            self.assertEqual((output/'host-recovery/0').read_text(),'synthetic-host-runtime-key')
            stops=[e for e in events if 'stop' in e];starts=[e for e in events if 'start' in e]
            self.assertEqual(len(stops),3);self.assertEqual(len(starts),3)
            self.assertEqual([p.name for p in (root/'work').iterdir()],['ready.export'])
            for env,changes in [('production',{}),('test',{'size':manifest['size']+1}),('test',{'sha256':'0'*64})]:
                metadata.write_text(json.dumps({**manifest,**changes}))
                with self.assertRaises(ValueError):recovery.verify(archive,metadata,key,root/'failed',env)

    def test_failed_preparation_preserves_previous_completed_ciphertext(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ready=root/'ready.export';ready.write_bytes(b'previous-complete-point')
            def fail(config,stream):
                stream.write(b'partial-new-point');raise RuntimeError('Interrupted local preparation')
            with patch.object(source,'export',side_effect=fail):
                with self.assertRaises(RuntimeError):source.prepare({'work_root':str(root)})
            self.assertEqual(ready.read_bytes(),b'previous-complete-point')
            self.assertEqual(list(root.iterdir()),[ready])

    def test_reader_rejects_stale_corrupt_partial_wrong_environment_and_symlink_before_streaming(self):
        import hashlib
        payload=b'age-encryption.org/v1\nfixture-only-ciphertext';now=1800000000
        manifest=dict(schema=1,environment='test',id='20270115T080000Z-abcdef123456',created=now,
                      size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=dict(work_root=str(root),environment='test',max_archive_bytes=10000)
            for changes,body in [({'created':now-21601},payload),({'created':now+61},payload),
                                 ({'environment':'production'},payload),({'sha256':'0'*64},payload),
                                 ({},payload[:-1]),({},payload+b'extra')]:
                (root/'ready.export').write_bytes(json.dumps({**manifest,**changes}).encode()+b'\n'+body)
                output=io.BytesIO()
                with self.assertRaises(ValueError):reader.serve(config,output,now=now)
                self.assertEqual(output.getvalue(),b'')
            (root/'outside').write_bytes(json.dumps(manifest).encode()+b'\n'+payload)
            (root/'ready.export').unlink();(root/'ready.export').symlink_to(root/'outside')
            with self.assertRaises(OSError):reader.serve(config,io.BytesIO(),now=now)

    @unittest.skipUnless(shutil.which('docker') and shutil.which('age'),'Docker/age required')
    def test_real_postgresql_clean_import_from_encrypted_recovery(self):
        import time, uuid
        if subprocess.run(['docker','info'],capture_output=True).returncode: self.skipTest('Docker daemon unavailable')
        name='hou1-pg-backup-fixture-'+uuid.uuid4().hex[:10]
        subprocess.run(['docker','run','-d','--rm','--name',name,'--network','none','--tmpfs','/var/lib/postgresql/data:rw,size=256m','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_USER=authentik','-e','POSTGRES_DB=authentik','postgres:16.6-alpine'],check=True,capture_output=True)
        try:
            for _ in range(100):
                if subprocess.run(['docker','exec',name,'pg_isready','-h','127.0.0.1','-U','authentik'],capture_output=True).returncode==0:break
                time.sleep(.1)
            subprocess.run(['docker','exec',name,'psql','-U','authentik','-d','authentik','-c',"CREATE TABLE backup_fixture (value text); INSERT INTO backup_fixture VALUES ('recover-real-postgres');"],check=True,capture_output=True)
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);config,key=self.fixture(root)
                def command(argv,output=None,timeout=300):
                    if 'pg_dump' in argv:
                        return subprocess.run(['docker','exec',name,'pg_dump','-U','authentik','-d','authentik','--no-owner','--format=plain'],stdout=output,stderr=subprocess.PIPE,check=True).stdout
                    if 'ps' in argv:return b'authentik-server\nauthentik-worker\nastro-app\nnetbird-server\n'
                    if argv[0]=='git':return b'0123456789012345678901234567890123456789'
                    return b''
                output=io.BytesIO()
                with patch.object(source,'command',side_effect=command):source.export(config,output)
                output.seek(0);manifest=root/'manifest.json';manifest.write_bytes(output.readline());archive=root/'recovery.age';archive.write_bytes(output.read())
                recovery.verify(archive,manifest,key,root/'restored','test')
                subprocess.run(['docker','exec',name,'createdb','-U','authentik','recovered'],check=True,capture_output=True)
                sql=(root/'restored/authentik/postgresql.sql').read_bytes()
                subprocess.run(['docker','exec','-i',name,'psql','-v','ON_ERROR_STOP=1','-U','authentik','-d','recovered'],input=sql,check=True,capture_output=True)
                row=subprocess.run(['docker','exec',name,'psql','-XqAt','-U','authentik','-d','recovered','-c','SELECT value FROM backup_fixture'],check=True,capture_output=True,text=True).stdout.strip()
                self.assertEqual(row,'recover-real-postgres')
        finally:subprocess.run(['docker','rm','-f',name],capture_output=True)

    def test_snapshot_failure_and_partial_stop_restart_writers(self):
        project={'name':'fixture','root':'/fixture'}
        events=[]
        def command(argv,**kwargs):
            events.append(argv)
            if 'ps' in argv:return b'astro-app\n'
            if 'stop' in argv:raise RuntimeError('Synthetic failed stop')
        with patch.object(source,'command',side_effect=command):
            with self.assertRaises(RuntimeError):
                with source.frozen(project,['astro-app']):pass
        self.assertEqual(events[-1][-2:],['start','astro-app'])
        with patch.object(source,'command',side_effect=lambda argv,**_:b'astro-app\n') as run:
            with self.assertRaises(ValueError):
                with source.frozen(project,['astro-app']):raise ValueError('snapshot failed')
        self.assertEqual(run.call_args.args[0][-2:],['start','astro-app'])

    def test_transport_denies_shell_arguments_and_arbitrary_commands(self):
        for cmd in ['', 'sh', 'export-v1;id', 'export-v1 extra', 'cat /etc/shadow']:
            with patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':cmd}),patch.object(gateway.sys,'argv',['gateway']),patch.object(gateway.os,'execv') as execute:
                with self.assertRaises(PermissionError):gateway.main()
                execute.assert_not_called()
        with patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':'export-v1'}),patch.object(gateway.sys,'argv',['gateway']),patch.object(gateway.os,'execv') as execute:
            gateway.main();self.assertEqual(execute.call_args.args[1],['sudo','-n','/usr/local/libexec/hou1-backup-export'])

    def test_source_input_guards_run_before_policy_or_filesystem_changes(self):
        import yaml
        from helpers import guard_play, run_plays
        role=yaml.safe_load((ROOT/'ansible/playbooks/roles/backup_source/tasks/main.yml').read_text())
        guard=role[0]['block'][0]
        base=dict(hou1_backup_environment='test',cms_environment='test',authentik_environment='test',node_bootstrap=False,
                  netbird_ip='100.91.0.10',netbird_subnet='100.91.0.0/16',hou1_backup_receiver_ip='100.91.0.22',
                  hou1_backup_transport_public_key='ssh-ed25519 '+'A'*68,hou1_backup_recipient='age1'+'a'*58,
                  hou1_backup_max_archive_bytes=10,hou1_backup_minimum_free_bytes=20)
        cases=[{},dict(node_bootstrap=True),dict(hou1_backup_receiver_ip='203.0.113.1'),dict(hou1_backup_receiver_ip='100.91.0.10'),dict(authentik_environment='prod'),dict(hou1_backup_recipient=''),dict(hou1_backup_transport_public_key='ssh-ed25519 bad'),dict(hou1_backup_minimum_free_bytes=1)]
        result=run_plays([guard_play({**base,**case},[guard],guard['name'] if case else None) for case in cases])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_policy_exact_members_port_direction_and_ownership(self):
        peers=[{'id':'h1','ip':'100.91.0.10'},{'id':'h4','ip':'100.91.0.22'}]
        groups=[{'id':'servers','name':'test-servers','peers':['h1','h4']}]
        args=(peers,[],'100.91.0.22','100.91.0.10','100.91.0.0/16','test')
        plan=policies.backup_plan(groups,*args)
        groups += [{'id':'g'+str(i),**g['body']} for i,g in enumerate(plan['groups'])]
        plan=policies.backup_plan(groups,*args);body=plan['policy']['body'];rule=body['rules'][0]
        self.assertEqual(rule['ports'],['22']);self.assertFalse(rule['bidirectional'])
        self.assertEqual(rule['sources'],['g0']);self.assertEqual(rule['destinations'],['g1'])
        with self.assertRaises(Exception):policies.backup_plan(groups,peers,[{**body,'id':'x','description':'operator-owned'}],*args[2:])
        groups[-1]['peers'].append('unwanted')
        with self.assertRaises(Exception):policies.backup_plan(groups,*args)

    def test_recovery_rejects_tar_traversal_links_and_devices(self):
        import tarfile
        for name,kind in [('../outside',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('device',tarfile.CHRTYPE),('link',tarfile.SYMTYPE)]:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path=root/'bad.tar'
                with tarfile.open(path,'w') as archive:
                    item=tarfile.TarInfo(name);item.type=kind;item.linkname='../../outside';archive.addfile(item)
                with self.assertRaises(ValueError):recovery.extract(path,root/'out',links=True)
