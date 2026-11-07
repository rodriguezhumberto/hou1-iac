"""Measure IdP disable/group-change propagation and explicit app revocation.

Only the internal vps-staff-admin test fixture is changed. Existing human users,
NetBird groups and production are untouched. A five-minute local recovery job
restores the fixture if the controller disappears.
"""

import json
import uuid

from controller import ROOT, api, probe, secrets, shell

UNIT = 'hou1-session-security-recovery'
REMOTE = '/root/hou1-session-security-recover.py'
REPORT = ROOT / 'revocation-boundary-report.json'


def args(name, reader=False):
    return {'run_id': 'revoke-' + name + '-' + uuid.uuid4().hex[:8],
            'username': 'vps-staff-reader' if reader else 'vps-staff-admin',
            'password': secrets()['TEST_READER_PASSWORD' if reader else 'TEST_ADMIN_PASSWORD']}


def retain(name, reader=False):
    a = args(name, reader)
    probe({**a, 'action': 'start'})
    result = probe({**a, 'action': 'finish', 'keep_session': True})
    assert result['protected_status'] == 200 and result['session_created']
    return a, result['session_sha256']


def check(a, status):
    return probe({**a, 'action': 'check', 'expected_status': status})['protected_status']


def revoke(session_hash):
    result = json.loads(shell('sudo -n python3 /root/hou1-test-session-db.py',
                             json.dumps({'action': 'revoke_subject', 'session_sha256': session_hash})))
    assert result['matched'] == 1 and result['changed'] >= 1
    return result['changed']


def main():
    users = api('core/users/?page_size=1000')
    assert not users.get('next')
    matches = [u for u in users['results'] if u['username'] == 'vps-staff-admin']
    assert len(matches) == 1
    user = matches[0]
    assert user['type'] == 'internal' and user['email'].endswith('@example.invalid') and user['is_active']
    path = 'core/users/' + str(user['pk']) + '/'
    original = {'is_active': user['is_active'], 'groups': user['groups']}
    groups = api('core/groups/?page_size=1000')
    assert not groups.get('next')
    role_ids = {g['pk'] for g in groups['results'] if g['name'] in {
        'pearland-paws-staff-admin', 'pearland-paws-staff-editor', 'pearland-paws-staff-reader'}}
    assert len(role_ids) == 3 and role_ids.intersection(user['groups'])
    recovery = """import json,subprocess
request=""" + repr({'path': path, 'method': 'PATCH', 'body': original}) + """
subprocess.run(['python3','/root/hou1-test-auth-api.py'],input=json.dumps(request),text=True,capture_output=True,check=True)
"""
    shell("sudo -n sh -c 'umask 077; cat > " + REMOTE + "'", recovery)
    shell('sudo -n systemd-run --collect --unit=' + UNIT + ' --on-active=5m /usr/bin/python3 ' + REMOTE)
    sessions = []
    report = {'environment': 'test', 'results': [], 'automatic_idp_session_revocation': False}
    try:
        control, _ = retain('reader-control', reader=True)
        sessions.append(control)
        a, h = retain('disable-a')
        b, _ = retain('disable-b')
        sessions.extend([a, b])
        api(path, 'PATCH', {'is_active': False})
        assert api(path)['is_active'] is False
        disabled = probe({**args('disabled-new-login'), 'action': 'start', 'expect_authorization_denied': True})
        assert disabled['authorization_denied']
        assert check(a, 200) == check(b, 200) == check(control, 200) == 200
        report['results'].append({'case': 'idp_disable', 'fresh_login_denied': True,
                                  'existing_two_sessions_status': 200,
                                  'finding': 'IdP disable alone does not revoke existing app sessions'})
        changed = revoke(h)
        assert changed >= 2
        assert check(a, 401) == check(b, 401) == 401
        assert check(control, 200) == 200
        report['results'].append({'case': 'operator_revoke_disabled_subject',
                                  'both_sessions_status': 401, 'other_subject_status': 200,
                                  'revoked_rows': changed})
        api(path, 'PATCH', original)
        c, hc = retain('role-removal')
        sessions.append(c)
        api(path, 'PATCH', {'groups': [g for g in user['groups'] if g not in role_ids]})
        assert not role_ids.intersection(api(path)['groups'])
        fresh = args('without-staff-role')
        probe({**fresh, 'action': 'start'})
        denied = probe({**fresh, 'action': 'finish', 'expect': 'rejected'})
        assert denied['protected_status'] == 401
        assert check(c, 200) == 200
        report['results'].append({'case': 'idp_staff_group_removal', 'fresh_login_status': 401,
                                  'existing_session_status': 200,
                                  'finding': 'Existing app session retains its role snapshot until revocation or expiry'})
        revoke(hc)
        assert check(c, 401) == 401 and check(control, 200) == 200
        report['results'].append({'case': 'operator_revoke_removed_role',
                                  'existing_session_status': 401, 'other_subject_status': 200})
    finally:
        api(path, 'PATCH', original)
        actual = api(path)
        assert actual['is_active'] == original['is_active'] and sorted(actual['groups']) == sorted(original['groups'])
        shell('sudo -n systemctl stop ' + UNIT + '.timer')
        shell('sudo -n rm ' + REMOTE)
        for session in sessions:
            probe({**session, 'action': 'cleanup'})
        report['fixture_restored'] = True
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
    for reader in (False, True):
        positive = args('restored-login', reader)
        probe({**positive, 'action': 'start'})
        assert probe({**positive, 'action': 'finish', 'reader_write': reader})['protected_status'] == 200
    report['final_admin_reader_logins'] = 'passed'
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
