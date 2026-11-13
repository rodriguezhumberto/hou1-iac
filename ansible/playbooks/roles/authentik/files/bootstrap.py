#!/usr/bin/env python3
"""Idempotently apply the environment-specific Authentik identity contract."""
import argparse
import copy
import json
import ipaddress
import os
import re
import ssl
import time
from pathlib import Path
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


def https_origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname
            or value != 'https://' + parsed.hostname
            or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)+', parsed.hostname)):
        raise ValueError('Expected an exact HTTPS origin without credentials, port or suffix')
    return value


def validate_profile(profile):
    """Validate public inventory inputs without embedding deployment conventions."""
    if profile.get('environment') not in ('test', 'prod'):
        raise ValueError('Select an explicit test or prod environment')
    https_origin('https://' + profile['issuer_host'])
    https_origin(profile['netbird_url'])
    ipaddress.IPv4Network(profile['netbird_subnet'], strict=True)
    for key in ('compose_project', 'compose_service'):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', profile[key]):
            raise ValueError('Expected an explicit Compose project and service')
    compose_file = Path(profile['compose_file'])
    if not compose_file.is_absolute() or '..' in compose_file.parts:
        raise ValueError('Expected an absolute Compose file path')
    for name in ('staff', 'netbird'):
        client = profile[name]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', client['client_id']) or not client['application_name']:
            raise ValueError('Expected a named client identity')
        for key in ('redirect_uris', 'logout_uris'):
            uris = client[key]
            if not isinstance(uris, list) or not uris or len(set(uris)) != len(uris):
                raise ValueError('Expected nonempty, unique redirect and logout allowlists')
            for uri in uris:
                # The NetBird CLI loopback callback is a protocol requirement,
                # independent of environment names or deployment hostnames.
                if name == 'netbird' and key == 'redirect_uris' and uri == 'http://localhost:53000/':
                    continue
                parsed = urllib.parse.urlsplit(uri)
                https_origin('https://' + parsed.netloc)
                if parsed.scheme != 'https' or parsed.fragment:
                    raise ValueError('Expected HTTPS redirects and logout URIs')
    if profile['staff']['client_id'] == profile['netbird']['client_id']:
        raise ValueError('Staff and NetBird must have separate client identities')
    return profile


def resolve_profile(public_profile, names):
    """Merge organization identifiers from the environment's SOPS bundle only."""
    if names.get('environment') != public_profile.get('environment'):
        raise ValueError('Identity names belong to a different environment')
    if any(key in public_profile['staff'] for key in ('client_id', 'application_name')):
        raise ValueError('Staff identity names must come from SOPS, not the public profile')
    if (set(names['staff']) != {'client_id', 'application_name'}
            or set(names['role_groups']) != {'reader', 'editor', 'admin'}
            or set(names['scope_mapping_names']) != {'profile', 'groups', 'audience'}):
        raise ValueError('Supply the complete private identity naming contract')
    for key in ('role_groups', 'scope_mapping_names'):
        values = list(names[key].values())
        if (any(not isinstance(value, str) or not value.strip() for value in values)
                or len(set(values)) != len(values)):
            raise ValueError('Private identity names must be nonempty and unique')
    profile = copy.deepcopy(public_profile)
    profile['staff'].update(names['staff'])
    profile['role_groups'] = dict(names['role_groups'])
    profile['scope_mapping_names'] = dict(names['scope_mapping_names'])
    return validate_profile(profile)


def validate_manifest(manifest, environment, profile):
    validate_profile(profile)
    if manifest.get('environment') != environment or profile['environment'] != environment:
        raise ValueError('manifest environment does not match deployment environment')
    for name in ('staff', 'netbird'):
        client, expected = manifest[name], profile[name]
        if client.get('audience') != client.get('client_id'):
            raise ValueError('Client audience must equal its isolated client ID')
        if (client.get('issuer_host') != profile['issuer_host']
                or any(client.get(key) != expected['client_id']
                       for key in ('client_id', 'provider_name', 'application_slug'))
                or client.get('application_name') != expected['application_name']):
            raise ValueError('Client identity must match the selected inventory profile')
        if (client['redirect_uris'] != expected['redirect_uris']
                or client['logout_uris'] != expected['logout_uris']):
            raise ValueError('Redirect and logout URIs must match the environment client contract')
    if sorted(group['name'] for group in manifest['groups']) != sorted(profile['role_groups'].values()):
        raise ValueError('Role groups must match the private identity naming contract')
    mapping_names = {mapping['scope_name']: mapping['name'] for mapping in manifest['scope_mappings']}
    if mapping_names != profile['scope_mapping_names']:
        raise ValueError('Scope mapping names must match the private identity naming contract')
    branding = manifest.get('branding')
    if branding is not None:
        if (type(branding.get('enabled')) is not bool
                or branding.get('domain') != profile['issuer_host']):
            raise ValueError('Branding must select the exact environment identity domain')
        if branding['enabled'] and (not manifest.get('google') or not branding.get('css')):
            raise ValueError('Branding requires configured Google sign-in and its stylesheet')
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
    # Google can return an existing connection while the browser already has
    # an Authentik session (for example, after an application logout). Permit
    # that callback only after both verified-email admission gates are ready.
    if flows[0].get('authentication') != 'none':
        api.request('PATCH', f'/flows/instances/{flows[0]["slug"]}/', {'authentication': 'none'})
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


BRANDING_FIELDS = ('branding_title', 'branding_logo', 'branding_favicon', 'branding_custom_css')
BRAND_INHERITED_FIELDS = (
    *BRANDING_FIELDS, 'branding_default_flow_background', 'flow_authentication',
    'flow_invalidation', 'flow_recovery', 'flow_unenrollment', 'flow_user_settings',
    'flow_device_code', 'default_application', 'web_certificate', 'client_certificates',
)


def apply_branding(branding, environment, api):
    """Reconcile only the selected domain, retaining prior presentation for rollback."""
    existing = api.find('core/brands', 'domain', branding['domain'])
    if not branding['enabled'] and not existing:
        return
    base = existing or api.find('core/brands', 'default', True)
    if not base:
        raise ValueError('The default Brand must exist before creating a domain Brand')
    attributes = copy.deepcopy(base.get('attributes', {}))
    managed = attributes.get('pearland_paws_login')
    if managed is not None and (not isinstance(managed, dict) or 'previous' not in managed):
        raise ValueError('Refusing to overwrite an unrecognized login branding marker')
    if not branding['enabled'] and not managed:
        return
    settings = attributes.setdefault('settings', {})
    theme = settings.setdefault('theme', {})
    if branding['enabled']:
        google = api.find('sources/oauth', 'slug', 'google')
        if not google or not google.get('enabled') or google.get('provider_type') != 'google':
            raise ValueError('Google must be ready before enabling login branding')
        identification = api.find('stages/identification', 'name', 'default-authentication-identification')
        if (not identification or not identification.get('user_fields')
                or google['pk'] not in identification.get('sources', [])):
            raise ValueError('Branding requires the existing local identifier fields and Google source')
        previous = managed['previous'] if managed else {
            'fields': {key: base[key] for key in BRANDING_FIELDS},
            'theme_base': theme.get('base'),
        }
        attributes['pearland_paws_login'] = {'environment': environment, 'previous': previous}
        theme['base'] = 'light'
        payload = {
            'branding_title': 'Pearland Paws',
            'branding_logo': '/media/branding/logo-v1.png',
            'branding_favicon': '/media/branding/logo-v1.png',
            'branding_custom_css': branding['css'],
        }
    else:
        previous = attributes.pop('pearland_paws_login')['previous']
        payload = previous['fields']
        if previous['theme_base'] is None:
            theme.pop('base', None)
        else:
            theme['base'] = previous['theme_base']
        if not theme:
            settings.pop('theme', None)
        if not settings:
            attributes.pop('settings', None)
    payload['attributes'] = attributes
    if existing:
        patch = {key: value for key, value in payload.items() if existing.get(key) != value}
        if patch:
            api.request('PATCH', f'/core/brands/{existing["brand_uuid"]}/', patch)
    else:
        # Preserve the catch-all Brand's flow selections and unrelated settings.
        inherited = {key: base[key] for key in BRAND_INHERITED_FIELDS if key in base}
        api.request('POST', '/core/brands/', {
            **inherited, **payload, 'domain': branding['domain'], 'default': False,
        })


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
    if manifest.get('branding', {}).get('enabled'):
        required.append(('core/brands', 'default', True))
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
    if manifest.get('branding'):
        apply_branding(manifest['branding'], manifest['environment'], api)
    return {'groups': len(groups), 'users': len(users), 'providers': len(clients)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--token', default=os.environ.get('AUTHENTIK_BOOTSTRAP_TOKEN'))
    parser.add_argument('--environment', required=True, choices=('test', 'prod'))
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--profile', required=True)
    args = parser.parse_args()
    if not args.token:
        raise SystemExit('AUTHENTIK_BOOTSTRAP_TOKEN is required')
    with open(args.manifest, encoding='utf-8') as stream:
        manifest = json.load(stream)
    with open(args.profile, encoding='utf-8') as stream:
        profile = json.load(stream)
    profile = resolve_profile(profile, json.loads(os.environ['AUTHENTIK_IDENTITY_NAMES']))
    validate_manifest(manifest, args.environment, profile)
    secrets = {key: os.environ[key] for key in ('STAFF_OIDC_CLIENT_SECRET', 'NETBIRD_OIDC_CLIENT_SECRET')}
    if manifest.get('google'):
        secrets['GOOGLE_OAUTH_CLIENT_SECRET'] = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    print(json.dumps(apply(manifest, AuthentikAPI(args.base_url, args.token), secrets), sort_keys=True))


if __name__ == '__main__':
    main()
