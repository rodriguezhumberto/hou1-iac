"""Privileged worker for offboard-user.py; JSON stdin only, no public listener.

Executed through the existing pinned SSH/sudo administration path. Authentik's
bootstrap credential stays in its own container. Audit actors are obtained from
sudo/kernel identity, never supplied in the operation request.
"""

import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import urllib.parse
import uuid

AUDIT_BASE = Path('/var/lib/hou1-operations')
AUDIT_OWNER = 0
REASONS = {'departure', 'role-removal', 'security-incident', 'test-drill'}


class Failure(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Failure(code)


def actor():
    require(os.geteuid() == 0, 'privileged_management_required')
    name = os.environ.get('SUDO_USER', 'root')
    uid = int(os.environ.get('SUDO_UID', '0'))
    require(pwd.getpwnam(name).pw_uid == uid, 'invalid_management_identity')
    return {'user': name, 'uid': uid, 'authentication': 'ssh-sudo' if 'SUDO_USER' in os.environ else 'root-management'}


def run(command, data=None):
    result = subprocess.run(command, input=None if data is None else json.dumps(data),
                            capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, 'service_operation_failed')
    return json.loads(result.stdout)


AUTH_REQUEST = r'''
import json,os,sys,urllib.request
r=json.load(sys.stdin)
req=urllib.request.Request('http://127.0.0.1:9000/api/v3/'+r['path'],
 data=None if r.get('body') is None else json.dumps(r['body']).encode(),
 method=r.get('method','GET'),headers={'Authorization':'Bearer '+os.environ['AUTHENTIK_BOOTSTRAP_TOKEN'],'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=30) as response:
 print(response.read().decode() or '{}')
'''


def containers(c):
    suffix = 'test' if c['environment'] == 'test' else 'prod'
    return ('pearlandpaws-' + suffix + '-astro-app-1',
            'hou1-authentik-' + suffix + '-authentik-server-1')


def auth(c, path, method='GET', body=None):
    return run(['docker', 'exec', '-i', containers(c)[1], 'python', '-c', AUTH_REQUEST],
               {'path': path, 'method': method, 'body': body})


def pages(c, path):
    result = []
    parsed = urllib.parse.urlsplit(path)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    seen = set()
    for _ in range(100):
        page = auth(c, parsed.path + '?' + urllib.parse.urlencode(query))
        require(isinstance(page.get('results'), list) and isinstance(page.get('pagination'), dict), 'invalid_authentik_page')
        result.extend(page['results'])
        following = page['pagination'].get('next')
        if not following:
            return result
        require(type(following) is int and following > 0 and following not in seen, 'invalid_authentik_pagination')
        seen.add(following)
        query['page'] = following
    raise Failure('authentik_page_limit')


def capabilities(c, live=False):
    app = containers(c)[0]
    inspect = run(['docker', 'inspect', app])[0]
    require((inspect['Config'].get('Labels') or {}).get('io.pearlandpaws.staff-offboarding-schema') == '1', 'staff_release_missing_offboarding_support')
    environment = dict(item.split('=', 1) for item in inspect['Config']['Env'])
    require(environment.get('STAFF_ENVIRONMENT') == c['environment']
            and environment.get('STAFF_OIDC_ISSUER') == c['staff_issuer'], 'staff_runtime_identity_mismatch')
    if live:
        result = run(['docker', 'exec', '--user', '0', app, 'node', '/app/scripts/staff-offboard.mjs', '--check'])
        require(result.get('schema') == 1 and result.get('persistent_subject_blocking') is True
                and result.get('environment') == c['environment'] and result.get('issuer') == c['staff_issuer'], 'staff_runtime_identity_mismatch')
    image = run(['docker', 'inspect', containers(c)[1]])[0]['Config']['Image']
    require(image == 'ghcr.io/goauthentik/server:2025.10.4', 'untested_authentik_release')
    suffix = 'test' if c['environment'] == 'test' else 'prod'
    image = run(['docker', 'inspect', 'hou1-netbird-' + suffix + '-netbird-server-1'])[0]['Config']['Image']
    require(image == 'netbirdio/netbird-server:0.82.0', 'untested_netbird_release')
    return {'schema': 1, 'persistent_subject_blocking': True}


def subject(provider, user):
    field = {'hashed_user_id': 'uid', 'user_uuid': 'uuid', 'user_id': 'pk'}.get(provider['sub_mode'])
    require(field is not None, 'mutable_oidc_subject_not_supported')
    value = str(user[field])
    require(re.fullmatch(r'[A-Za-z0-9._:-]{1,255}', value) is not None, 'invalid_oidc_subject')
    return value


def eligible_user(c, user):
    fixture = (c['environment'] == 'test' and user['type'] == 'internal'
               and user['username'].startswith('offboard-test-') and user['email'].endswith('@example.invalid'))
    require(not user['is_superuser'] and (fixture or (user['type'] == 'external'
            and user['path'] == 'goauthentik.io/sources/google')), 'recovery_or_unsupported_identity')
    return fixture


def resolve(c, target):
    matches = [u for u in pages(c, 'core/users/?page_size=1000') if u['uuid'] == target]
    require(len(matches) == 1, 'authentik_identity_not_unique')
    user = matches[0]
    fixture = eligible_user(c, user)
    providers = {}
    for name in ('staff', 'netbird'):
        app = auth(c, 'core/applications/' + urllib.parse.quote(c[name + '_application'], safe='') + '/')
        provider = auth(c, 'providers/oauth2/' + str(app['provider']) + '/')
        require(provider['client_id'] == c[name + '_client_id'] and provider['issuer_mode'] == 'per_provider', 'oidc_provider_contract_mismatch')
        providers[name] = subject(provider, user)
    return {'target_uuid': target, 'authentik_pk': user['pk'], 'staff_subject': providers['staff'],
            'netbird_subject': providers['netbird'],
            'email_sha256': hashlib.sha256(user['email'].lower().encode()).hexdigest(),
            'fixture_username_sha256': hashlib.sha256(user['username'].encode()).hexdigest() if fixture else None}


def secure_directory(path):
    current = AUDIT_BASE.parent
    for part in path.relative_to(current).parts:
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        info = current.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == AUDIT_OWNER and not info.st_mode & 0o077, 'unsafe_audit_directory')


def secure_open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == AUDIT_OWNER and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_audit_file')
        return fd
    except BaseException:
        os.close(fd)
        raise


def paths(c, operation_id):
    root = AUDIT_BASE / c['environment'] / 'offboarding'
    secure_directory(root)
    return root / (operation_id + '.json'), root / 'audit.jsonl'


def config_digest(c):
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()


def manifest(c, request, plan=None):
    path, _ = paths(c, request['operation_id'])
    metadata = {k: request[k] for k in ('operation_id', 'target_uuid', 'reason', 'reference')}
    metadata['config_sha256'] = config_digest(c)
    lock_fd = secure_open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT)
    with os.fdopen(lock_fd) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return read_manifest(path, metadata)
        require(plan is not None, 'operation_plan_missing')
        temporary = path.with_suffix('.new-' + uuid.uuid4().hex)
        fd = secure_open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, 'w') as stream:
            json.dump({**metadata, 'plan': plan}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return plan


def read_manifest(path, metadata):
    fd = secure_open(path, os.O_RDONLY)
    with os.fdopen(fd) as stream:
        existing = json.load(stream)
    require(all(existing.get(k) == v for k, v in metadata.items()), 'operation_identity_mismatch')
    return existing['plan']


def append_audit(c, request, who, event, step=None, outcome=None):
    _, path = paths(c, request['operation_id'])
    record = {k: request[k] for k in ('operation_id', 'target_uuid', 'reason', 'reference')}
    record.update({'schema': 1, 'environment': c['environment'], 'actor': who,
                   'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'event': event, 'step': step, 'outcome': outcome})
    fd = secure_open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def validated_user(c, plan):
    user = auth(c, 'core/users/' + str(plan['authentik_pk']) + '/')
    require(user['uuid'] == plan['target_uuid'] and not user['is_superuser'], 'authentik_identity_changed')
    eligible_user(c, user)
    return user


def revoke_idp(c, plan):
    user = validated_user(c, plan)
    require(not user['is_active'], 'disable_identity_before_revoking_sessions')
    total = 0
    resources = [('core/authenticated_sessions/', 'uuid'), ('oauth2/authorization_codes/', 'pk'),
                 ('oauth2/refresh_tokens/', 'pk'), ('oauth2/access_tokens/', 'pk')]

    def owned(path):
        records = []
        for item in pages(c, path + '?page_size=1000'):
            owner = item.get('user')
            owner_pk = owner.get('pk') if isinstance(owner, dict) else owner
            require(type(owner_pk) is int, 'unknown_idp_record_owner')
            if owner_pk == user['pk']:
                records.append(item)
        return records

    for path, key in resources:
        for item in owned(path):
            require(key in item and isinstance(item[key], (int, str)), 'unknown_idp_record_key')
            auth(c, path + urllib.parse.quote(str(item[key]), safe='') + '/', 'DELETE')
            total += 1
        require(not owned(path), 'idp_revocation_readback_failed')
    return {'revoked_idp_records': total}


def validate_request(request):
    allowed = {'action', 'config', 'target_uuid', 'operation_id', 'reason', 'reference', 'step', 'outcome'}
    require(isinstance(request, dict) and not set(request) - allowed, 'invalid_operation_request')
    for key in ('target_uuid', 'operation_id'):
        require(str(uuid.UUID(request[key])) == request[key], 'invalid_operation_identity')
    require(uuid.UUID(request['operation_id']).version == 4, 'invalid_operation_identity')
    require(request['reason'] in REASONS and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', request['reference']) is not None, 'invalid_audit_reference')
    c = request['config']
    require(c['environment'] in ('test', 'production'), 'invalid_environment')
    require(set(c) == {'environment', 'staff_issuer', 'staff_application', 'staff_client_id',
                       'netbird_application', 'netbird_client_id'}, 'invalid_remote_configuration')
    for key in ('staff_application', 'netbird_application'):
        require(re.fullmatch(r'[a-z0-9-]+', c[key]) is not None, 'invalid_application_slug')
    return c


def handle(request):
    who = actor()
    c = validate_request(request)
    action = request['action']
    require(action in ('plan', 'begin', 'audit', 'block_staff', 'disable_authentik', 'revoke_idp', 'verify'), 'invalid_operation_action')
    if action == 'plan':
        capabilities(c)
        return {'actor': who, 'plan': resolve(c, request['target_uuid'])}
    if action == 'begin':
        capabilities(c)
        path, _ = paths(c, request['operation_id'])
        plan = manifest(c, request, None if path.exists() else resolve(c, request['target_uuid']))
        append_audit(c, request, who, 'begin')
        return {'actor': who, 'plan': plan}
    plan = manifest(c, request)
    if action == 'audit':
        require(request['step'] in ('admission', 'staff', 'authentik', 'idp_sessions', 'netbird', 'peers', 'verification', 'complete'), 'invalid_audit_step')
        require(request['outcome'] in ('intent', 'passed', 'failed', 'partial'), 'invalid_audit_outcome')
        append_audit(c, request, who, 'step', request['step'], request['outcome'])
        return {'recorded': True, 'actor': who}
    append_audit(c, request, who, 'intent', action)
    try:
        if action == 'block_staff':
            capabilities(c, live=True)
            payload = {'schema': 1, 'environment': c['environment'], 'issuer': c['staff_issuer'],
                       'operation_id': request['operation_id'], 'subject': plan['staff_subject'],
                       'reason': request['reason'], 'reference': request['reference']}
            result = run(['docker', 'exec', '--user', '0', '-i', containers(c)[0],
                          'node', '/app/scripts/staff-offboard.mjs'], payload)
        elif action == 'disable_authentik':
            validated_user(c, plan)
            result = auth(c, 'core/users/' + str(plan['authentik_pk']) + '/', 'PATCH', {'is_active': False, 'groups': []})
            require(result['uuid'] == plan['target_uuid'] and not result['is_active'] and not result['groups'], 'authentik_disable_readback_failed')
            result = {'identity_disabled': True, 'groups_removed': True}
        elif action == 'revoke_idp':
            result = revoke_idp(c, plan)
        else:
            user = validated_user(c, plan)
            require(not user['is_active'] and not user['groups'], 'authentik_not_disabled')
            # Reapplying the block both verifies denial and retries any sessions
            # issued before earlier phases completed, with another DB audit row.
            payload = {'schema': 1, 'environment': c['environment'], 'issuer': c['staff_issuer'],
                       'operation_id': request['operation_id'], 'subject': plan['staff_subject'],
                       'reason': request['reason'], 'reference': request['reference']}
            result = run(['docker', 'exec', '--user', '0', '-i', containers(c)[0], 'node', '/app/scripts/staff-offboard.mjs'], payload)
        append_audit(c, request, who, 'result', action, 'passed')
        return result
    except BaseException:
        append_audit(c, request, who, 'result', action, 'failed')
        raise


if __name__ == '__main__':
    try:
        raw = sys.stdin.read(32769)
        require(len(raw) <= 32768, 'request_too_large')
        print(json.dumps(handle(json.loads(raw))))
    except Exception as error:
        code = str(error) if isinstance(error, Failure) else 'remote_operation_failed'
        print(json.dumps({'error': code}))
        sys.exit(1)
