import base64,hashlib,http.cookiejar,json,socket,struct,sys,urllib.error,urllib.parse,urllib.request,zlib
s=json.load(sys.stdin)
AUTH=s['auth_origin'];STAFF=s['staff_origin'];NETBIRD=s['netbird_origin'];PUBLIC=s['public_origin'];checks={}
class Redirects(urllib.request.HTTPRedirectHandler):
 def __init__(self):self.callback=None
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  if newurl.startswith(STAFF+'/api/staff/oidc/callback?'):self.callback=newurl
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def opener():
 jar=http.cookiejar.CookieJar();r=Redirects();return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),r),jar,r
def request(op,url,data=None,headers=None,ctype=None):
 origin=AUTH if url.startswith(AUTH) else STAFF
 h={'Accept':'application/json','Origin':origin,'Referer':origin+'/'};h.update(headers or {})
 if data is not None:
  if not isinstance(data,bytes):data=json.dumps(data).encode();ctype='application/json'
  h['Content-Type']=ctype
 return op.open(urllib.request.Request(url,headers=h,data=data),timeout=25)
def login(username,key):
 op,jar,redirect=opener();first=request(op,STAFF+'/api/staff/login');target=first.geturl()
 assert urllib.parse.urlsplit(target).netloc==urllib.parse.urlsplit(AUTH).netloc
 query=urllib.parse.urlsplit(target).query
 api=AUTH+'/api/v3/flows/executor/default-authentication-flow/?'+urllib.parse.urlencode({'query':query})
 response=json.load(request(op,api))
 for _ in range(10):
  component=response.get('component');kind=response.get('type')
  if kind=='redirect' or component=='xak-flow-redirect':final=request(op,urllib.parse.urljoin(AUTH,response['to']));break
  csrf=next((c.value for c in jar if c.name in ['authentik_csrf','csrftoken']),None);headers={'X-CSRFToken':csrf} if csrf else {}
  if component=='ak-stage-identification':data={'uid_field':username,'password':s[key]}
  elif component=='ak-stage-password':data={'password':s[key]}
  elif component=='ak-stage-consent':data={'consent':True}
  else:raise RuntimeError('Unexpected authentication stage '+str(component))
  response=json.load(request(op,api,data,headers))
 else:raise RuntimeError('Authentication flow did not finish')
 assert final.status==200 and urllib.parse.urlsplit(final.geturl()).path not in ['/staff/login','/login']
 cookie=next((c.value for c in jar if c.name=='pp_staff_session'),None);assert cookie
 assert request(op,STAFF+'/api/staff/cms/pets').status==200
 return op,jar,redirect,cookie
def denied(url,cookie=None,expected=401,data=None):
 op=urllib.request.build_opener();headers={'Cookie':'pp_staff_session='+cookie} if cookie else {}
 try:request(op,url,data,headers)
 except urllib.error.HTTPError as e:assert e.code==expected;return True
 raise AssertionError('Expected denial')
def logout(op,cookie):
 assert request(op,STAFF+'/api/staff/logout').status==200
 assert denied(STAFF+'/api/staff/cms/pets',cookie)
def probe():
 action=s['action']
 if action=='session-reset-setup':
  admin,_,_,cookie=login(s['admin_username'],'TEST_ADMIN_PASSWORD')
  print(json.dumps({'checks':{'active_staff_and_idp_login_before_session_reset':True},'continuation':{'cookie':cookie}}));return
 if action=='after-session-reset':
  assert denied(STAFF+'/api/staff/cms/pets',s['continuation']['cookie'])
  admin,_,_,cookie=login(s['admin_username'],'TEST_ADMIN_PASSWORD');logout(admin,cookie)
  print(json.dumps({'checks':{'pre_reset_staff_session_denied':True,'fresh_oidc_login_after_session_reset':True,'fresh_logout_revokes_session':True}}));return
 if action=='initial':
  try:
   with socket.create_connection(('1.1.1.1',443),timeout=2):pass
  except OSError:checks['external_egress_denied']=True
  else:raise AssertionError('Internal network permitted external egress')
  public=urllib.request.build_opener()
  discovery=json.load(request(public,s['issuer']+'.well-known/openid-configuration'));assert discovery['issuer']==s['issuer']
  jwks=json.load(request(public,discovery['jwks_uri']));assert jwks==s['reference_jwks']
  checks.update(restored_discovery=True,restored_signing_key_matches_reference=True)
  assert request(public,NETBIRD+'/api/instance').status==200
  nb=json.load(request(public,NETBIRD+'/oauth2/.well-known/openid-configuration'));assert nb['issuer']==NETBIRD+'/oauth2'
  assert json.load(request(public,nb['jwks_uri']))['keys'];checks['netbird_api_discovery_and_jwks']=True
  assert request(public,NETBIRD+'/').status==200;checks['netbird_dashboard']=True
  checks['unauthenticated_staff_denied']=denied(STAFF+'/api/staff/cms/pets')
  reader,_,_,reader_cookie=login(s['reader_username'],'TEST_READER_PASSWORD')
  checks['restored_reader_login_and_read']=True
  checks['reader_write_denied']=denied(STAFF+'/api/staff/cms/pets',reader_cookie,403,{'name':'denied'})
  logout(reader,reader_cookie);checks['reader_logout_revoked_session']=True
  admin,_,redirect,cookie=login(s['admin_username'],'TEST_ADMIN_PASSWORD');checks['restored_admin_login_and_read']=True
  existing=json.load(request(admin,STAFF+'/api/staff/cms/pets'));assert existing['data']==[];checks['empty_snapshot_content_preserved']=True
  def chunk(t,data):return struct.pack('!I',len(data))+t+data+struct.pack('!I',zlib.crc32(t+data)&0xffffffff)
  png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x20\x80\x40'*2)*2))+chunk(b'IEND',b'')
  boundary='restore-drill-boundary';body=(f'--{boundary}\r\nContent-Disposition: form-data; name="alt"\r\n\r\nRecovery drill fixture\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+png+f'\r\n--{boundary}--\r\n'.encode())
  response=request(admin,STAFF+'/api/staff/media',body,ctype='multipart/form-data; boundary='+boundary);assert response.status==201;media=json.load(response)
  private_preview=request(admin,STAFF+'/api/staff/media/'+media['id']).read();assert private_preview;checks['admin_private_media_preview']=True
  reader,_,_,reader_cookie=login(s['reader_username'],'TEST_READER_PASSWORD')
  assert request(reader,STAFF+'/api/staff/media/'+media['id']).read()==private_preview;checks['reader_private_media_preview']=True
  try:request(reader,STAFF+'/api/staff/media',body,ctype='multipart/form-data; boundary='+boundary)
  except urllib.error.HTTPError as e:assert e.code==403
  else:raise AssertionError('Reader media upload was permitted')
  checks['reader_media_write_denied']=True;logout(reader,reader_cookie)
  pet=json.load(request(admin,STAFF+'/api/staff/cms/pets',{'name':'Recovery drill fixture','slug':'restore-drill-fixture','type':'Dog','imageId':media['id'],'status':'published'}))
  blob=request(admin,PUBLIC+media['url']).read();assert blob
  checks.update(admin_media_upload=True,admin_content_write=True)
  continuation={'cookie':cookie,'media_url':media['url'],'media_sha256':hashlib.sha256(blob).hexdigest(),'pet_id':pet['id']}
  print(json.dumps({'checks':checks,'continuation':continuation}));return
 state=s['continuation'];cookie=state['cookie'];op=urllib.request.build_opener();headers={'Cookie':'pp_staff_session='+cookie}
 if action=='after-restart':
  pets=json.load(request(op,STAFF+'/api/staff/cms/pets',headers=headers))['data'];assert any(p['id']==state['pet_id'] for p in pets)
  assert hashlib.sha256(request(op,PUBLIC+state['media_url'],headers=headers).read()).hexdigest()==state['media_sha256']
  checks.update(identity_aware_session_survives_app_restart=True,content_survives_app_restart=True,media_survives_app_restart=True)
  media_id=state['media_url'].rsplit('/',1)[1]
  assert hashlib.sha256(request(op,STAFF+'/api/staff/media/'+media_id,headers=headers).read()).hexdigest()==state['media_sha256'];checks['private_preview_survives_app_restart']=True
 elif action=='after-rollback':
  checks['post_snapshot_session_absent_after_database_rollback']=denied(STAFF+'/api/staff/cms/pets',cookie)
  admin,_,_,cookie=login(s['admin_username'],'TEST_ADMIN_PASSWORD')
  assert json.load(request(admin,STAFF+'/api/staff/cms/pets'))['data']==[]
  checks['snapshot_content_restored_after_rollback']=True
  try:request(admin,PUBLIC+state['media_url'])
  except urllib.error.HTTPError as e:assert e.code==404
  else:raise AssertionError('Post-snapshot fixture media survived rollback')
  checks['snapshot_media_restored_after_rollback']=True
  logout(admin,cookie);checks.update(login_after_rollback=True,logout_revoked_session_after_rollback=True)
 else:raise ValueError('Unknown action')
 print(json.dumps({'checks':checks}))
try:probe()
except Exception as e:
 print(json.dumps({'failed':True,'error_type':type(e).__name__,'http_status':e.code if isinstance(e,urllib.error.HTTPError) else None,'http_path':urllib.parse.urlsplit(e.url).path if isinstance(e,urllib.error.HTTPError) else None,'checks':checks}));sys.exit(1)
