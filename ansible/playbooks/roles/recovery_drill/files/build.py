from pathlib import Path
import importlib.util,json,os,subprocess,time,tarfile
os.umask(0o077)
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('start',ROOT/'runtime.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cfg=json.loads((ROOT/'drill-input.json').read_text());started=time.time()
if 'application_image' in cfg:
 spec=importlib.util.spec_from_file_location('application_artifact',ROOT/'artifact.py');artifact=importlib.util.module_from_spec(spec);spec.loader.exec_module(artifact)
 receipt=artifact.verify_saved(cfg['application_image'],cfg['staff_revision'],pull=True)
 cfg['application_image']=receipt
 image=receipt['image_id']
 compose=json.loads((ROOT/'compose.json').read_text());compose['services']['astro-app']['image']=receipt['image'];(ROOT/'compose.json').write_text(json.dumps(compose,indent=2))
 (ROOT/'build-report.json').write_text(json.dumps(dict(staff_revision=cfg['staff_revision'],image_id=image,application_image=receipt,source='saved CI image digest',host_build=False),indent=2))
else:
 build=ROOT/'build';build.mkdir(mode=0o700,exist_ok=True);assert not any(build.iterdir())
 archive=m.run(['git','-C',cfg['app_source'],'archive',cfg['staff_revision']])
 import io
 archive_path=ROOT/'source.tar';archive_path.write_bytes(archive)
 safe_spec=importlib.util.spec_from_file_location('safe_archive',ROOT/'safe_archive.py');safe=importlib.util.module_from_spec(safe_spec);safe_spec.loader.exec_module(safe)
 safe.extract(archive_path,build,links=True)
 with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
  for member in tar.getmembers():
   path=build/member.name
   if not path.is_symlink():path.chmod(0o755 if member.isdir() or member.mode & 0o111 else 0o644)
 values={}
 for line in (ROOT/'payload/app-config/.env').read_text().splitlines():
  if '=' in line and not line.startswith('#'):
   k,v=line.split('=',1);values[k]=v.strip().strip('"').strip("'")
 args=['docker','build','--label','org.opencontainers.image.revision='+cfg['staff_revision'],'--pull=false','-t','hou1-recovery-app:'+cfg['staff_revision'],'-f',str(build/'astro-app/Dockerfile')]
 keys=['PUBLIC_HCAPTCHA_SITE_KEY','PUBLIC_VAPID_PUBLIC_KEY','PUBLIC_SITE_ORIGIN','STAFF_OIDC_ISSUER','STAFF_OIDC_CLIENT_ID','STAFF_OIDC_REDIRECT_URI','STAFF_OIDC_POST_LOGOUT_REDIRECT_URI','STAFF_OIDC_SCOPES']
 for key in keys:args+=['--build-arg',key]
 args+=[str(build)]
 with (ROOT/'private-build.log').open('wb') as log:
  p=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,**{k:values.get(k,'') for k in keys}},timeout=900)
 assert p.returncode==0,'Recovery image build failed; see private log'
 image=json.loads(m.run(['docker','image','inspect','hou1-recovery-app:'+cfg['staff_revision']]))[0]['Id']
 compose=json.loads((ROOT/'compose.json').read_text());compose['services']['astro-app']['image']=image;(ROOT/'compose.json').write_text(json.dumps(compose,indent=2))
 (ROOT/'build-report.json').write_text(json.dumps(dict(staff_revision=cfg['staff_revision'],image_id=image,build_seconds=round(time.time()-started,3),source='git archive of exact recovery revision',runtime_configuration='saved recovery environment; public build arguments only',artifact_requirement='Exact saved source and cached base images/dependencies; no retained image registry assumed'),indent=2))
cfg['app_image']=image;(ROOT/'drill-input.json').write_text(json.dumps(cfg,indent=2))
print(json.dumps({'staff_revision':cfg['staff_revision'],'image_id':image,'host_build':'application_image' not in cfg,'elapsed_seconds':round(time.time()-started,3)}))
