"""Exercise the managed Grafana client lifecycle without changing other identities."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit,parse_qs
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('grafana_identity',ROOT/'ansible/playbooks/roles/grafana_identity/files/provision.py')
identity=importlib.util.module_from_spec(spec);spec.loader.exec_module(identity)


def config():
    return dict(environment='test',issuer_origin='https://auth-test.example.invalid',hostname='monitor-test.example.invalid',
        application_slug='monitoring-test',application_name='Monitoring test',client_id='monitoring-client-test',
        client_secret='fixture-client-secret-'+'x'*48,role_groups=dict(viewer='monitor-viewers',editor='monitor-editors',admin='monitor-admins'),
        seed_members=dict(viewer=[],editor=[],admin=['owner@example.invalid']),signing_key_name='Signing key')


class API(identity.API):
    def __init__(self):
        self.writes=[]
        self.changed=False
        self.objects={
            'flows/instances':[dict(pk='authorize',slug='default-provider-authorization-implicit-consent'),dict(pk='invalidate',slug='default-provider-invalidation-flow')],
            'crypto/certificatekeypairs':[dict(pk='key',name='Signing key')],
            'propertymappings/provider/scope':[dict(pk=v,scope_name=v,name=v,
                managed='goauthentik.io/providers/oauth2/scope-'+v) for v in ['openid','profile','email','offline_access']],
            'core/users':[dict(pk=7,username='owner',email='owner@example.invalid',is_active=True,path='goauthentik.io/sources/google',groups=['unrelated'])],
            'core/groups':[dict(pk='unrelated',name='unrelated',attributes={'keep':True})],
            'core/applications':[dict(pk='staff-app',slug='staff',name='Staff',provider='staff-provider')],
            'providers/oauth2':[dict(pk='staff-provider',name='staff-client',client_id='staff-client',client_secret='unchanged')],
        }
    def request(self,method,path,body=None):
        parsed=urlsplit(path);parts=parsed.path.strip('/').split('/')
        if method=='GET' and parts[-1]=='preview_user':
            provider=next(p for p in self.objects['providers/oauth2'] if str(p['pk'])==parts[-2])
            app=next(a for a in self.objects['core/applications'] if a.get('provider')==provider['pk'])
            subject=next(u for u in self.objects['core/users'] if str(u['pk'])==parse_qs(parsed.query)['for_user'][0])
            names=[g['name'] for g in self.objects['core/groups'] if g['pk'] in subject['groups']]
            class Groups:
                def __init__(self,names):self.names=names
                def filter(self,**kwargs):return Groups([name for name in self.names if name in kwargs['name__in']])
                def values_list(self,*args,**kwargs):return self.names
            user=SimpleNamespace(name='Fixture owner',username=subject['username'],email=subject['email'],ak_groups=Groups(names))
            actor=SimpleNamespace(user=SimpleNamespace(name='Request actor',username='request-actor',ak_groups=Groups([])))
            claims=dict(iss=config()['issuer_origin']+'/application/o/'+app['slug']+'/',
                        aud=provider['client_id'],email_verified=True)
            for mapping in self.objects['propertymappings/provider/scope']:
                if mapping['pk'] in provider['property_mappings'] and mapping.get('expression'):
                    scope={};exec('def check(user,request):\n    '+mapping['expression'],scope)
                    claims.update(scope['check'](user,actor))
            return {'preview':{**claims,**getattr(self,'preview_override',{})}}
        if method=='GET':return {'results':self.objects.get('/'.join(parts),[]),'pagination':{'next':None}}
        self.writes.append((method,path,copy.deepcopy(body)))
        self.changed=True
        if parts[-1]=='add_user':
            user=next(u for u in self.objects['core/users'] if u['pk']==body['pk']);user['groups'].append(parts[-2]);return {}
        if method=='PATCH':
            resource='/'.join(parts[:-1]);key='slug' if resource=='core/applications' else 'pk'
            obj=next(o for o in self.objects[resource] if str(o[key])==parts[-1]);obj.update(body);return obj
        resource='/'.join(parts);obj={'pk':'created-'+str(len(self.writes)),**body}
        self.objects.setdefault(resource,[]).append(obj);return obj


class GrafanaIdentityTest(unittest.TestCase):
    def test_committed_identity_contract_encrypts_all_values_only_to_the_owner(self):
        rules=yaml.safe_load((ROOT/'.sops.yaml').read_text())['creation_rules']
        owner=next(rule['age'] for rule in rules if 'grafana-identity-' in rule['path_regex'])
        bundle=json.loads((ROOT/'ansible/secrets/grafana-identity-prod.enc.json').read_text())
        self.assertEqual([r['recipient'] for r in bundle.pop('sops')['age']],[owner])
        def check(value):
            if isinstance(value,dict):
                for child in value.values():check(child)
            elif isinstance(value,list):
                for child in value:check(child)
            else:self.assertTrue(isinstance(value,str) and value.startswith('ENC[AES256_GCM,'))
        check(bundle)

    def test_provider_is_gated_before_attachment_and_reapply_is_idempotent(self):
        c=config();api=API();before=copy.deepcopy(api.objects)
        result=identity.apply(c,api)
        self.assertTrue(result['passed'])
        self.assertTrue(result['changed'])
        self.assertEqual(result['verified_claim_previews'],1)
        provider=api.find('providers/oauth2','name',c['client_id'])
        self.assertEqual(provider['client_type'],'confidential')
        self.assertEqual(provider['redirect_uris'],[{'matching_mode':'strict','url':'https://'+c['hostname']+'/login/generic_oauth'}])
        self.assertEqual(provider['access_token_validity'],'minutes=5')
        self.assertTrue(provider['include_claims_in_id_token'])
        gate=next(i for i,w in enumerate(api.writes) if w[1]=='policies/bindings/')
        attach=next(i for i,w in enumerate(api.writes) if w[0]=='PATCH' and w[1].startswith('core/applications/') and w[2].get('provider'))
        self.assertLess(gate,attach)
        provider['property_mappings'].reverse()
        count=len(api.writes);api.changed=False
        self.assertFalse(identity.apply(c,api)['changed'])
        self.assertEqual(len(api.writes),count)
        self.assertEqual(api.objects['core/groups'][0],before['core/groups'][0])
        self.assertEqual(api.objects['core/applications'][0],before['core/applications'][0])
        self.assertEqual(api.objects['providers/oauth2'][0],before['providers/oauth2'][0])
        self.assertIn('unrelated',api.objects['core/users'][0]['groups'])

    def test_invalid_contract_and_unrelated_names_fail_without_writes(self):
        cases=[dict(client_secret='short'),dict(issuer_origin='http://auth.example.invalid'),dict(client_id='bad\nvalue'),
            dict(role_groups={**config()['role_groups'],'admin':"group'"}),dict(seed_members={**config()['seed_members'],'viewer':['owner@example.invalid']})]
        for change in cases:
            api=API()
            with self.subTest(keys=list(change)),self.assertRaises((ValueError,TypeError)):
                identity.apply({**config(),**change},api)
            self.assertFalse(api.writes)
        for resource,obj in [('core/groups',dict(pk='other',name='monitor-admins',attributes={})),
            ('core/applications',dict(pk='other',slug='monitoring-test',name='Unrelated',provider=None)),
            ('providers/oauth2',dict(pk='other',name='monitoring-client-test',client_id='other-client'))]:
            api=API();api.objects[resource].append(obj)
            with self.subTest(resource=resource),self.assertRaises(ValueError):identity.apply(config(),api)
            self.assertFalse(api.writes)

    def test_inactive_missing_or_non_google_seed_does_not_reactivate_accounts(self):
        for change in [dict(is_active=False),dict(path='users'),dict(email='other@example.invalid')]:
            api=API();api.objects['core/users'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):identity.apply(config(),api)
            self.assertFalse(api.writes)

    def test_application_policy_rejects_missing_groups_and_inactive_users(self):
        c=config();api=API();identity.apply(c,api)
        policy=api.find('policies/expression','name',c['client_id']+'-access')
        group=api.find('core/groups','name',c['role_groups']['admin'])['pk']
        code=compile('def check(request):\n    '+policy['expression'],'<managed-policy>','exec');scope={};exec(code,scope)
        class Groups:
            def __init__(self,values):self.values=values
            def filter(self,**kwargs):return SimpleNamespace(exists=lambda:bool(set(self.values)&set(kwargs['pk__in'])))
        for active,authenticated,members,expected in [(True,True,[group],True),(True,True,[],False),(False,True,[group],False),(True,False,[group],False),(True,True,['unrelated'],False)]:
            user=SimpleNamespace(is_active=active,is_authenticated=authenticated,ak_groups=Groups(members))
            self.assertEqual(scope['check'](SimpleNamespace(user=user)),expected)

    def test_partial_api_pages_are_refused(self):
        api=identity.API()
        api.request=lambda *a: {'results':[],'pagination':{'next':2}}
        with self.assertRaises(ValueError):api.listing('core/users')

    def test_dedicated_profile_preserves_shared_and_staff_mappings_and_emits_no_groups(self):
        api=API()
        api.objects['propertymappings/provider/scope'][1]['expression']='return {"groups": ["unrelated-group"]}'
        shared=copy.deepcopy(api.objects['propertymappings/provider/scope'])
        custom=dict(pk='staff-profile',scope_name='profile',name='Staff profile',managed=None,
                    expression='return {"staff_only": True}')
        api.objects['propertymappings/provider/scope'].append(copy.deepcopy(custom))
        identity.apply(config(),api)
        provider=api.find('providers/oauth2','name',config()['client_id'])
        self.assertNotIn('profile',provider['property_mappings'])
        self.assertNotIn('staff-profile',provider['property_mappings'])
        self.assertEqual(api.find('propertymappings/provider/scope','pk','staff-profile'),custom)
        profile=api.find('propertymappings/provider/scope','name',config()['client_id']+'-profile')
        self.assertIn(profile['pk'],provider['property_mappings'])
        scope={}
        exec('def check(user, request):\n    '+profile['expression'],scope)
        claims=scope['check'](SimpleNamespace(name='Fixture owner',username='fixture-owner'),
                              SimpleNamespace(user=SimpleNamespace(name='Request actor',username='request-actor')))
        self.assertNotIn('groups',claims)
        self.assertEqual(claims['preferred_username'],'fixture-owner')
        for mapping in shared:
            self.assertEqual(api.find('propertymappings/provider/scope','pk',mapping['pk']),mapping)

        api=API()
        api.objects['propertymappings/provider/scope']=[custom]
        with self.assertRaises(ValueError):identity.apply(config(),api)
        self.assertFalse(api.writes)

    def test_scope_mapping_uses_the_token_subject_rather_than_the_http_request_actor(self):
        api=API();c=config();identity.apply(c,api)
        mapping=api.find('propertymappings/provider/scope','name',c['client_id']+'-groups')
        scope={}
        exec('def check(user, request):\n    '+mapping['expression'],scope)
        class Groups:
            def __init__(self,names):self.names=names
            def filter(self,**kwargs):return Groups([name for name in self.names if name in kwargs['name__in']])
            def values_list(self,*args,**kwargs):return self.names
        def user(names):return SimpleNamespace(ak_groups=Groups(names))
        admin=c['role_groups']['admin']
        self.assertEqual(scope['check'](user([admin,'staff-admin']),SimpleNamespace(user=user([]))),{'groups':[admin]})
        self.assertEqual(scope['check'](user([]),SimpleNamespace(user=user([admin]))),{'groups':[]})

    def test_live_claim_preview_must_pass_before_provisioning_reports_success(self):
        for claims in [dict(groups=[]),dict(groups='monitor-admins'),dict(groups=['monitor-admins','unrelated']),
                       dict(iss='https://other.example.invalid/'),dict(aud='other-client'),dict(email='other@example.invalid')]:
            api=API();api.preview_override=claims
            with self.subTest(claims=claims),self.assertRaises(ValueError):identity.apply(config(),api)

    def test_interrupted_attachment_resumes_without_adopting_other_applications(self):
        api=API();request=api.request;failed=[]
        def interrupt(method,path,body=None):
            if method=='PATCH' and path.startswith('core/applications/') and body.get('provider') and not failed:
                failed.append(True);raise RuntimeError('fixture interruption')
            return request(method,path,body)
        api.request=interrupt
        with self.assertRaises(RuntimeError):identity.apply(config(),api)
        self.assertTrue(identity.apply(config(),api)['passed'])
        provider=api.find('providers/oauth2','name',config()['client_id'])
        api.objects['core/applications'][0]['provider']=provider['pk']
        count=len(api.writes)
        with self.assertRaises(ValueError):identity.apply(config(),api)
        self.assertEqual(len(api.writes),count)
