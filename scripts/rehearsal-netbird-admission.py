#!/usr/bin/env python3
"""Enforce environment-bound Authentik admission before disabling NetBird approvals."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request

AUTH_REQUEST = r'''
import json, os, sys, urllib.request
r = json.load(sys.stdin)
body = None if r.get('body') is None else json.dumps(r['body']).encode()
req = urllib.request.Request('http://127.0.0.1:9000/api/v3/' + r['path'],
    data=body, method=r['method'], headers={
        'Authorization': 'Bearer ' + os.environ['AUTHENTIK_BOOTSTRAP_TOKEN'],
        'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=20) as response:
    print(response.read().decode() or '{}')
'''


def admission_expression(emails, fixture_ids):
    # Google enrollment already enforces verified email. Limit application
    # admission to those source users and explicit internal rehearsal fixtures.
    return (
        'user = request.user\n'
        'return user.is_authenticated and user.is_active and (\n'
        f'    (user.path == "goauthentik.io/sources/google" and user.email.lower() in {emails!r})\n'
        f'    or (user.type == "internal" and user.pk in {fixture_ids!r})\n'
        ')'
    )


def unique(items, key, value):
    matches = [x for x in items if x.get(key) == value]
    if len(matches) != 1:
        raise ValueError('Expected exactly one configured ' + key)
    return matches[0]


STAFF_ROLES = {role: 'pearland-paws-staff-' + role for role in ('reader', 'editor', 'admin')}


def deployment_profile(config):
    """Bind every destination to one environment before any API call or write."""
    environment = config.get('environment')
    if environment not in ('test', 'prod'):
        raise ValueError('Select an explicit test or prod environment')
    prefix = 'netbird-test.' if environment == 'test' else 'netbird.'
    origin = config.get('netbird_url', '')
    nb = urllib.parse.urlsplit(origin)
    host = nb.hostname or ''
    suffix = host.removeprefix(prefix)
    if (not host.startswith(prefix) or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)+', suffix)
            or origin != 'https://' + host):
        raise ValueError('Expected the exact environment-specific HTTPS NetBird origin')
    client = 'netbird-test' if environment == 'test' else 'netbird-prod'
    auth_host = ('auth-test.' if environment == 'test' else 'auth.') + suffix
    if config.get('authentik_issuer') != f'https://{auth_host}/application/o/{client}/':
        raise ValueError('Issuer must match the environment, DNS suffix and client')
    subnet = '100.91.0.0/16' if environment == 'test' else '100.90.0.0/16'
    if config.get('netbird_subnet') != subnet:
        raise ValueError('Subnet must match the selected environment')
    if environment == 'prod' and config.get('fixture_users'):
        raise ValueError('Production admission cannot include rehearsal fixtures')
    return {'environment': environment, 'client': client,
            'container': f'hou1-authentik-{environment}-authentik-server-1'}


def auth_results(auth, path):
    result = auth('GET', path)
    if result.get('next') or result.get('pagination', {}).get('next'):
        raise ValueError('Refusing partially paginated Authentik inputs')
    return result['results']


def configure_staff_access(assignments, users, provider, auth, netbird):
    """Keep network staff access separate from application privilege."""
    if not assignments:
        return {'staff_users_configured': 0, 'staff_users_awaiting_first_login': 0}
    staff = unique(netbird('GET', '/groups'), 'name', 'staff')
    groups = auth_results(auth, 'core/groups/?page_size=1000')
    role_ids = {role: unique(groups, 'name', name)['pk'] for role, name in STAFF_ROLES.items()}
    peers = netbird('GET', '/peers')
    nb_users = netbird('GET', '/users')
    configured = waiting = 0
    for assignment in assignments:
        matches = [u for u in users if u.get('email') == assignment['email']]
        if not matches:
            waiting += 1
            continue
        user = unique(matches, 'email', assignment['email'])
        if not user['is_active'] or user['path'] != 'goauthentik.io/sources/google':
            raise ValueError('Staff assignment requires an active Google-source identity')
        desired = sorted((set(user.get('groups', [])) - set(role_ids.values())) | {role_ids[assignment['role']]})
        if sorted(user.get('groups', [])) != desired:
            auth('PATCH', 'core/users/' + str(user['pk']) + '/', {'groups': desired})
        actual = auth('GET', 'core/users/' + str(user['pk']) + '/')
        if sorted(actual['groups']) != desired:
            raise ValueError('Authentik staff role readback differs')
        matches = [u for u in nb_users if u.get('email') == assignment['email'] and u.get('idp_id') == provider['id']]
        if not matches:
            waiting += 1
            continue
        nb_user = unique(matches, 'email', assignment['email'])
        if nb_user['role'] != 'user' or nb_user['is_blocked'] or nb_user['pending_approval']:
            raise ValueError('Staff network assignment requires an active regular NetBird user')
        desired = sorted(set(nb_user.get('auto_groups', [])) | {staff['id']})
        if sorted(nb_user.get('auto_groups', [])) != desired:
            netbird('PUT', '/users/' + urllib.parse.quote(nb_user['id'], safe=''), {
                'auto_groups': desired, 'role': nb_user['role'], 'is_blocked': nb_user['is_blocked']})
        actual = unique(netbird('GET', '/users'), 'id', nb_user['id'])
        if sorted(actual['auto_groups']) != desired or actual['role'] != 'user':
            raise ValueError('NetBird staff group readback differs')
        owned_ids = {p['id'] for p in peers if p.get('user_id') == nb_user['id']}
        actual_peers = netbird('GET', '/peers')
        if not all(staff['id'] in [g['id'] for g in p['groups']] for p in actual_peers if p['id'] in owned_ids):
            raise ValueError('Staff group did not propagate to existing devices')
        configured += 1
    return {'staff_users_configured': configured, 'staff_users_awaiting_first_login': waiting}


def configure(config, auth, netbird):
    profile = deployment_profile(config)
    emails = sorted(set(config['google_emails']))
    if not emails or any(e != e.lower() or e.count('@') != 1 or '*' in e or any(c.isspace() for c in e) for e in emails):
        raise ValueError('Supply explicit lowercase Google account addresses')
    assignments = config.get('staff_users', [])
    if (len({u['email'] for u in assignments}) != len(assignments)
            or any(u['email'] not in emails or u['role'] not in STAFF_ROLES for u in assignments)):
        raise ValueError('Staff roles require unique, explicitly allowlisted Google accounts')
    users = auth_results(auth, 'core/users/?page_size=1000')
    if not users:
        raise ValueError('An existing identity is required for live policy checks')
    fixture_ids = []
    for name in config.get('fixture_users', []):
        u = unique(users, 'username', name)
        if u['type'] != 'internal' or not u['email'].endswith('@example.invalid'):
            raise ValueError('Fixtures must be isolated internal test identities')
        fixture_ids.append(u['pk'])
    app_path = 'core/applications/' + profile['client'] + '/'
    app = auth('GET', app_path)
    source = auth('GET', 'sources/oauth/google/')
    if not source['enabled'] or source['provider_type'] != 'google':
        raise ValueError('The environment Google source must be enabled')
    policies = auth_results(auth, 'policies/expression/?page_size=1000')
    source_policy = unique(policies, 'name', 'Google approved accounts')
    bindings = auth_results(auth, 'policies/bindings/?page_size=1000')
    flows = auth_results(auth, 'flows/instances/?page_size=1000')
    for flow in ('authentication_flow', 'enrollment_flow'):
        instance = unique(flows, 'pk', source[flow])
        if instance['policy_engine_mode'] != 'all' or not any(
            b['target'] == source[flow] and b['policy'] == source_policy['pk']
            and b['enabled'] and not b['negate'] and b.get('failure_result') is False for b in bindings
        ):
            raise ValueError('Google allowlist must gate both source flows in ALL mode')
    expression = (
        'userinfo = context.get("oauth_userinfo", {})\n'
        'verified = userinfo.get("email_verified", userinfo.get("verified_email", False))\n'
        f'return verified is True and userinfo.get("email", "").lower() in {emails!r}'
    )
    auth('PATCH', 'policies/expression/' + source_policy['pk'] + '/', {'expression': expression})
    name = 'HOU1 ' + profile['environment'] + ' NetBird allowlist'
    matches = [p for p in policies if p['name'] == name]
    if len(matches) > 1:
        raise ValueError('Duplicate managed admission policy')
    payload = {'name': name, 'expression': admission_expression(emails, fixture_ids)}
    policy = auth('PATCH', 'policies/expression/' + matches[0]['pk'] + '/', payload) if matches else auth('POST', 'policies/expression/', payload)
    payload = {'target': app['pk'], 'policy': policy['pk'], 'order': 0,
               'enabled': True, 'negate': False, 'failure_result': False}
    existing = [b for b in bindings if b['target'] == app['pk'] and b['policy'] == policy['pk']]
    if len(existing) > 1:
        raise ValueError('Duplicate managed admission binding')
    if existing:
        auth('PATCH', 'policies/bindings/' + existing[0]['pk'] + '/', payload)
    else:
        auth('POST', 'policies/bindings/', payload)
    auth('PATCH', app_path, {'policy_engine_mode': 'all'})
    # Test the running policy engine before changing NetBird's admission switch.
    for user in users:
        expected = user['is_active'] and (
            (user['path'] == 'goauthentik.io/sources/google' and user['email'].lower() in emails)
            or (user['type'] == 'internal' and user['pk'] in fixture_ids))
        result = auth('POST', 'policies/all/' + policy['pk'] + '/test/', {'user': user['pk']})
        if result['passing'] != expected:
            raise ValueError('Live application allowlist check failed')
    test_user = users[0]['pk']
    source_checks = 0
    for email, verified, claim, expected in [
        (emails[0], True, 'email_verified', True),
        (emails[0], True, 'verified_email', True),
        (emails[0], False, 'email_verified', False),
        ('not-allowed@example.invalid', True, 'email_verified', False),
        (emails[0], 'true', 'email_verified', False),
    ]:
        result = auth('POST', 'policies/all/' + source_policy['pk'] + '/test/', {
            'user': test_user, 'context': {'oauth_userinfo': {'email': email, claim: verified}}})
        if result['passing'] != expected:
            raise ValueError('Live Google verified-email allowlist check failed')
        source_checks += 1
    # Validate the expected external connector and preserve every other setting.
    providers = netbird('GET', '/identity-providers')
    provider = unique(providers, 'issuer', config['authentik_issuer'])
    if provider['type'] != 'authentik':
        raise ValueError('Expected isolated Authentik connector')
    accounts = netbird('GET', '/accounts')
    if len(accounts) != 1 or accounts[0]['settings']['network_range'] != config['netbird_subnet']:
        raise ValueError('Expected the selected environment account and subnet')
    account = accounts[0]
    settings = account['settings']
    if assignments and not settings['groups_propagation_enabled']:
        raise ValueError('Staff access requires existing device group propagation')
    settings['extra']['user_approval_required'] = False
    netbird('PUT', '/accounts/' + account['id'], {'settings': settings})
    actual = netbird('GET', '/accounts')[0]['settings']
    if actual != settings:
        raise ValueError('NetBird settings readback differs from the requested settings')
    # Existing pending users stay pending when the switch changes. Reconcile only
    # exact allowlisted users from the verified connector, without granting roles.
    approved = 0
    for user in netbird('GET', '/users'):
        if user.get('pending_approval') and user.get('idp_id') == provider['id'] and user.get('email') in emails:
            if user['role'] != 'user':
                raise ValueError('Expected regular user role for pending allowlisted user')
            updated = netbird('POST', '/users/' + urllib.parse.quote(user['id'], safe='') + '/approve')
            if updated.get('pending_approval') or updated.get('is_blocked') or updated.get('role') != 'user':
                raise ValueError('Allowlisted pending user did not become a regular active user')
            approved += 1
    staff_report = configure_staff_access(assignments, users, provider, auth, netbird)
    return {**staff_report, 'environment': profile['environment'], 'application_policy_checks': len(users),
            'google_policy_checks': source_checks, 'manual_approval_required': False,
            'existing_allowlisted_pending_users_reconciled': approved}


def load_encrypted_config(path, age_key_file=None):
    """Decrypt authenticated SOPS input in memory; never fall back to plaintext."""
    key_file = age_key_file or os.environ.get('SOPS_AGE_KEY_FILE')
    if not key_file:
        raise ValueError('Supply --age-key-file or SOPS_AGE_KEY_FILE for the encrypted config')
    try:
        result = subprocess.run(
            ['sops', '--decrypt', '--output-type', 'json', str(path)],
            env={**os.environ, 'SOPS_AGE_KEY_FILE': str(key_file)},
            capture_output=True, text=True, check=True, timeout=30)
        config = json.loads(result.stdout)
        if not isinstance(config, dict):
            raise ValueError('Expected a configuration object')
        return config
    except (OSError, subprocess.SubprocessError, ValueError):
        # SOPS diagnostics and malformed decrypted JSON can contain private data.
        raise ValueError('Encrypted configuration could not be decrypted and validated') from None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--age-key-file', help='Age identity for config decryption (or SOPS_AGE_KEY_FILE)')
    args = p.parse_args()
    c = load_encrypted_config(args.config, args.age_key_file)
    profile = deployment_profile(c)
    token = load_encrypted_config(c['netbird_token_file'], c['age_key_file'])
    if token['netbird_management_url'] != c['netbird_url']:
        raise ValueError('Token belongs to a different NetBird deployment')

    def auth(method, path, body=None):
        command = shlex.join(['sudo', '-n', 'docker', 'exec', '-i',
            profile['container'], 'python', '-c', AUTH_REQUEST])
        r = subprocess.run(c['ssh'] + [command], input=json.dumps({
            'method': method, 'path': path, 'body': body}), capture_output=True,
            text=True, check=True, timeout=35)
        result = json.loads(r.stdout)
        if isinstance(result, dict) and (result.get('next') or result.get('pagination', {}).get('next')):
            raise ValueError('Refusing partially paginated Authentik inputs')
        return result

    def netbird(method, path, body=None):
        req = urllib.request.Request(c['netbird_url'] + '/api' + path, method=method,
            data=None if body is None else json.dumps(body).encode(), headers={
                'Authorization': 'Token ' + token['netbird_api_token'], 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            return json.loads(data) if data else {}

    print(json.dumps(configure(c, auth, netbird)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # API errors and subprocess tracebacks may carry credentials or emails.
        sys.exit('Admission reconciliation failed; private diagnostics withheld. Check encrypted inputs and service policy gates.')
