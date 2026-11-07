#!/usr/bin/env python3
"""Offboard one immutable Authentik identity through private SSH/sudo.

Dry-run is the default. Secrets are SOPS/age encrypted; tokens and decrypted
allowlists stay in memory on the controller. No production rollout is implied.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

from offboard_remote import Failure, require


class Unavailable(Failure):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def sops_read(path, key):
    result = subprocess.run(['sops', '--decrypt', '--output-type', 'json', str(path)],
                            env={**os.environ, 'SOPS_AGE_KEY_FILE': str(key)},
                            capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, 'sops_decryption_failed')
    return json.loads(result.stdout)


def validate_config(c, environment):
    require(c['environment'] == environment and environment in ('test', 'production'), 'environment_mismatch')
    nb = urllib.parse.urlsplit(c['netbird_url'])
    issuer = urllib.parse.urlsplit(c['netbird_issuer'])
    staff = urllib.parse.urlsplit(c['staff_issuer'])
    for value in (nb, issuer, staff):
        require(value.scheme == 'https' and value.hostname and not value.username and not value.password
                and value.port in (None, 443) and not value.query and not value.fragment, 'unsafe_service_origin')
    require(not nb.path and issuer.netloc == staff.netloc, 'service_origin_mismatch')
    for name, url in (('staff', staff), ('netbird', issuer)):
        require(re.fullmatch(r'[a-z0-9-]+', c[name + '_application']) is not None
                and url.path == '/application/o/' + c[name + '_application'] + '/', 'issuer_application_mismatch')
    if environment == 'test':
        require(nb.hostname.startswith('netbird-test.') and issuer.hostname == nb.hostname.replace('netbird-test.', 'auth-test.', 1)
                and c['netbird_subnet'] == '100.91.0.0/16', 'test_isolation_mismatch')
    else:
        require('-test.' not in nb.hostname and '-test.' not in issuer.hostname
                and c['netbird_subnet'] == '100.90.0.0/16', 'production_isolation_mismatch')
    require(re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', c['ssh_fingerprint']) is not None, 'invalid_host_fingerprint')
    require(isinstance(c['ssh'], list) and c['ssh'][0] == 'ssh' and '-F' in c['ssh']
            and all(isinstance(arg, str) for arg in c['ssh'])
            and re.fullmatch(r'[A-Za-z0-9._-]+', c['ssh'][-1]) is not None, 'unsafe_management_transport')


def pinned_ssh(c):
    result = subprocess.run(['ssh', '-G', *c['ssh'][1:]], capture_output=True, text=True, timeout=15)
    require(result.returncode == 0, 'ssh_configuration_failed')
    values = dict(line.split(' ', 1) for line in result.stdout.splitlines() if ' ' in line)
    require(values.get('stricthostkeychecking') in ('yes', 'true') and values.get('identitiesonly') == 'yes', 'ssh_host_verification_required')
    known_files = shlex.split(values.get('userknownhostsfile', ''))
    require(len(known_files) == 1 and known_files[0] != '/dev/null', 'explicit_known_hosts_required')
    host = values.get('hostkeyalias', values['hostname'])
    known = subprocess.run(['ssh-keygen', '-F', host, '-f', os.path.expanduser(known_files[0])], capture_output=True, text=True, timeout=10)
    entries = [line for line in known.stdout.splitlines() if not line.startswith('#') and ' ssh-ed25519 ' in line]
    require(entries, 'pinned_ed25519_host_key_missing')
    check = subprocess.run(['ssh-keygen', '-lf', '/dev/stdin'], input='\n'.join(entries) + '\n',
                           capture_output=True, text=True, timeout=10)
    require(check.returncode == 0 and {line.split()[1] for line in check.stdout.splitlines()} == {c['ssh_fingerprint']}, 'ssh_fingerprint_mismatch')
    # Restrict negotiation to the algorithm whose fingerprint was checked.
    return [*c['ssh'][:-1], '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'KnownHostsCommand=none',
            '-o', 'VerifyHostKeyDNS=no', '-o', 'UpdateHostKeys=no', c['ssh'][-1]], values['user']


def decode_netbird_id(value):
    try:
        require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,2048}', value) is not None, 'unsupported_netbird_identity')
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        fields, offset = {}, 0
        while offset < len(raw):
            tag = raw[offset]
            offset += 1
            require(tag in (10, 18) and tag not in fields, 'unsupported_netbird_identity')
            length, shift = 0, 0
            while True:
                require(offset < len(raw) and shift <= 21, 'unsupported_netbird_identity')
                byte = raw[offset]
                offset += 1
                length |= (byte & 127) << shift
                if not byte & 128:
                    break
                shift += 7
            require(0 < length <= 512 and offset + length <= len(raw), 'unsupported_netbird_identity')
            fields[tag] = raw[offset:offset + length].decode('utf8')
            offset += length
        require(set(fields) == {10, 18}, 'unsupported_netbird_identity')
        return fields[10], fields[18]
    except (ValueError, UnicodeError):
        raise Failure('unsupported_netbird_identity') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Netbird:
    def __init__(self, c, token):
        self.config, self.token = c, token
        self.opener = urllib.request.build_opener(NoRedirect)

    def __call__(self, method, path, body=None):
        req = urllib.request.Request(self.config['netbird_url'] + '/api' + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Token ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=20) as response:
                data = response.read()
                return json.loads(data) if data else {}
        except (urllib.error.URLError, TimeoutError):
            raise Unavailable('netbird_request_failed') from None


def netbird_binding(c, plan, api):
    providers = [p for p in api('GET', '/identity-providers') if p['issuer'] == c['netbird_issuer']]
    require(len(providers) == 1 and providers[0]['type'] == 'authentik'
            and providers[0]['client_id'] == c['netbird_client_id'], 'netbird_provider_mismatch')
    accounts = api('GET', '/accounts')
    require(len(accounts) == 1 and accounts[0]['settings']['network_range'] == c['netbird_subnet'], 'netbird_account_mismatch')
    provider = providers[0]
    matches = []
    for user in api('GET', '/users'):
        if user.get('idp_id') != provider['id']:
            continue
        sub, connector = decode_netbird_id(user['id'])
        require(connector == provider['id'], 'netbird_connector_binding_mismatch')
        if sub == plan['netbird_subject']:
            require(not user['is_service_user'] and user['role'] == 'user', 'netbird_recovery_identity_protected')
            matches.append(user)
    require(len(matches) <= 1, 'netbird_identity_not_unique')
    return matches[0] if matches else None


def remove_admission(c, plan, key):
    path = Path(c['admission_file'])
    require(not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == 0o600
            and path.stat().st_uid == os.getuid(), 'unsafe_admission_file')
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        document = sops_read(path, key)
        require(document['environment'] == ('prod' if c['environment'] == 'production' else 'test')
                and document['netbird_url'] == c['netbird_url']
                and document['authentik_issuer'] == c['netbird_issuer'], 'admission_environment_mismatch')
        before = sum(len(document.get(name, [])) for name in ('google_emails', 'staff_users', 'fixture_users'))
        document['google_emails'] = [v for v in document.get('google_emails', []) if digest(v.lower()) != plan['email_sha256']]
        document['staff_users'] = [v for v in document.get('staff_users', []) if digest(v['email'].lower()) != plan['email_sha256']]
        if plan.get('fixture_username_sha256'):
            document['fixture_users'] = [v for v in document.get('fixture_users', []) if digest(v) != plan['fixture_username_sha256']]
        after = sum(len(document.get(name, [])) for name in ('google_emails', 'staff_users', 'fixture_users'))
        if before == after:
            return {'admission_entries_removed': 0}
        encrypted = json.loads(path.read_text())
        metadata = encrypted['sops']
        require(not metadata.get('key_groups') and not any(metadata.get(k) for k in ('kms', 'gcp_kms', 'azure_kv', 'hc_vault', 'pgp')), 'unsupported_admission_recipients')
        recipients = [entry['recipient'] for entry in metadata['age']]
        require(recipients and all(r.startswith('age1') for r in recipients), 'missing_age_recipients')
        result = subprocess.run(['sops', '--encrypt', '--config', '/dev/null', '--age', ','.join(recipients),
                                 '--input-type', 'json', '--output-type', 'json', '/dev/stdin'],
                                input=json.dumps(document), capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, 'admission_encryption_failed')
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.enc.json', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(result.stdout)
                stream.flush()
                os.fsync(stream.fileno())
            require(sops_read(temporary, key) == document, 'admission_encryption_readback_failed')
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'admission_entries_removed': before - after}


def execute(c, request, remote, api, key, apply=False):
    prepared = remote('begin' if apply else 'plan')
    plan = prepared['plan']
    try:
        binding = netbird_binding(c, plan, api)
    except Unavailable:
        binding = None
        if not apply:
            raise
    except Failure:
        if apply:
            remote('audit', step='complete', outcome='failed')
        raise
    if not apply:
        return {'schema': 1, 'mode': 'plan', 'environment': c['environment'], 'operation_id': request['operation_id'],
                'target_uuid': request['target_uuid'], 'operator': prepared['actor'],
                'netbird_registered': binding is not None, 'mutations': False}
    report = {'schema': 1, 'mode': 'apply', 'environment': c['environment'], 'operation_id': request['operation_id'],
              'target_uuid': request['target_uuid'], 'operator': prepared['actor'], 'steps': {}}
    audit_ok = True

    def step(name, fn):
        nonlocal audit_ok
        if not audit_ok:
            report['steps'][name] = {'status': 'not_attempted', 'error': 'audit_unavailable'}
            return
        try:
            remote('audit', step=name, outcome='intent')
        except Exception:
            audit_ok = False
            report['steps'][name] = {'status': 'failed', 'error': 'audit_unavailable'}
            return
        try:
            result = fn()
            report['steps'][name] = {'status': 'passed', **result}
        except Exception as error:
            report['steps'][name] = {'status': 'failed', 'error': str(error) if isinstance(error, Failure) else 'operation_failed'}
        try:
            remote('audit', step=name, outcome=report['steps'][name]['status'])
        except Exception:
            audit_ok = False
            report['steps'][name]['audit_result'] = 'unavailable'

    def block_netbird():
        user = netbird_binding(c, plan, api)
        if user is None:
            return {'registered': False}
        api('PUT', '/users/' + urllib.parse.quote(user['id'], safe=''),
            {'role': 'user', 'auto_groups': [], 'is_blocked': True})
        actual = netbird_binding(c, plan, api)
        require(actual is not None and actual['is_blocked'] and not actual['auto_groups'], 'netbird_block_readback_failed')
        return {'registered': True, 'blocked': True}

    def remove_peers():
        user = netbird_binding(c, plan, api)
        if user is None:
            return {'peers_removed': 0}
        require(user['is_blocked'], 'netbird_user_not_blocked')
        peers = [p for p in api('GET', '/peers') if p.get('user_id') == user['id']]
        for peer in peers:
            api('DELETE', '/peers/' + urllib.parse.quote(peer['id'], safe=''))
        require(not [p for p in api('GET', '/peers') if p.get('user_id') == user['id']], 'owned_peers_remain')
        return {'peers_removed': len(peers)}

    def verify():
        remote('verify')
        user = netbird_binding(c, plan, api)
        if user:
            require(user['is_blocked'] and not user['auto_groups'], 'netbird_access_remains')
            require(not [p for p in api('GET', '/peers') if p.get('user_id') == user['id']], 'owned_peers_remain')
        document = sops_read(c['admission_file'], key)
        require(not any(digest(v.lower()) == plan['email_sha256'] for v in document.get('google_emails', []))
                and not any(digest(v['email'].lower()) == plan['email_sha256'] for v in document.get('staff_users', []))
                and not any(digest(v) == plan.get('fixture_username_sha256') for v in document.get('fixture_users', [])), 'admission_entry_remains')
        return {'denial_readback': True}

    step('admission', lambda: remove_admission(c, plan, key))
    step('staff', lambda: remote('block_staff'))
    step('authentik', lambda: remote('disable_authentik'))
    step('idp_sessions', lambda: remote('revoke_idp'))
    step('netbird', block_netbird)
    step('peers', remove_peers)
    step('verification', verify)
    report['complete'] = audit_ok and all(v['status'] == 'passed' for v in report['steps'].values())
    try:
        remote('audit', step='complete', outcome='passed' if report['complete'] else 'partial')
    except Exception:
        report['complete'] = False
        report['audit_result'] = 'unavailable'
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--age-key-file', default=os.environ.get('SOPS_AGE_KEY_FILE'))
    p.add_argument('--environment', choices=('test', 'production'), required=True)
    p.add_argument('--target', required=True, help='Immutable Authentik user UUID, never email')
    p.add_argument('--operation-id', default=None, help='Reuse the same UUID for a retry')
    p.add_argument('--reason', choices=('departure', 'role-removal', 'security-incident', 'test-drill'), required=True)
    p.add_argument('--reference', required=True, help='Non-sensitive ticket/change identifier')
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    try:
        require(args.age_key_file, 'age_identity_required')
        c = sops_read(args.config, args.age_key_file)
        validate_config(c, args.environment)
        transport, expected_actor = pinned_ssh(c)
        request = {'operation_id': args.operation_id or str(uuid.uuid4()), 'target_uuid': str(uuid.UUID(args.target)),
                   'reason': args.reason, 'reference': args.reference,
                   'config': {k: c[k] for k in ('environment', 'staff_issuer', 'staff_application', 'staff_client_id', 'netbird_application', 'netbird_client_id')}}
        source = Path(__file__).with_name('offboard_remote.py').read_text()

        def remote(action, **extra):
            command = shlex.join(['sudo', '-n', 'python3', '-c', source])
            result = subprocess.run([*transport, command], input=json.dumps({**request, 'action': action, **extra}),
                                    capture_output=True, text=True, timeout=240)
            try:
                value = json.loads(result.stdout)
            except ValueError:
                raise Failure('management_operation_failed') from None
            require(result.returncode == 0 and 'error' not in value, value.get('error', 'management_operation_failed'))
            if 'actor' in value:
                require(value['actor']['user'] == expected_actor, 'authenticated_actor_mismatch')
            return value

        token = sops_read(c['netbird_token_file'], args.age_key_file)
        require(token['netbird_management_url'] == c['netbird_url'], 'netbird_token_deployment_mismatch')
        result = execute(c, request, remote, Netbird(c, token['netbird_api_token']), args.age_key_file, args.apply)
        print(json.dumps(result))
        return 0 if result.get('complete', not args.apply) else 1
    except Exception as error:
        print(json.dumps({'schema': 1, 'complete': False, 'error': str(error) if isinstance(error, Failure) else 'offboarding_failed'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
