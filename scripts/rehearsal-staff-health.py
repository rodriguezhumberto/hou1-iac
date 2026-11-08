#!/usr/bin/env python3
"""Routine isolated login/read/role/logout probe; secrets arrive only on stdin.

Intentional callback replay belongs to the dedicated OIDC security gate, since
repeating attacks during a health check would trigger real failure-rate alerts.
"""
import http.cookiejar, json, os, subprocess, urllib.request, urllib.parse, urllib.error, sys
AUTH='https://auth-test.vps-test.pearlandpaws.com'
STAFF='https://staff-test.vps-test.pearlandpaws.com'
s=json.load(sys.stdin)
results={}
class Redirects(urllib.request.HTTPRedirectHandler):
 def __init__(self): self.callback=None
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  if newurl.startswith(STAFF+'/api/staff/oidc/callback?'):self.callback=newurl
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def run(username,password_key):
 jar=http.cookiejar.CookieJar(); redirect=Redirects(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),redirect)
 def request(url,data=None):
  headers={'Accept':'application/json','Referer':AUTH+'/','Origin':AUTH}
  if data is not None:
   headers['Content-Type']='application/json'
   csrf=next((c.value for c in jar if c.name in ['authentik_csrf','csrftoken']),None)
   if csrf:headers['X-CSRFToken']=csrf
  return op.open(urllib.request.Request(url,headers=headers,data=None if data is None else json.dumps(data).encode()),timeout=20)
 first=request(STAFF+'/api/staff/login');target=first.geturl()
 assert urllib.parse.urlsplit(target).netloc==urllib.parse.urlsplit(AUTH).netloc, 'authorization did not reach Authentik'
 q=urllib.parse.urlsplit(target).query
 api=AUTH+'/api/v3/flows/executor/default-authentication-flow/?'+urllib.parse.urlencode({'query':q})
 response=json.load(request(api))
 for _ in range(8):
  component=response.get('component');kind=response.get('type')
  if kind=='redirect' or component=='xak-flow-redirect':
   dest=urllib.parse.urljoin(AUTH,response['to']); final=request(dest);break
  if component=='ak-stage-identification':response=json.load(request(api,{'uid_field':username,'password':s[password_key]}))
  elif component=='ak-stage-password':response=json.load(request(api,{'password':s[password_key]}))
  elif component=='ak-stage-consent':response=json.load(request(api,{'consent':True}))
  else:raise RuntimeError('unexpected authentication stage '+str(component))
 else:raise RuntimeError('authentication flow did not finish')
 final_path=urllib.parse.urlsplit(final.geturl()).path
 assert final.status==200 and final_path not in ['/staff/login','/login'], 'callback failed with final path '+final_path
 cookie=next((c.value for c in jar if c.name=='pp_staff_session'),None)
 assert cookie,'callback did not create a staff session'
 with request(STAFF+'/api/staff/cms/pets') as r:assert r.status==200
 results[username]={'login':'passed','protected_read':'passed'}
 if username.endswith('reader'):
  try: request(STAFF+'/api/staff/cms/pets',{'title':'Denied test mutation'})
  except urllib.error.HTTPError as e:assert e.code==403
  else:raise AssertionError('reader mutation was allowed')
  results[username]['reader_mutation_denied']='passed'
 assert redirect.callback,'authorization callback was not observed'
 with request(STAFF+'/api/staff/logout') as r:assert r.status==200
 req=urllib.request.Request(STAFF+'/api/staff/cms/pets',headers={'Cookie':'pp_staff_session='+cookie})
 try: urllib.request.urlopen(req,timeout=15)
 except urllib.error.HTTPError as e:assert e.code==401
 else:raise AssertionError('logged-out session remained usable')
 results[username]['logout_revoked_session']='passed'
try:
 for user,key in [('vps-staff-admin','TEST_ADMIN_PASSWORD'),('vps-staff-reader','TEST_READER_PASSWORD')]:run(user,key)
except Exception:
 print(json.dumps({'routine_login_probe':'failed'}))
 sys.exit(1)
print(json.dumps({'routine_login_probe':'passed','identities_checked':len(results),'checks_passed':sum(len(v) for v in results.values())}))
