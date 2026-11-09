#!/usr/bin/python3
"""Start only the private recovery project; retain diagnostics in its root-only directory."""
import hashlib,json,os,shutil,sqlite3,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;PROJECT=json.loads((ROOT/'drill-input.json').read_text())['project']
os.umask(0o077)
def run(args,input=None,timeout=180):
 p=subprocess.run(args,input=input,capture_output=True,timeout=timeout)
 if p.returncode:
  with (ROOT/'private-diagnostics.log').open('ab') as f:f.write(p.stdout+p.stderr)
  raise RuntimeError('Private recovery command failed: '+args[0])
 return p.stdout
base=['docker','compose','--project-directory',str(ROOT),'-p',PROJECT,'-f',str(ROOT/'compose.json')]
def dc(*args,**kwargs):return run(base+list(args),**kwargs)
def inspect(names):return json.loads(run(['docker','inspect',*names]))
def sql(query):return dc('exec','-T','postgresql','psql','-h','127.0.0.1','-XqAt','-U','authentik','-d','authentik','-c',query).decode().strip()
def sqlite_report(path):
 with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
  assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
  tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
  return {table:db.execute('SELECT count(*) FROM "'+table.replace('"','""')+'"').fetchone()[0] for table in tables}
def hash_tree(path):
 return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file() and not p.is_symlink()}
SESSION_TABLES=['django_session','authentik_core_authenticatedsession','authentik_providers_oauth2_authorizationcode','authentik_providers_oauth2_accesstoken','authentik_providers_oauth2_refreshtoken']
def reset_restored_sessions(label):
 for service in ['authentik-server','authentik-worker','astro-app']:
  assert not run(['docker','ps','-q','--filter','label=com.docker.compose.project='+PROJECT,'--filter','label=com.docker.compose.service='+service]).strip(),'Restored writers must be stopped during session invalidation'
 before={table:int(sql('SELECT count(*) FROM '+table)) for table in SESSION_TABLES}
 sql('BEGIN; TRUNCATE '+','.join(SESSION_TABLES)+' CASCADE; COMMIT;')
 assert all(int(sql('SELECT count(*) FROM '+table))==0 for table in SESSION_TABLES)
 with sqlite3.connect(ROOT/'payload/cms/database/forms.db') as db:
  staff_before=db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0]
  db.execute('UPDATE staff_sessions SET revoked_at = ? WHERE revoked_at IS NULL',(int(time.time()*1000),))
  assert db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0]==0
 report=dict(idp_sessions_codes_tokens_cleared=True,staff_sessions_revoked=True,writers_stopped=True,idp_rows_before=before,unrevoked_staff_rows_before=staff_before)
 (ROOT/('session-reset-'+label+'.json')).write_text(json.dumps(report,indent=2))
 return report

def wait_ready(mapping):
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  containers=inspect([PROJECT+'-'+service+'-1' for service in mapping])
  for c in containers:
   assert c['State']['Status'] not in ('exited','dead'), 'Restored container stopped: '+c['Name']
   assert c['State'].get('Health',{}).get('Status')!='unhealthy','Restored container unhealthy: '+c['Name']
  if all(c['State']['Running'] and c['State'].get('Health',{}).get('Status','healthy')=='healthy' for c in containers):return containers
  time.sleep(2)
 raise TimeoutError('Isolated restored services did not become ready')

def wait_postgresql():
 deadline=time.monotonic()+90
 while time.monotonic()<deadline:
  p=subprocess.run(base+['exec','-T','postgresql','psql','-h','127.0.0.1','-XqAt','-v','ON_ERROR_STOP=1','-U','authentik','-d','authentik','-c','SELECT 1'],capture_output=True,timeout=10)
  if p.returncode==0 and p.stdout.strip()==b'1':break
  time.sleep(1)
 else:raise TimeoutError('Fresh PostgreSQL did not become ready')

def main():
 started=time.time();cfg=json.loads((ROOT/'drill-input.json').read_text());assert cfg['environment']=='test' and cfg['project']==PROJECT and cfg['root']==str(ROOT)
 assert PROJECT.startswith('hou1-recovery-drill-') and ROOT.parent==Path('/var/tmp') and ROOT.name==PROJECT
 existing=run(['docker','ps','-a','-q','--filter','label=com.docker.compose.project='+PROJECT])
 assert not existing.strip(),'Recovery project must start empty'
 saved=json.loads((ROOT/'payload/recovery.json').read_text());assert saved['staff_revision']==cfg['staff_revision']
 deployed=run(['git','-C',cfg['app_source'],'rev-parse','HEAD']).decode().strip();assert deployed==saved['staff_revision']
 names=cfg['primary_containers']
 live=inspect(names);baseline={c['Name']:dict(id=c['Id'],started=c['State']['StartedAt']) for c in live}
 (ROOT/'live-baseline.json').write_text(json.dumps(baseline))
 compose=json.loads((ROOT/'compose.json').read_text());images={}
 mapping={'postgresql':names[2],'authentik-server':names[0],'authentik-worker':names[1],'netbird-server':names[3],'dashboard':names[4],'astro-app':names[5],'proxy':names[6]}
 for service,name in mapping.items():
  c=next(c for c in live if c['Name']=='/'+name)
  image=cfg['app_image'] if service=='astro-app' else c['Image']
  assert image.startswith('sha256:')
  run(['docker','image','inspect',image])
  images[service]=image;compose['services'][service]['image']=image
  s=compose['services'][service];assert not s.get('ports') and not s.get('privileged') and s.get('networks')
  for volume in s.get('volumes',[]):assert volume.split(':',1)[0].startswith(str(ROOT)+'/')
 assert compose['networks']['recovery']['internal'] is True
 (ROOT/'compose.json').write_text(json.dumps(compose,indent=2))
 for folder in ['media','templates','certs','config']:
  path=ROOT/'payload/authentik-files'/folder
  for p in [path,*path.rglob('*')]:
   if not p.is_symlink():os.chown(p,1000,1000)
 for path in [ROOT/'payload/cms/database',ROOT/'payload/cms/media']:
  for p in [path,*path.rglob('*')]:
   if not p.is_symlink():os.chown(p,100,102)
 before=dict(cms=sqlite_report(ROOT/'payload/cms/database/forms.db'),netbird={p.name:sqlite_report(p) for p in (ROOT/'payload/netbird-files/data').glob('*.db')},media_hashes=hash_tree(ROOT/'payload/cms/media'),authentik_hashes=hash_tree(ROOT/'payload/authentik-files'))
 (ROOT/'source-data-report.json').write_text(json.dumps(before,indent=2))
 dc('up','-d','--pull','never','postgresql')
 wait_postgresql()
 assert sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")=='0'
 with (ROOT/'payload/authentik/postgresql.sql').open('rb') as source:
  p=subprocess.run(base+['exec','-T','postgresql','psql','-h','127.0.0.1','-X','-v','ON_ERROR_STOP=1','-U','authentik','-d','authentik'],stdin=source,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180)
  if p.returncode:(ROOT/'private-import.log').write_bytes(p.stderr);raise RuntimeError('Logical import failed')
 imported=int(sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"));assert imported>0
 counts={table:int(sql('SELECT count(*) FROM '+table)) for table in ['authentik_core_user','authentik_core_group','authentik_core_application','authentik_providers_oauth2_oauth2provider','authentik_crypto_certificatekeypair']}
 reset_restored_sessions('before-start')
 dc('up','-d','--pull','never','authentik-server','authentik-worker','astro-app','netbird-server','dashboard','proxy',timeout=420)
 containers=wait_ready(mapping)
 network=json.loads(run(['docker','network','inspect',PROJECT]))[0];assert network['Internal'] and set(network['Containers'])=={c['Id'] for c in containers}
 for c in containers:
  assert c['State']['Running'] and not c['HostConfig']['PortBindings']
  assert set(c['NetworkSettings']['Networks'])=={PROJECT}
  assert all(m['Source'].startswith(str(ROOT)+'/') for m in c['Mounts'])
  assert not c['HostConfig']['Privileged']
 result=dict(schema=1,environment='test',started=int(started),stack_started=int(time.time()),restore_startup_seconds=round(time.time()-started,3),postgres_imported_into_empty_database=True,postgres_tables=imported,identity_counts=counts,staff_revision=saved['staff_revision'],tested_app_revision=cfg.get('candidate_revision',saved['staff_revision']),images=images,all_seven_running=True,internal_network=True,published_ports=0,all_mounts_private=True,only_drill_containers_attached=True,source_counts=before,live_services_untouched=True)
 assert {c['Name']:dict(id=c['Id'],started=c['State']['StartedAt']) for c in inspect(names)}==baseline
 (ROOT/'startup-report.json').write_text(json.dumps(result,indent=2))
 print(json.dumps({k:v for k,v in result.items() if k!='source_counts'}))
if __name__=='__main__':main()
