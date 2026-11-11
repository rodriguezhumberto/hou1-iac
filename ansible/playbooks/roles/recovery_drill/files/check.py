#!/usr/bin/python3
import hashlib,importlib.util,json,os,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;PROJECT=json.loads((ROOT/'drill-input.json').read_text())['project'];os.umask(0o077)
spec=importlib.util.spec_from_file_location('restore_start',ROOT/'runtime.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cfg=json.loads((ROOT/'drill-input.json').read_text());secret=json.loads((ROOT/'health-input.json').read_text());script=(ROOT/'http_probe.py').read_text()
def probe(action,state=None):
 values={**cfg,**secret,'reference_jwks':json.loads((ROOT/'reference-jwks.json').read_text()),'action':action,'continuation':state}
 p=subprocess.run(['docker','exec','-i',PROJECT+'-authentik-server-1','python','-c',script],input=json.dumps(values).encode(),capture_output=True,timeout=180)
 (ROOT/('private-probe-'+action+'.json')).write_bytes(p.stdout)
 try:out=json.loads(p.stdout)
 except Exception:(ROOT/'private-probe-errors.log').write_bytes(p.stderr);raise RuntimeError('Invalid private probe report')
 if p.returncode or out.get('failed'):raise RuntimeError('Recovery HTTP phase '+action+' failed: '+json.dumps(out))
 return out

def main():
 started=time.time();assert (ROOT/'startup-report.json').is_file()
 original=ROOT/'rollback-original';original.mkdir(mode=0o700,exist_ok=True)
 # Preserve verified snapshot state before exercising mutations in the restored app.
 # Use the original validated recovery database sent by the operator, not a live source read.
 source=ROOT/'payload/cms/database/forms.db'
 # App has only started; original snapshot copy is separately supplied for exact rollback.
 assert (ROOT/'original-cms/database/forms.db').is_file()
 for source,target in [(ROOT/'original-cms/database',original/'database'),(ROOT/'original-cms/media',original/'media')]:
  if not target.exists():shutil.copytree(source,target)
 before=json.loads((ROOT/'source-data-report.json').read_text())
 with m.sqlite3.connect(ROOT/'payload/cms/database/forms.db') as db:
  db.execute('UPDATE staff_sessions SET revoked_at = ? WHERE revoked_at IS NULL',(int(time.time()*1000),))
  assert db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0]==0
 setup=probe('session-reset-setup')
 m.dc('stop','--timeout','30','astro-app','authentik-server','authentik-worker',timeout=120)
 m.reset_restored_sessions('live-proof')
 m.dc('start','authentik-server','authentik-worker','astro-app',timeout=120)
 m.wait_ready(['postgresql','authentik-server','authentik-worker','netbird-server','dashboard','astro-app','proxy'])
 reset=probe('after-session-reset',setup['continuation'])
 first=probe('initial');state=first['continuation']
 results={'session_reset_setup':setup['checks'],'after_session_reset':reset['checks'],'initial':first['checks']}
 m.dc('restart','astro-app',timeout=120)
 m.wait_ready(['postgresql','authentik-server','authentik-worker','netbird-server','dashboard','astro-app','proxy'])
 results['after_restart']=probe('after-restart',state)['checks']
 rollback_started=time.time();m.dc('stop','--timeout','30','astro-app',timeout=90)
 for name in ['database','media']:
  target=ROOT/'payload/cms'/name;shutil.rmtree(target);shutil.copytree(original/name,target)
  for path in [target,*target.rglob('*')]:
   if not path.is_symlink():os.chown(path,100,102)
 assert m.sqlite_report(ROOT/'payload/cms/database/forms.db')==before['cms']
 assert m.hash_tree(ROOT/'payload/cms/media')==before['media_hashes']
 with m.sqlite3.connect(ROOT/'payload/cms/database/forms.db') as db:
  db.execute('UPDATE staff_sessions SET revoked_at = ? WHERE revoked_at IS NULL',(int(time.time()*1000),))
  assert db.execute('SELECT count(*) FROM staff_sessions WHERE revoked_at IS NULL').fetchone()[0]==0
 m.dc('start','astro-app',timeout=120);m.wait_ready(['postgresql','authentik-server','authentik-worker','netbird-server','dashboard','astro-app','proxy'])
 results['after_rollback']=probe('after-rollback',state)['checks']
 report=dict(restored_staff_sessions_invalidated_before_login_and_after_rollback=True,schema=1,passed=True,environment='test',recorded_at=int(time.time()),checks=results,check_count=sum(len(checks) for checks in results.values()),runtime_identity_and_application_checks_seconds=round(time.time()-started,3),staff_database_media_rollback_seconds=round(time.time()-rollback_started,3),rollback_scope='Restored-copy staff SQLite/media to the actual recovery point; not a live HOU1 traffic cutover',original_cms_table_counts=before['cms'],original_netbird_table_counts=before['netbird'])
 (ROOT/'http-report.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['original_cms_table_counts','original_netbird_table_counts']}))
if __name__=='__main__':main()
