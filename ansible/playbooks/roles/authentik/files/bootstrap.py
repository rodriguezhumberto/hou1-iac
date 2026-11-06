#!/usr/bin/env python3
"""Idempotently apply the environment-specific Authentik identity contract."""
import argparse
import json
import os
import ssl
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
                lookup = existing['slug'] if resource == 'core/applications' else existing['pk']
                self.request('PATCH', f'/{resource}/{lookup}/', patch)
            return existing['pk']
        return self.request('POST', f'/{resource}/', payload)['pk']


def validate_manifest(manifest, environment):
    if manifest.get('environment') != environment:
        raise ValueError('manifest environment does not match deployment environment')
    if manifest.get('staff', {}).get('audience') != manifest['staff'].get('client_id'):
        raise ValueError('staff audience must equal its isolated client ID')
    if manifest.get('netbird', {}).get('audience') != manifest['netbird'].get('client_id'):
        raise ValueError('NetBird audience must equal its isolated client ID')
    if any(not client['issuer_host'].startswith('auth-test.' if environment == 'test' else 'auth.') for client in [manifest['staff'], manifest['netbird']]):
        raise ValueError('test and production issuer hosts must remain isolated')
    for client in [manifest['staff'], manifest['netbird']]:
        if any(not uri.startswith(('https://', 'http://localhost:')) for uri in client['redirect_uris'] + client['logout_uris']):
            raise ValueError('redirect and logout URIs must be HTTPS or the documented CLI localhost URI')


def apply(manifest, api, secrets):
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
    print(json.dumps(apply(manifest, AuthentikAPI(args.base_url, args.token), secrets), sort_keys=True))


if __name__ == '__main__':
    main()
