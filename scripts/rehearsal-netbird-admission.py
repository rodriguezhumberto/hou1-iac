#!/usr/bin/env python3
"""Enforce environment-bound Authentik admission before disabling NetBird approvals."""

import argparse
from contextlib import ExitStack
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from ssh_target import pinned_ssh, private_file

# Use the same profile validator as managed-host bootstrap.
_spec = importlib.util.spec_from_file_location(
    'hou1_identity', Path(__file__).resolve().parents[1] /
    'ansible/playbooks/roles/authentik/files/bootstrap.py')
identity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(identity)

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


STAFF_ROLES = ('reader', 'editor', 'admin')


def validate_assignments(assignments, emails):
    if (not isinstance(assignments, list)
            or any(not isinstance(user, dict) or user.get('email') not in emails
                   or user.get('role') not in STAFF_ROLES
                   or user.get('vpn_group', 'staff') not in ('staff', 'administrators')
                   or user.get('netbird_role', 'user') not in ('user', 'admin') for user in assignments)
            or len({user['email'] for user in assignments}) != len(assignments)):
        raise ValueError('Declare unique allowlisted users and explicit application/VPN permissions')


def provision_base_users(assignments, users, auth):
    """Prepare declared Google identities without passwords or invitations."""
    for assignment in assignments:
        matches = [user for user in users if user.get('email') == assignment['email']]
        if len(matches) > 1:
            raise ValueError('Duplicate base identity email')
        if not matches:
            users.append(auth('POST', 'core/users/', {
                'username': assignment['email'], 'email': assignment['email'],
                'name': assignment.get('name', assignment['email']), 'type': 'internal',
                'path': 'goauthentik.io/sources/google', 'is_active': True,
            }))
    return users


def deployment_profile(config, profile):
    """Bind encrypted inputs to a separately selected, inventory-exported profile."""
    identity.validate_profile(profile)
    if config.get('environment') != profile['environment']:
        raise ValueError('Configuration and inventory profile environments differ')
    issuer = 'https://' + profile['issuer_host'] + '/application/o/' + profile['netbird']['client_id'] + '/'
    if (config.get('netbird_url') != profile['netbird_url']
            or config.get('authentik_issuer') != issuer
            or config.get('netbird_subnet') != profile['netbird_subnet']):
        raise ValueError('Endpoints, client and subnet must match the selected inventory profile')
    if profile['environment'] == 'prod' and config.get('fixture_users'):
        raise ValueError('Production admission cannot include rehearsal fixtures')
    return profile


def auth_results(auth, path):
    result = auth('GET', path)
    if result.get('next') or result.get('pagination', {}).get('next'):
        raise ValueError('Refusing partially paginated Authentik inputs')
    return result['results']


def configure_staff_access(assignments, users, provider, auth, netbird, role_groups):
    """Keep network staff access separate from application privilege."""
    if not assignments:
        return {'staff_users_configured': 0, 'staff_users_awaiting_first_login': 0}
    network_groups = netbird('GET', '/groups')
    groups = auth_results(auth, 'core/groups/?page_size=1000')
    role_ids = {role: unique(groups, 'name', name)['pk'] for role, name in role_groups.items()}
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
        desired_role = assignment.get('netbird_role', 'user')
        if (nb_user['role'] not in ('user', 'admin') or nb_user['is_blocked'] or nb_user['pending_approval']
                or ('netbird_role' not in assignment and nb_user['role'] != 'user')):
            raise ValueError('Network assignment requires an active user and an explicit privileged role')
        group = unique(network_groups, 'name', assignment.get('vpn_group', 'staff'))
        current_groups = set(nb_user.get('auto_groups', []))
        if 'vpn_group' in assignment:
            current_groups -= {g['id'] for g in network_groups if g['name'] in ('staff', 'administrators')}
        desired = sorted(current_groups | {group['id']})
        if sorted(nb_user.get('auto_groups', [])) != desired or nb_user['role'] != desired_role:
            netbird('PUT', '/users/' + urllib.parse.quote(nb_user['id'], safe=''), {
                'auto_groups': desired, 'role': desired_role, 'is_blocked': nb_user['is_blocked']})
        actual = unique(netbird('GET', '/users'), 'id', nb_user['id'])
        if sorted(actual['auto_groups']) != desired or actual['role'] != desired_role:
            raise ValueError('NetBird staff group readback differs')
        owned_ids = {p['id'] for p in peers if p.get('user_id') == nb_user['id']}
        actual_peers = netbird('GET', '/peers')
        if not all(group['id'] in [g['id'] for g in p['groups']] for p in actual_peers if p['id'] in owned_ids):
            raise ValueError('Declared network group did not propagate to existing devices')
        configured += 1
    return {'staff_users_configured': configured, 'staff_users_awaiting_first_login': waiting}


def configure(config, auth, netbird, profile):
    profile = deployment_profile(config, profile)
    emails = sorted(set(config['google_emails']))
    if not emails or any(e != e.lower() or e.count('@') != 1 or '*' in e or any(c.isspace() for c in e) for e in emails):
        raise ValueError('Supply explicit lowercase Google account addresses')
    assignments = config.get('staff_users', [])
    validate_assignments(assignments, emails)
    if type(config.get('provision_users', False)) is not bool:
        raise ValueError('provision_users must be a boolean')
    users = auth_results(auth, 'core/users/?page_size=1000')
    if not users:
        raise ValueError('An existing identity is required for live policy checks')
    fixture_ids = []
    for name in config.get('fixture_users', []):
        u = unique(users, 'username', name)
        if u['type'] != 'internal' or not u['email'].endswith('@example.invalid'):
            raise ValueError('Fixtures must be isolated internal test identities')
        fixture_ids.append(u['pk'])
    app_path = 'core/applications/' + profile['netbird']['client_id'] + '/'
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
    if config.get('provision_users', False):
        users = provision_base_users(assignments, users, auth)
    expression = (
        'userinfo = context.get("oauth_userinfo", {})\n'
        'verified = userinfo.get("email_verified", userinfo.get("verified_email", False))\n'
        f'return verified is True and userinfo.get("email", "").lower() in {emails!r}'
    )
    if source_policy.get('expression') != expression:
        auth('PATCH', 'policies/expression/' + source_policy['pk'] + '/', {'expression': expression})
    name = 'HOU1 ' + profile['environment'] + ' NetBird allowlist'
    matches = [p for p in policies if p['name'] == name]
    if len(matches) > 1:
        raise ValueError('Duplicate managed admission policy')
    payload = {'name': name, 'expression': admission_expression(emails, fixture_ids)}
    if not matches:
        policy = auth('POST', 'policies/expression/', payload)
    elif any(matches[0].get(key) != value for key, value in payload.items()):
        policy = auth('PATCH', 'policies/expression/' + matches[0]['pk'] + '/', payload)
    else:
        policy = matches[0]
    payload = {'target': app['pk'], 'policy': policy['pk'], 'order': 0,
               'enabled': True, 'negate': False, 'failure_result': False}
    existing = [b for b in bindings if b['target'] == app['pk'] and b['policy'] == policy['pk']]
    if len(existing) > 1:
        raise ValueError('Duplicate managed admission binding')
    if existing and any(existing[0].get(key) != value for key, value in payload.items()):
        auth('PATCH', 'policies/bindings/' + existing[0]['pk'] + '/', payload)
    elif not existing:
        auth('POST', 'policies/bindings/', payload)
    if app.get('policy_engine_mode') != 'all':
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
    if settings['extra'].get('user_approval_required') is not False:
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
    staff_report = configure_staff_access(assignments, users, provider, auth, netbird, profile['role_groups'])
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
    p.add_argument('--identity-secrets', required=True, help='SOPS Authentik bundle containing IDENTITY_NAMES')
    p.add_argument('--profile', required=True, help='Public JSON exported from the selected Ansible inventory')
    p.add_argument('--age-key-file', help='Age identity for config decryption (or SOPS_AGE_KEY_FILE)')
    p.add_argument('--coordination-lock', help='Scheduled runs skip while a managed deployment holds this private lock')
    p.add_argument('--capture-lock', action='store_true', help='Coordinate scheduled writes with host backup capture')
    args = p.parse_args()
    with ExitStack() as stack:
        if args.coordination_lock:
            lock = stack.enter_context(private_file(args.coordination_lock).open('r+'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({'skipped': 'managed operation active'}))
                return
        reconcile(args, stack)


def reconcile(args, stack):
    c = load_encrypted_config(args.config, args.age_key_file)
    if c.get('google_secret_source'):
        google = load_encrypted_config(c['google_secret_source'], args.age_key_file)['GOOGLE_ALLOWED_EMAILS']
        if 'google_emails' in c and set(c['google_emails']) != set(google):
            raise ValueError('Admission and Google source allowlists differ')
        c['google_emails'] = google
    with open(args.profile, encoding='utf-8') as stream:
        public_profile = json.load(stream)
    names = load_encrypted_config(args.identity_secrets, args.age_key_file)['IDENTITY_NAMES']
    profile = deployment_profile(c, identity.resolve_profile(public_profile, names))
    ssh = pinned_ssh(c, environment=profile['environment'])
    token = load_encrypted_config(c['netbird_token_file'], c['age_key_file'])
    if token['netbird_management_url'] != c['netbird_url']:
        raise ValueError('Token belongs to a different NetBird deployment')

    capture = None
    if args.capture_lock:
        from lab_capture_guard import CaptureGuard
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix='.admission-', dir=Path(args.config).parent))
        command = ssh + [shlex.join(['sudo', '-n', 'python3',
            '/usr/local/libexec/hou1-backup-operation-lock.py', '--hold'])]
        capture = stack.enter_context(CaptureGuard(command, Path(directory) / 'capture.log', timeout=35))

    container = None

    def auth(method, path, body=None):
        nonlocal container
        if capture:
            capture.check()
        if container is None:
            command = shlex.join(['sudo', '-n', 'docker', 'compose', '-p', profile['compose_project'],
                                  '-f', profile['compose_file'], 'ps', '-q', profile['compose_service']])
            result = subprocess.run(ssh + [command], capture_output=True, text=True, check=True, timeout=35)
            container = result.stdout.strip()
            if not re.fullmatch(r'[0-9a-f]{12,64}', container):
                raise ValueError('Expected exactly one running Compose service container')
        command = shlex.join(['sudo', '-n', 'docker', 'exec', '-i',
            container, 'python', '-c', AUTH_REQUEST])
        r = subprocess.run(ssh + [command], input=json.dumps({
            'method': method, 'path': path, 'body': body}), capture_output=True,
            text=True, check=True, timeout=35)
        result = json.loads(r.stdout)
        if capture:
            capture.check()
        if isinstance(result, dict) and (result.get('next') or result.get('pagination', {}).get('next')):
            raise ValueError('Refusing partially paginated Authentik inputs')
        return result

    def netbird(method, path, body=None):
        if capture:
            capture.check()
        req = urllib.request.Request(c['netbird_url'] + '/api' + path, method=method,
            data=None if body is None else json.dumps(body).encode(), headers={
                'Authorization': 'Token ' + token['netbird_api_token'], 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            if capture:
                capture.check()
            return json.loads(data) if data else {}

    report = configure(c, auth, netbird, profile)
    if capture:
        capture.check()
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # API errors and subprocess tracebacks may carry credentials or emails.
        sys.exit('Admission reconciliation failed; private diagnostics withheld. Check encrypted inputs and service policy gates.')
