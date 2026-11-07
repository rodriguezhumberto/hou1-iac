#!/usr/bin/env python3
"""Enforce Authentik admission before disabling the isolated NetBird approval queue."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
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


def configure(config, auth, netbird):
    if config['environment'] != 'test':
        raise ValueError('This command is restricted to isolated test')
    emails = sorted(set(config['google_emails']))
    if not emails or any(e != e.lower() or e.count('@') != 1 or '*' in e or any(c.isspace() for c in e) for e in emails):
        raise ValueError('Supply explicit lowercase Google account addresses')
    users = auth('GET', 'core/users/?page_size=1000')['results']
    fixture_ids = []
    for name in config['fixture_users']:
        u = unique(users, 'username', name)
        if u['type'] != 'internal' or not u['email'].endswith('@example.invalid'):
            raise ValueError('Fixtures must be isolated internal test identities')
        fixture_ids.append(u['pk'])
    app = auth('GET', 'core/applications/netbird-test/')
    source = auth('GET', 'sources/oauth/google/')
    if not source['enabled'] or source['provider_type'] != 'google':
        raise ValueError('The test Google source must be enabled')
    policies = auth('GET', 'policies/expression/?page_size=1000')['results']
    source_policy = unique(policies, 'name', 'Google approved accounts')
    bindings = auth('GET', 'policies/bindings/?page_size=1000')['results']
    flows = auth('GET', 'flows/instances/?page_size=1000')['results']
    for flow in ('authentication_flow', 'enrollment_flow'):
        instance = unique(flows, 'pk', source[flow])
        if instance['policy_engine_mode'] != 'all' or not any(
            b['target'] == source[flow] and b['policy'] == source_policy['pk']
            and b['enabled'] and not b['negate'] for b in bindings
        ):
            raise ValueError('Google allowlist must gate both source flows in ALL mode')
    expression = (
        'userinfo = context.get("oauth_userinfo", {})\n'
        'verified = userinfo.get("email_verified", userinfo.get("verified_email", False))\n'
        f'return verified is True and userinfo.get("email", "").lower() in {emails!r}'
    )
    auth('PATCH', 'policies/expression/' + source_policy['pk'] + '/', {'expression': expression})
    name = 'HOU1 test NetBird allowlist'
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
    auth('PATCH', 'core/applications/netbird-test/', {'policy_engine_mode': 'all'})
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
        raise ValueError('Expected the isolated test account and subnet')
    account = accounts[0]
    settings = account['settings']
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
    return {'environment': 'test', 'application_policy_checks': len(users),
            'google_policy_checks': source_checks, 'manual_approval_required': False,
            'existing_allowlisted_pending_users_reconciled': approved}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    c = json.loads(Path(p.parse_args().config).read_text())
    if c['environment'] != 'test':
        raise ValueError('Refusing production')
    nb = urllib.parse.urlsplit(c['netbird_url'])
    issuer = urllib.parse.urlsplit(c['authentik_issuer'])
    if (nb.scheme != 'https' or not nb.hostname.startswith('netbird-test.') or nb.path
            or issuer.scheme != 'https' or issuer.hostname != nb.hostname.replace('netbird-test.', 'auth-test.', 1)
            or issuer.path != '/application/o/netbird-test/' or c['netbird_subnet'] == '100.90.0.0/16'):
        raise ValueError('Expected isolated test origins, issuer and subnet')
    token = json.loads(subprocess.run(
        ['sops', '--decrypt', '--output-type', 'json', c['netbird_token_file']],
        env={**os.environ, 'SOPS_AGE_KEY_FILE': c['age_key_file']},
        capture_output=True, text=True, check=True).stdout)
    if token['netbird_management_url'] != c['netbird_url']:
        raise ValueError('Token belongs to a different NetBird deployment')

    def auth(method, path, body=None):
        command = shlex.join(['sudo', '-n', 'docker', 'exec', '-i',
            'hou1-authentik-test-authentik-server-1', 'python', '-c', AUTH_REQUEST])
        r = subprocess.run(c['ssh'] + [command], input=json.dumps({
            'method': method, 'path': path, 'body': body}), capture_output=True,
            text=True, check=True, timeout=35)
        result = json.loads(r.stdout)
        if isinstance(result, dict) and result.get('next'):
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
    main()
