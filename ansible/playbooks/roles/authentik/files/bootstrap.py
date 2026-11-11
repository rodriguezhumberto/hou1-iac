#!/usr/bin/env python3
"""Idempotently apply the environment-specific Authentik identity contract."""
import argparse
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request


class AuthentikAPI:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip('/') + '/api/v3'
        self.token = token
        self.context = ssl.create_default_context()

    def request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, method=method, data=body, headers={
            'Authorization': f'Bearer {self.token}', 'Accept': 'application/json', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, context=self.context, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    def find(self, resource, field, value):
        params = {field: value}
        if resource == 'core/applications':
            params['superuser_full_list'] = 'true'
        query = urllib.parse.urlencode(params)
        data = self.request('GET', f'/{resource}/?{query}')
        results = data.get('results', [])
        matches = [item for item in results if item.get(field) == value]
        if len(matches) > 1:
            raise ValueError('multiple exact matches for managed identity')
        return matches[0] if matches else None

    def ensure(self, resource, field, value, payload):
        existing = self.find(resource, field, value)
        if existing:
            patch = {key: val for key, val in payload.items() if existing.get(key) != val}
            if patch:
                # 2025.10.4 validates source updates using attrs['provider_type'],
                # including PATCHes of its write-only consumer secret.
                if resource == 'sources/oauth':
                    patch['provider_type'] = payload['provider_type']
                lookup = existing['slug'] if resource in ('core/applications', 'sources/oauth') else existing['pk']
                self.request('PATCH', f'/{resource}/{lookup}/', patch)
            return existing['pk']
        return self.request('POST', f'/{resource}/', payload)['pk']


def validate_manifest(manifest, environment):
    if environment not in ('test', 'prod'):
        raise ValueError('Select an explicit test or prod environment')
    if manifest.get('environment') != environment:
        raise ValueError('manifest environment does not match deployment environment')
    if manifest.get('staff', {}).get('audience') != manifest['staff'].get('client_id'):
        raise ValueError('staff audience must equal its isolated client ID')
    if manifest.get('netbird', {}).get('audience') != manifest['netbird'].get('client_id'):
        raise ValueError('NetBird audience must equal its isolated client ID')
    if any(not client['issuer_host'].startswith('auth-test.' if environment == 'test' else 'auth.') for client in [manifest['staff'], manifest['netbird']]):
        raise ValueError('test and production issuer hosts must remain isolated')
    staff_id = 'pearland-paws-staff-test' if environment == 'test' else 'pearland-paws-staff'
    netbird_id = 'netbird-test' if environment == 'test' else 'netbird-prod'
    for name, expected in [('staff', staff_id), ('netbird', netbird_id)]:
        client = manifest[name]
        if any(client.get(key) != expected for key in ('client_id', 'provider_name', 'application_slug')):
            raise ValueError('Client identity must match the selected environment')
    issuer_host = manifest['staff']['issuer_host']
    if manifest['netbird']['issuer_host'] != issuer_host:
        raise ValueError('Clients must use the same environment issuer host')
    suffix = issuer_host.removeprefix('auth-test.' if environment == 'test' else 'auth.')
    if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)+', suffix):
        raise ValueError('Issuer must be an environment-specific DNS hostname')
    staff_host = ('staff-test.' if environment == 'test' else 'staff.') + suffix
    netbird_host = ('netbird-test.' if environment == 'test' else 'netbird.') + suffix
    expected_uris = {
        'staff': ([f'https://{staff_host}/api/staff/oidc/callback'], [f'https://{staff_host}/staff/login']),
        'netbird': ([f'https://{netbird_host}/oauth2/callback', f'https://{netbird_host}/nb-auth',
                     f'https://{netbird_host}/nb-silent-auth', 'http://localhost:53000/'],
                    [f'https://{netbird_host}/nb-auth']),
    }
    for name, (redirects, logouts) in expected_uris.items():
        client = manifest[name]
        if sorted(client['redirect_uris']) != sorted(redirects) or client['logout_uris'] != logouts:
            raise ValueError('Redirect and logout URIs must match the environment client contract')
    google = manifest.get('google')
    if google is not None:
        emails = google.get('allowed_emails', [])
        if (not isinstance(emails, list) or not emails
                or any(not isinstance(email, str) or email != email.lower()
                       or email.count('@') != 1 or '*' in email
                       or any(c.isspace() for c in email) for email in emails)):
            raise ValueError('Google requires an explicit lowercase account allowlist')
        if not google.get('client_id', '').endswith('.apps.googleusercontent.com'):
            raise ValueError('Google requires its isolated OAuth client ID')


def apply_google(google, api, secret):
    """Gate both source flows before enabling Google or exposing its login button."""
    flows = []
    for slug in ('default-source-authentication', 'default-source-enrollment'):
        flow = api.find('flows/instances', 'slug', slug)
        if not flow:
            raise ValueError('Default source flows must exist before Google bootstrap')
        flows.append(flow)
    identification = api.find('stages/identification', 'name', 'default-authentication-identification')
    user_write = api.find('stages/user_write', 'name', 'default-source-enrollment-write')
    if not identification or not user_write:
        raise ValueError('Default identification and source enrollment stages must exist')
    if not secret:
        raise ValueError('Google OAuth client secret is required')
    expression = (
        'userinfo = context.get("oauth_userinfo", {})\n'
        'verified = userinfo.get("email_verified", userinfo.get("verified_email", False))\n'
        f'return verified is True and userinfo.get("email", "").lower() in {sorted(set(google["allowed_emails"]))!r}'
    )
    policy = api.ensure('policies/expression', 'name', 'Google approved accounts', {
        'name': 'Google approved accounts', 'expression': expression})
    for flow in flows:
        if flow.get('policy_engine_mode') != 'all':
            api.request('PATCH', f'/flows/instances/{flow["slug"]}/', {'policy_engine_mode': 'all'})
        # The list filter expects the inherited PolicyBindingModel UUID, while
        # serialized targets and writes use the flow UUID. Match returned
        # targets locally rather than submitting a flow UUID to that filter.
        bindings = api.request('GET', '/policies/bindings/?page_size=1000')
        if bindings.get('next'):
            raise ValueError('Refusing partially paginated source policy bindings')
        matches = [b for b in bindings.get('results', []) if b['target'] == flow['pk'] and b.get('policy') == policy]
        if len(matches) > 1:
            raise ValueError('Duplicate Google source policy binding')
        payload = {'target': flow['pk'], 'policy': policy, 'order': 0,
                   'enabled': True, 'negate': False, 'failure_result': False}
        if not matches:
            api.request('POST', '/policies/bindings/', payload)
        elif any(matches[0].get(k) != v for k, v in payload.items()):
            api.request('PATCH', f'/policies/bindings/{matches[0]["pk"]}/', payload)
    # Internal is Authentik's normal application-user type, not superuser status.
    # Roles remain explicit and are reconciled separately after first login.
    if user_write.get('user_type') != 'internal':
        api.request('PATCH', f'/stages/user_write/{user_write["pk"]}/', {'user_type': 'internal'})
    source = api.ensure('sources/oauth', 'slug', 'google', {
        'name': 'Google', 'slug': 'google', 'enabled': True, 'provider_type': 'google',
        'consumer_key': google['client_id'], 'consumer_secret': secret,
        'authentication_flow': flows[0]['pk'], 'enrollment_flow': flows[1]['pk'],
        'policy_engine_mode': 'all', 'user_matching_mode': 'email_link',
        'user_path_template': 'goauthentik.io/sources/%(slug)s',
        'additional_scopes': 'email openid profile', 'pkce': 'S256'})
    sources = sorted(set(identification.get('sources', [])) | {source})
    if sorted(identification.get('sources', [])) != sources:
        api.request('PATCH', f'/stages/identification/{identification["pk"]}/', {'sources': sources})


def wait_for_defaults(manifest, api, timeout=180, poll_interval=2):
    """Health can pass before the worker finishes installing default objects."""
    required = [
        ('flows/instances', 'slug', 'default-provider-authorization-implicit-consent'),
        ('flows/instances', 'slug', 'default-provider-invalidation-flow'),
        ('crypto/certificatekeypairs', 'name', 'authentik Self-signed Certificate'),
    ]
    managed_scopes = {mapping['scope_name'] for mapping in manifest['scope_mappings']}
    required += [('propertymappings/provider/scope', 'scope_name', scope)
                 for scope in ('openid', 'profile', 'email') if scope not in managed_scopes]
    if manifest.get('google'):
        required += [
            ('flows/instances', 'slug', 'default-source-authentication'),
            ('flows/instances', 'slug', 'default-source-enrollment'),
            ('stages/identification', 'name', 'default-authentication-identification'),
            ('stages/user_write', 'name', 'default-source-enrollment-write'),
        ]
    deadline = time.monotonic() + timeout
    while True:
        # Only missing defaults are polled. Authentication/API errors and
        # duplicate identities still fail immediately; writes are never retried.
        missing = [value for resource, field, value in required
                   if api.find(resource, field, value) is None]
        if not missing:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Authentik defaults were not ready before bootstrap: ' + ', '.join(missing))
        time.sleep(min(poll_interval, remaining))


def apply(manifest, api, secrets):
    wait_for_defaults(manifest, api)
    groups = {}
    for group in manifest['groups']:
        groups[group['name']] = api.ensure('core/groups', 'name', group['name'], {'name': group['name'], 'attributes': group.get('attributes', {})})

    users = {}
    for user in manifest['users']:
        users[user['username']] = api.ensure('core/users', 'username', user['username'], {
            'username': user['username'], 'email': user.get('email', ''), 'name': user.get('name', user['username']), 'is_active': True})

    # Group membership is additive and only touches users named in this manifest.
    for user in manifest['users']:
        for group_name in user.get('groups', []):
            api.request('POST', f'/core/groups/{groups[group_name]}/add_user/', {'pk': users[user['username']]})

    mappings = {}
    for mapping in manifest['scope_mappings']:
        mappings[mapping['scope_name']] = api.ensure('propertymappings/provider/scope', 'name', mapping['name'], mapping)

    authorization = api.find('flows/instances', 'slug', 'default-provider-authorization-implicit-consent')
    invalidation = api.find('flows/instances', 'slug', 'default-provider-invalidation-flow')
    signing = api.find('crypto/certificatekeypairs', 'name', 'authentik Self-signed Certificate')
    if not authorization or not invalidation or not signing:
        raise ValueError('the default provider flows and signing certificate must be ready')
    standard_mappings = [api.find('propertymappings/provider/scope', 'scope_name', scope)['pk'] for scope in ['openid', 'profile', 'email'] if scope not in mappings]
    clients = {}
    for client_name in ('staff', 'netbird'):
        client = manifest[client_name]
        clients[client_name] = api.ensure('providers/oauth2', 'name', client['provider_name'], {
            'name': client['provider_name'], 'client_id': client['client_id'],
            'client_secret': secrets[client['secret_env']], 'client_type': 'confidential',
            'authorization_flow': authorization['pk'], 'invalidation_flow': invalidation['pk'],
            'signing_key': signing['pk'], 'issuer_mode': 'per_provider',
            'access_token_validity': 'minutes=5',
            'redirect_uris': [{'matching_mode': 'strict', 'url': uri} for uri in client['redirect_uris']],
            'logout_uri': client['logout_uris'][0],
            'property_mappings': standard_mappings + [mappings[scope] for scope in client['scopes']],
            'include_claims_in_id_token': True,
        })
        api.ensure('core/applications', 'slug', client['application_slug'], {
            'name': client['application_name'], 'slug': client['application_slug'], 'provider': clients[client_name]})
    if manifest.get('google'):
        apply_google(manifest['google'], api, secrets.get('GOOGLE_OAUTH_CLIENT_SECRET'))
    return {'groups': len(groups), 'users': len(users), 'providers': len(clients)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--token', default=os.environ.get('AUTHENTIK_BOOTSTRAP_TOKEN'))
    parser.add_argument('--environment', required=True, choices=('test', 'prod'))
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args()
    if not args.token:
        raise SystemExit('AUTHENTIK_BOOTSTRAP_TOKEN is required')
    with open(args.manifest, encoding='utf-8') as stream:
        manifest = json.load(stream)
    validate_manifest(manifest, args.environment)
    secrets = {key: os.environ[key] for key in ('STAFF_OIDC_CLIENT_SECRET', 'NETBIRD_OIDC_CLIENT_SECRET')}
    if manifest.get('google'):
        secrets['GOOGLE_OAUTH_CLIENT_SECRET'] = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    print(json.dumps(apply(manifest, AuthentikAPI(args.base_url, args.token), secrets), sort_keys=True))


if __name__ == '__main__':
    main()
