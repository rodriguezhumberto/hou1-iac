#!/usr/bin/env python3
"""Reconcile one private Grafana client through Authentik's container-local API."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

OWNER = 'grafana-oidc/v1'
ROLES = ('viewer', 'editor', 'admin')


def validate(c):
    required = {'environment', 'issuer_origin', 'hostname', 'application_slug', 'application_name',
                'client_id', 'client_secret', 'role_groups', 'seed_members', 'signing_key_name'}
    if set(c) != required or c['environment'] not in ('test', 'production'):
        raise ValueError('Require one complete environment-bound client')
    for host in (c['hostname'], urllib.parse.urlsplit(c['issuer_origin']).hostname or ''):
        if not isinstance(host, str) or not re.fullmatch(r'(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,63}', host):
            raise ValueError('Require canonical private hostnames')
    if c['issuer_origin'] != 'https://' + urllib.parse.urlsplit(c['issuer_origin']).hostname:
        raise ValueError('Require an exact HTTPS issuer origin')
    if c['issuer_origin'] == 'https://' + c['hostname']:
        raise ValueError('The provider and application must have separate origins')
    for key in ('application_slug', 'client_id'):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,100}', c[key]):
            raise ValueError('Require explicit application and client identifiers')
    for key in ('application_name', 'signing_key_name'):
        if not isinstance(c[key], str) or not c[key].strip() or re.search(r'[\x00-\x1f\x7f]', c[key]):
            raise ValueError('Require private display and signing-key names')
    if not isinstance(c['client_secret'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{48,200}', c['client_secret']):
        raise ValueError('Require a separate protected client secret')
    if set(c['role_groups']) != set(ROLES) or set(c['seed_members']) != set(ROLES):
        raise ValueError('Declare the exact monitoring role contract')
    names = list(c['role_groups'].values())
    if len(set(names)) != 3 or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{1,100}', n) for n in names):
        raise ValueError('Monitoring groups must be distinct literal identifiers')
    members = []
    for role in ROLES:
        if not isinstance(c['seed_members'][role], list):
            raise ValueError('Seed membership must be explicit lists')
        members += c['seed_members'][role]
    if len(set(members)) != len(members) or any(not isinstance(e, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+', e) or e != e.lower() for e in members):
        raise ValueError('Seed members must be unique lowercase accounts')
    return c


class API:
    changed = False
    issuer_origin = ''

    def request(self, method, path, body=None):
        headers = {'Authorization': 'Bearer ' + os.environ['AUTHENTIK_BOOTSTRAP_TOKEN'],
                   'Content-Type': 'application/json'}
        if self.issuer_origin:
            headers.update({'Host': urllib.parse.urlsplit(self.issuer_origin).netloc,
                            'X-Forwarded-Proto': 'https'})
        request = urllib.request.Request('http://127.0.0.1:9000/api/v3/' + path.lstrip('/'),
            method=method, data=None if body is None else json.dumps(body).encode(),
            headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
        if method != 'GET':
            self.changed = True
        return json.loads(raw) if raw else {}

    def listing(self, resource, **query):
        data = self.request('GET', resource + '/?' + urllib.parse.urlencode({'page_size': 1000, **query}))
        if data.get('next') or data.get('pagination', {}).get('next'):
            raise ValueError('Refuse incomplete identity input')
        return data['results']

    def find(self, resource, field, value):
        query = {field: value}
        if resource == 'core/applications':
            query['superuser_full_list'] = 'true'
        matches = [item for item in self.listing(resource, **query) if item.get(field) == value]
        if len(matches) > 1:
            raise ValueError('Duplicate managed identity')
        return matches[0] if matches else None

    def ensure(self, resource, field, value, payload, existing=None):
        current = existing if existing is not None else self.find(resource, field, value)
        if current:
            # Authentik serializes this many-to-many relation in server order.
            changes = {k: v for k, v in payload.items()
                       if (set(current.get(k, [])) != set(v) if k == 'property_mappings'
                           else current.get(k) != v)}
            if changes:
                lookup = current['slug'] if resource == 'core/applications' else current['pk']
                self.request('PATCH', f'{resource}/{lookup}/', changes)
            return {**current, **payload}
        return self.request('POST', resource + '/', payload)


def apply(c, api):
    validate(c)
    api.issuer_origin = c['issuer_origin']
    # Resolve the entire contract and every seeded identity before changing anything.
    existing = api.find('core/applications', 'slug', c['application_slug'])
    provider = api.find('providers/oauth2', 'name', c['client_id'])
    if existing and existing.get('meta_description') != OWNER:
        raise ValueError('Refuse an unrelated application')
    if provider and (provider['client_id'] != c['client_id'] or not existing
                     or existing.get('provider') not in (None, provider['pk'])):
        raise ValueError('Refuse an unrelated provider')
    if existing and existing.get('provider') and not provider:
        raise ValueError('Refuse replacing another provider')
    if provider and any(app.get('provider') == provider['pk'] and app['slug'] != c['application_slug']
                        for app in api.listing('core/applications', superuser_full_list='true')):
        raise ValueError('The provider belongs to another application')
    group_state = {}
    for role, name in c['role_groups'].items():
        group = api.find('core/groups', 'name', name)
        if group and group.get('attributes', {}).get('managed_by') != OWNER:
            raise ValueError('Refuse an unrelated group')
        group_state[role] = group
    users = {}
    for role, emails in c['seed_members'].items():
        for email in emails:
            matches = [u for u in api.listing('core/users', email=email) if u.get('email', '').lower() == email]
            if len(matches) != 1 or not matches[0]['is_active'] or matches[0].get('path') != 'goauthentik.io/sources/google':
                raise ValueError('Seed only one existing active approved Google identity')
            users[email] = matches[0]
    authorization = api.find('flows/instances', 'slug', 'default-provider-authorization-implicit-consent')
    invalidation = api.find('flows/instances', 'slug', 'default-provider-invalidation-flow')
    signing = api.find('crypto/certificatekeypairs', 'name', c['signing_key_name'])
    standard = [api.find('propertymappings/provider/scope', 'managed',
                        'goauthentik.io/providers/oauth2/scope-' + scope)
                for scope in ('openid', 'offline_access')]
    if not authorization or not invalidation or not signing or not all(standard):
        raise ValueError('Require existing provider flows, signing key and standard scopes')
    groups = {}
    for role, name in c['role_groups'].items():
        current = group_state[role]
        attributes = {**(current or {}).get('attributes', {}), 'managed_by': OWNER}
        groups[role] = api.ensure('core/groups', 'name', name, {'name': name, 'attributes': attributes}, current)
        for email in c['seed_members'][role]:
            if groups[role]['pk'] not in users[email].get('groups', []):
                api.request('POST', f'core/groups/{groups[role]["pk"]}/add_user/', {'pk': users[email]['pk']})
    names = list(c['role_groups'].values())
    # The built-in profile mapping includes all groups. Keep this client's
    # profile minimal so only its dedicated groups mapping emits memberships.
    profile = api.ensure('propertymappings/provider/scope', 'name', c['client_id'] + '-profile', {
        'name': c['client_id'] + '-profile', 'scope_name': 'profile',
        'expression': 'return {"name": user.name, "preferred_username": user.username, "nickname": user.username}'})
    email_mapping = api.ensure('propertymappings/provider/scope', 'name', c['client_id'] + '-email', {
        'name': c['client_id'] + '-email', 'scope_name': 'email',
        'expression': 'return {"email": user.email}'})
    mapping = api.ensure('propertymappings/provider/scope', 'name', c['client_id'] + '-groups', {
        'name': c['client_id'] + '-groups', 'scope_name': 'groups',
        'expression': 'return {"groups": list(user.ak_groups.filter(name__in=' + repr(names) + ').values_list("name", flat=True))}'})
    expression = ('return request.user.is_authenticated and request.user.is_active and '
                  'request.user.ak_groups.filter(pk__in=' + repr([g['pk'] for g in groups.values()]) + ').exists()')
    policy = api.ensure('policies/expression', 'name', c['client_id'] + '-access',
                        {'name': c['client_id'] + '-access', 'expression': expression})
    payload = {'name': c['application_name'], 'slug': c['application_slug'], 'meta_description': OWNER,
               'policy_engine_mode': 'all', 'open_in_new_tab': False}
    if not existing:
        payload['provider'] = None
    app = api.ensure('core/applications', 'slug', c['application_slug'], payload, existing)
    bindings = api.listing('policies/bindings')
    bindings = [b for b in bindings if b['target'] == app['pk'] and b.get('policy') == policy['pk']]
    if len(bindings) > 1:
        raise ValueError('Duplicate managed application policy')
    binding = {'target': app['pk'], 'policy': policy['pk'], 'order': 0, 'enabled': True,
               'negate': False, 'failure_result': False}
    if not bindings:
        api.request('POST', 'policies/bindings/', binding)
    elif any(bindings[0].get(k) != v for k, v in binding.items()):
        api.request('PATCH', f'policies/bindings/{bindings[0]["pk"]}/', binding)
    provider = api.ensure('providers/oauth2', 'name', c['client_id'], {
        'name': c['client_id'], 'client_id': c['client_id'], 'client_secret': c['client_secret'],
        'client_type': 'confidential', 'authorization_flow': authorization['pk'],
        'invalidation_flow': invalidation['pk'], 'signing_key': signing['pk'],
        'issuer_mode': 'per_provider', 'sub_mode': 'hashed_user_id', 'access_code_validity': 'minutes=1',
        'access_token_validity': 'minutes=5', 'refresh_token_validity': 'hours=8',
        'redirect_uris': [{'matching_mode': 'strict', 'url': 'https://' + c['hostname'] + '/login/generic_oauth'}],
        'logout_uri': 'https://' + c['hostname'] + '/login',
        'property_mappings': [m['pk'] for m in standard] + [profile['pk'], email_mapping['pk'], mapping['pk']],
        'include_claims_in_id_token': True,
    }, provider)
    api.ensure('core/applications', 'slug', c['application_slug'], {**payload, 'provider': provider['pk']}, app)
    previews = 0
    for role, emails in c['seed_members'].items():
        for email in emails:
            query = urllib.parse.urlencode({'for_user': users[email]['pk']})
            claims = api.request('GET', f'providers/oauth2/{provider["pk"]}/preview_user/?{query}')['preview']
            memberships = claims.get('groups')
            if (claims.get('iss') != c['issuer_origin'] + '/application/o/' + c['application_slug'] + '/'
                    or claims.get('aud') not in (c['client_id'], [c['client_id']])
                    or claims.get('email', '').lower() != email
                    or not isinstance(memberships, list)
                    or any(not isinstance(name, str) for name in memberships)
                    or not set(memberships).issubset(names)
                    or c['role_groups'][role] not in memberships):
                raise ValueError('The live seeded-user claim preview failed the monitoring contract')
            previews += 1
    return {'passed': True, 'changed': api.changed, 'environment': c['environment'], 'providers': 1, 'groups': 3,
            'seeded_users': len(users), 'verified_claim_previews': previews,
            'unrelated_identities_preserved': True}


if __name__ == '__main__':
    try:
        print(json.dumps(apply(json.load(sys.stdin), API())))
    except Exception:
        sys.exit('Grafana identity reconciliation refused; private diagnostics withheld.')
