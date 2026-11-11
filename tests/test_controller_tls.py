"""Native certificate checks, encrypted issuance, rollback and renewal authority."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
from jinja2 import Environment, StrictUndefined
from helpers import ROOT, ROLES, guard_play, run_plays

sys.path.insert(0,str(ROOT/'scripts'))
import hou1_tls_material as material


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


controller=load(ROOT/'scripts/controller-tls.py','controller_tls')
receiver=load(ROLES/'controller_tls/files/receive.py','tls_receiver')
gateway=load(ROLES/'controller_tls/files/gateway.py','tls_gateway')
collector=load(ROLES/'monitoring_exporter/files/collect.py','tls_collector')


def command(*args,**kwargs):
    return subprocess.run(list(args),check=True,capture_output=True,**kwargs).stdout


class ControllerTlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temporary.name)
        command('openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(cls.root/'ca.key'),
                '-out',str(cls.root/'ca.pem'),'-subj','/CN=TLS Fixture Root','-days','5','-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
        cls.trust=patch.dict(os.environ,SSL_CERT_FILE=str(cls.root/'ca.pem'))
        cls.trust.start()
        cls.lineages={'auth.example.invalid':{'domains':['auth.example.invalid'],'probe_hosts':['auth.example.invalid']}}
        cls.bundles=[]
        for suffix in ('old','new'):
            key=cls.root/(suffix+'.key');csr=cls.root/(suffix+'.csr');cert=cls.root/(suffix+'.pem')
            command('openssl','req','-new','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(csr),'-subj','/CN=auth.example.invalid')
            ext=cls.root/'extensions';ext.write_text('subjectAltName=DNS:auth.example.invalid\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
            command('openssl','x509','-req','-in',str(csr),'-CA',str(cls.root/'ca.pem'),'-CAkey',str(cls.root/'ca.key'),
                    '-CAcreateserial','-out',str(cert),'-days','3','-extfile',str(ext))
            cls.bundles.append({'environment':'production','certificates':{'auth.example.invalid':{'fullchain':cert.read_text(),'private_key':key.read_text()}}})

    @classmethod
    def tearDownClass(cls):
        cls.trust.stop();cls.temporary.cleanup()

    def config(self,root):
        return {'environment':'production','lineages':self.lineages,'app_directory':str(root/'app'),
                'certificate_directory':str(root/'app/certbot/conf')}

    def test_native_validation_rejects_environment_san_key_and_contract_mismatches(self):
        self.assertIn('auth.example.invalid',material.validate_bundle(self.bundles[0],'production',self.lineages))
        test_bundle={**self.bundles[0],'environment':'test'}
        material.validate_bundle(test_bundle,'test',self.lineages)
        cases=[(test_bundle,self.lineages),
               (self.bundles[0],{'auth.example.invalid':{'domains':['auth.example.invalid','other.example.invalid'],'probe_hosts':['auth.example.invalid']}}),
               ({**self.bundles[0],'certificates':{}},self.lineages)]
        wrong=json.loads(json.dumps(self.bundles[0]));wrong['certificates']['auth.example.invalid']['private_key']=self.bundles[1]['certificates']['auth.example.invalid']['private_key']
        cases.append((wrong,self.lineages))
        for bundle,lineages in cases:
            with self.assertRaises((ValueError,subprocess.CalledProcessError)):
                material.validate_bundle(bundle,'production',lineages)
        with self.assertRaises(ValueError):material.validate_lineages({'../escape':self.lineages['auth.example.invalid']})
        with patch.dict(os.environ,SSL_CERT_FILE='/dev/null',SSL_CERT_DIR='/nonexistent-ca-fixture'):
            with self.assertRaises(subprocess.CalledProcessError):material.validate_bundle(self.bundles[0],'production',self.lineages)

    def test_bootstrap_noop_rotation_and_failed_reload_restore_original_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            c=self.config(Path(directory))
            with patch.object(receiver,'nginx') as nginx,patch.object(receiver,'served'):
                result=receiver.install(c,self.bundles[0],bootstrap=True)
                self.assertTrue(result['bootstrap_only']);self.assertFalse(result['verified_served_certificate'])
                nginx.assert_not_called()
                self.assertFalse(receiver.install(c,self.bundles[0])['changed'])
                nginx.assert_any_call(c,'-t')
                self.assertNotIn((c,'-s','reload'),[call.args for call in nginx.call_args_list])
                self.assertTrue(receiver.install(c,self.bundles[1])['changed'])
            folder=Path(c['certificate_directory'])/'live/auth.example.invalid'
            archive=Path(c['certificate_directory'])/'archive';archive.mkdir()
            for filename in ('fullchain.pem','privkey.pem'):
                path=folder/filename;path.replace(archive/filename);path.symlink_to('../../archive/'+filename)
            events=[]
            def invoke(config,*args):
                events.append(args)
                if args==('-s','reload') and events.count(args)==1:raise RuntimeError('private fixture')
            with patch.object(receiver,'nginx',side_effect=invoke),patch.object(receiver,'served'):
                with self.assertRaisesRegex(RuntimeError,'previous certificate files restored'):
                    receiver.install(c,self.bundles[0])
            for filename in ('fullchain.pem','privkey.pem'):
                self.assertTrue((folder/filename).is_symlink())
                self.assertEqual(os.readlink(folder/filename),'../../archive/'+filename)
            self.assertEqual((folder/'fullchain.pem').read_text(),self.bundles[1]['certificates']['auth.example.invalid']['fullchain'])
            self.assertEqual(events.count(('-s','reload')),2)
            with patch.object(receiver,'nginx'),patch.object(receiver,'served',side_effect=ValueError('wrong served fingerprint')),patch.object(receiver.time,'sleep'):
                with self.assertRaises(RuntimeError):receiver.install(c,self.bundles[0])
            self.assertTrue((folder/'privkey.pem').is_symlink())
            self.assertFalse(list(Path(directory).rglob('.tls-*')))

    def test_native_served_certificate_verifies_trust_hostname_and_fingerprint(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(5)
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.num_tickets=0
        server_context.load_cert_chain(self.root/'old.pem',self.root/'old.key')
        errors=[]
        def serve():
            try:
                raw,_=listener.accept()
                with server_context.wrap_socket(raw,server_side=True) as conn:conn.recv(1)
            except Exception as error:errors.append(type(error).__name__)
            finally:listener.close()
        worker=threading.Thread(target=serve);worker.start()
        connect=socket.create_connection
        address=listener.getsockname()
        context=ssl.create_default_context(cafile=str(self.root/'ca.pem'))
        metadata=material.validate_bundle(self.bundles[0],'production',self.lineages)
        try:
            with patch.object(receiver.socket,'create_connection',side_effect=lambda target,timeout:connect(address,timeout=timeout)),patch.object(receiver.ssl,'create_default_context',return_value=context):
                receiver.served({'lineages':self.lineages},metadata)
        finally:worker.join(6)
        self.assertEqual(errors,[])
        self.assertFalse(worker.is_alive())

    def test_status_receiver_rejects_replay_future_wrong_environment_and_non_numeric_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c=self.config(root)
            with patch.object(receiver,'nginx'),patch.object(receiver,'served'):receiver.install(c,self.bundles[0],bootstrap=True)
            data={'environment':'production','last_success':900,'last_run_success':1,'observed_at':1000}
            with patch.object(receiver,'STATE',root):
                self.assertTrue(receiver.publish_status(c,data,now=1000)['passed'])
                original=(root/'controller-status.json').read_bytes()
                for override in [{'environment':'test'},{'observed_at':999},{'observed_at':800},{'observed_at':1100},
                                 {'last_success':float('nan')},{'last_success':True},{'last_run_success':True},
                                 {'last_run_success':2},{'last_success':1001},{'certificate_not_after':1}]:
                    with self.assertRaises(ValueError):receiver.publish_status(c,{**data,**override},now=1000)
                    self.assertEqual((root/'controller-status.json').read_bytes(),original)
            text,success=collector.renewal_metrics('production',now=1000,path=root/'controller-status.json')
            self.assertTrue(success);self.assertIn('hou1_tls_last_run_success 1.0',text)
            self.assertNotIn('example.invalid',text)
            text,success=collector.renewal_metrics('test',now=1000,path=root/'controller-status.json')
            self.assertFalse(success);self.assertIn('hou1_tls_last_success_timestamp_seconds 0.0',text)

    def test_gateway_denies_shell_arguments_bootstrap_and_other_commands(self):
        with patch.object(sys,'argv',['gateway']):
            for action,tail in [('deploy-v1',[]),('status-v1',['--metrics'])]:
                with patch.dict(os.environ,SSH_ORIGINAL_COMMAND=action),patch.object(gateway.os,'execv') as execute:
                    gateway.main();execute.assert_called_once_with('/usr/bin/sudo',['sudo','-n','/usr/local/libexec/hou1-tls-receive',*tail])
            for action in ('bash','deploy-v1 --bootstrap','status-v1; id','','sudo -n /bin/sh'):
                with patch.dict(os.environ,SSH_ORIGINAL_COMMAND=action),patch.object(gateway.os,'execv') as execute:
                    with self.assertRaises(PermissionError):gateway.main()
                    execute.assert_not_called()
        with patch.object(sys,'argv',['gateway','--bootstrap']),patch.dict(os.environ,SSH_ORIGINAL_COMMAND='deploy-v1'):
            with self.assertRaises(PermissionError):gateway.main()

    def test_native_ssh_config_requires_pinned_private_renewal_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);key=root/'ssh.key'
            command('ssh-keygen','-q','-t','ed25519','-N','','-f',str(key))
            (root/'age.key').write_text('fixture');(root/'age.key').chmod(0o600)
            (root/'dns.enc.json').write_text('{}')
            known=root/'known_hosts';known.write_text('100.90.0.10 '+Path(str(key)+'.pub').read_text());known.chmod(0o600)
            c={'environment':'production','lineages':self.lineages,'recipients':['age1'+'q'*58],
               'email':'operator@example.invalid','age_key_file':str(root/'age.key'),
               'dns_secret_source':str(root/'dns.enc.json'),'ssh_config':str(root/'ssh.conf'),
               'ssh_host':'renewal','netbird_ip':'100.90.0.10','netbird_subnet':'100.90.0.0/16'}
            base=f'Host renewal\n Hostname 100.90.0.10\n User hou1-renewal\n IdentityFile {key}\n UserKnownHostsFile {known}\n StrictHostKeyChecking yes\n IdentitiesOnly yes\n BatchMode yes\n PasswordAuthentication no\n'
            config=root/'config.json';config.write_text(json.dumps(c));config.chmod(0o600)
            for text,valid in [(base,True),(base.replace('Checking yes','Checking no'),False),
                               (base.replace('User hou1-renewal','User root'),False),
                               (base.replace('Hostname 100.90.0.10','Hostname 203.0.113.10'),False),
                               (base.replace(str(known),'/dev/null'),False)]:
                Path(c['ssh_config']).write_text(text);Path(c['ssh_config']).chmod(0o600)
                if valid:self.assertEqual(controller.read_config(config,transport=True)[0],c)
                else:
                    with self.assertRaises(ValueError):controller.read_config(config,transport=True)
            Path(c['ssh_config']).write_text(base);known.chmod(0o666)
            with self.assertRaises(ValueError):controller.read_config(config,transport=True)
            known.chmod(0o600);root.chmod(0o755)
            with self.assertRaises(ValueError):controller.read_config(config)

    def test_real_sops_issuance_dry_run_cleanup_and_failed_delivery_preserve_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);identity=root/'age.key'
            command('age-keygen','-o',str(identity));recipient=command('age-keygen','-y',str(identity)).decode().strip()
            token='fixture_DNS_only_token'
            c={'environment':'production','lineages':self.lineages,'email':'operator@example.invalid',
               'recipients':[recipient],'age_key_file':str(identity),'dns_secret_source':str(root/'dns.enc.json')}
            controller.private_write(root/'dns.enc.json',controller.encrypted({'cloudflare_dns_api_token':token},c,root))
            original_run=subprocess.run;events=[]
            def docker(args,**kwargs):
                events.append(args)
                if args[:2]==['docker','run']:
                    self.assertNotIn(token,' '.join(args))
                    work=Path(next(arg.split(':/etc/letsencrypt')[0] for arg in args if arg.endswith(':/etc/letsencrypt')))
                    folder=work/'live/auth.example.invalid';folder.mkdir(parents=True,exist_ok=True)
                    folder.joinpath('fullchain.pem').write_text(self.bundles[0]['certificates']['auth.example.invalid']['fullchain'])
                    folder.joinpath('privkey.pem').write_text(self.bundles[0]['certificates']['auth.example.invalid']['private_key'])
                    return subprocess.CompletedProcess(args,0,token.encode(),b'')
                if args[:3]==['docker','rm','--force']:return subprocess.CompletedProcess(args,0,b'',b'')
                return original_run(args,**kwargs)
            with patch.object(controller,'subprocess',SimpleNamespace(run=docker)):
                self.assertTrue(controller.issue(c,root)['passed'])
                bundle=controller.decrypt(root/'certificates.enc.json',c)
                self.assertEqual(bundle,self.bundles[0]);self.assertNotIn(token,json.dumps(bundle))
                before={name:(root/name).read_bytes() for name in ('acme.enc.json','certificates.enc.json')}
                self.assertTrue(controller.issue(c,root,dry_run=True)['passed'])
                self.assertEqual(before,{name:(root/name).read_bytes() for name in before})
                fingerprints={name:item['sha256'] for name,item in material.validate_bundle(bundle,c['environment'],c['lineages']).items()}
                with patch.object(controller,'ssh',return_value={'passed':True,'verified_served_certificate':True,'sha256':fingerprints}):
                    self.assertTrue(controller.issue(c,root,deploy=True)['passed'])
                controller.check_ready(c,root)
                success=root/'tls-success.json'
                receipt=json.loads(success.read_text())
                success.write_text(json.dumps({**receipt,'recorded_at':0}))
                with self.assertRaises(ValueError):controller.check_ready(c,root)
                success.write_text(json.dumps({**receipt,'sha256':{}}))
                with self.assertRaises(ValueError):controller.check_ready(c,root)
                success.write_bytes(b'previous-success')
                with patch.object(controller,'ssh',return_value={'passed':True,'verified_served_certificate':True,'sha256':{'wrong':'fingerprint'}}):
                    with self.assertRaises(RuntimeError):controller.issue(c,root,deploy=True)
                self.assertEqual(success.read_bytes(),b'previous-success')
                def timeout(args,**kwargs):
                    if args[:2]==['docker','run']:raise subprocess.TimeoutExpired(args,720)
                    return docker(args,**kwargs)
                with patch.object(controller,'subprocess',SimpleNamespace(run=timeout)):
                    with self.assertRaises(subprocess.TimeoutExpired):controller.issue(c,root)
                self.assertFalse(list(root.glob('hou1-acme-*')))
                self.assertNotIn(token,(root/'certbot.log').read_text())
                self.assertEqual((root/'certificates.enc.json').stat().st_mode&0o777,0o600)
                self.assertTrue(any(args[:3]==['docker','rm','--force'] for args in events))

    def test_failure_receipt_and_service_failure_publication_preserve_last_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c={'environment':'production'}
            success={'environment':'production','passed':True,'recorded_at':900}
            (root/'tls-success.json').write_text(json.dumps(success))
            with patch.object(sys,'argv',['controller','--config','fixture','--deploy']),patch.object(controller,'read_config',return_value=(c,root)),patch.object(controller,'issue',side_effect=RuntimeError('private fixture')):
                with self.assertRaises(RuntimeError):controller.main()
            self.assertFalse(json.loads((root/'tls-last-run.json').read_text())['passed'])
            self.assertEqual(json.loads((root/'tls-success.json').read_text()),success)
            (root/'tls-last-run.json').write_text(json.dumps({'passed':True}))
            with patch.object(controller,'run',return_value=b'exit-code\n'),patch.object(controller,'ssh',return_value={'passed':True}) as ssh:
                self.assertTrue(controller.publish(c,root,now=1000)['passed'])
                self.assertEqual(ssh.call_args.args[1],'status-v1')
                self.assertEqual(ssh.call_args.args[2]['last_run_success'],0)
                self.assertEqual(ssh.call_args.args[2]['last_success'],900)

    def test_import_and_transport_guards_fail_before_mutations(self):
        main=yaml.safe_load((ROLES/'controller_tls/tasks/main.yml').read_text())[0]
        base={'hou1_controller_managed_tls':True,'cms_environment':'production','hou1_tls_identity_profile':'prod',
              'hou1_tls_lineages':{**self.lineages,'netbird.example.invalid':{'domains':['netbird.example.invalid'],'probe_hosts':['netbird.example.invalid']},
                                   'example.invalid':{'domains':['example.invalid'],'probe_hosts':['example.invalid']}},
              'ssl_domains':[{'name':'example.invalid'}],'netbird_domain':'netbird.example.invalid','primary_domain':'example.invalid',
              'app_directory':'/opt/application','hou1_tls_bundle_source':'/private/certificates.enc.json','hou1_tls_age_key_file':'/private/age.key'}
        cases=[({},None),({'cms_environment':'test'},main['name']),({'hou1_tls_identity_profile':'test'},main['name']),
               ({'rehearsal_controller_managed_tls':True},main['name']),({'hou1_tls_lineages':{}},main['name'])]
        result=run_plays([guard_play({**base,**extra},[main],guard) for extra,guard in cases],
                         inventory_source={'all':{'children':{'prod_env':{'hosts':{'localhost':{}}}}}})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        access=yaml.safe_load((ROLES/'controller_tls/tasks/access.yml').read_text())[0]
        variables={'hou1_controller_managed_tls':True,'hou1_tls_inventory_group':'private-target',
                   'cms_environment':'production','node_bootstrap':False,'ansible_host':'100.90.0.10','netbird_ip':'100.90.0.10',
                   'netbird_subnet':'100.90.0.0/16','hou1_tls_controller_ip':'100.90.0.30',
                   'hou1_tls_renewal_public_key':'ssh-ed25519 '+'A'*68+' fixture'}
        cases=[({},None),({'node_bootstrap':True},access['name']),({'ansible_host':'203.0.113.1'},access['name']),
               ({'hou1_tls_controller_ip':'203.0.113.1'},access['name']),({'hou1_tls_controller_ip':'100.90.0.10'},access['name']),
               ({'hou1_tls_renewal_public_key':variables['hou1_tls_renewal_public_key']+'\nsh'},access['name'])]
        inventory={'all':{'children':{'private-target':{'hosts':{'localhost':{}}},'prod_env':{'hosts':{'localhost':{}}}}}}
        result=run_plays([guard_play({**variables,**extra},[access],guard) for extra,guard in cases],inventory_source=inventory)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_real_sops_import_preflight_never_logs_decrypted_material(self):
        tasks=yaml.safe_load((ROLES/'controller_tls/tasks/main.yml').read_text())[:5]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);identity=root/'age.key'
            command('age-keygen','-o',str(identity));recipient=command('age-keygen','-y',str(identity)).decode().strip()
            lineages={name:{'domains':[name],'probe_hosts':[name]} for name in
                      ('example.invalid','auth.example.invalid','netbird.example.invalid')}
            sentinel='PRIVATE_TLS_MATERIAL_FIXTURE_DO_NOT_LOG'
            payload={'environment':'production','certificates':{name:{'fullchain':sentinel,'private_key':sentinel} for name in lineages}}
            c={'age_key_file':str(identity),'recipients':[recipient]}
            ciphertext=root/'certificates.enc.json'
            controller.private_write(ciphertext,controller.encrypted(payload,c,root))
            inputs={'hou1_controller_managed_tls':True,'cms_environment':'production','hou1_tls_identity_profile':'prod',
                    'hou1_tls_lineages':lineages,'ssl_domains':[{'name':'example.invalid','flags':'-d example.invalid'}],
                    'primary_domain':'example.invalid','netbird_domain':'netbird.example.invalid','app_directory':'/opt/application',
                    'hou1_tls_bundle_source':str(ciphertext),'hou1_tls_age_key_file':str(identity)}
            inventory={'all':{'children':{'prod_env':{'hosts':{'localhost':{}}}}}}
            result=run_plays([guard_play(inputs,tasks)],extra_args=('--diff','-vv'),inventory_source=inventory)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn(sentinel,result.stdout+result.stderr)
            payload['environment']='test'
            controller.private_write(ciphertext,controller.encrypted(payload,c,root))
            result=run_plays([guard_play(inputs,tasks,tasks[-1]['name'])],extra_args=('--diff','-vv'),inventory_source=inventory)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn(sentinel,result.stdout+result.stderr)

    def test_production_mode_skips_host_dns_issuance_and_legacy_renewal_writes(self):
        names={
            'webstack':['Deploy Cloudflare API Credentials File','Obtain or update SSL certificates via Cloudflare DNS-01',
                        'Create automated SSL renewal script','Schedule cron job for SSL renewal'],
            'authentik':['Obtain the Authentik DNS-01 certificate'],
            'netbird_control_plane':['Prepare DNS challenge credentials for the NetBird certificate',
                                    'Obtain the dedicated NetBird certificate through DNS-01',
                                    'Preserve NetBird certificate renewal credentials for the app renewal job']}
        tasks=[]
        for role, selected in names.items():
            tasks.extend(task for task in yaml.safe_load((ROLES/role/'tasks/main.yml').read_text()) if task.get('name') in selected)
        # Paths/credentials deliberately absent: every legacy task must skip before
        # templating or changing the host, rather than quietly rendering a token.
        result=run_plays([{'variables':{'cms_environment':'production','hou1_controller_managed_tls':True,
                                      'rehearsal_controller_managed_tls':False,
                                      'authentik_certificate':{'stat':{'exists':False}},
                                      'netbird_certificate':{'stat':{'exists':False}}},'tasks':tasks}])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('changed=0',result.stdout)

    def test_native_systemd_units_are_environment_bound_and_persistent(self):
        env=Environment(undefined=StrictUndefined)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for environment in ('test','production'):
                variables={'ansible_playbook_python':sys.executable,'hou1_tls_schedule_directory':'/private/tls',
                           'hou1_tls_controller_config':'/private/tls/config.json','hou1_tls_schedule_environment':environment}
                files=[]
                for name in ('renew.service','renew.timer','status.service','status.timer'):
                    text=env.from_string((ROLES/'controller_tls/templates'/f'{name}.j2').read_text()).render(**variables)
                    unit='hou1-tls-'+environment+('-status' if name.startswith('status') else '')+'.'+name.rsplit('.',1)[1]
                    path=root/unit;path.write_text(text.replace('/private/tls/controller-tls.py','/usr/bin/true'));files.append(str(path))
                    if name=='renew.service':self.assertIn('--deploy',text)
                    if name.endswith('timer'):self.assertIn('Persistent=true',text)
                result=subprocess.run(['systemd-analyze','--user','verify',*files],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
