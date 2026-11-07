"""Stage a public-key-only overlap fixture, then test real 15-minute cache expiry.

Run start, wait at least 905 seconds while doing other checks, then finish.
Always run cleanup. No app restart, clock adjustment, token fabrication or
production changes. Authentik itself still publishes only one signing key.
"""

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request
import uuid

from controller import AUTH, ROOT, container_api as api, probe, secrets, shell

STATE = ROOT / 'jwks-overlap-state.json'
REPORT = ROOT / 'jwks-overlap-report.json'
REMOTE = '/root/hou1-jwks-security'
CONTAINER = 'hou1-jwks-security-proxy'
UNIT = 'hou1-jwks-security-recovery'
PROVIDER = 'providers/oauth2/1/'
JWKS = AUTH + '/application/o/pearland-paws-staff-test/jwks/'


def publish(keys, status=200):
    # Atomic replacement prevents a concurrent JWKS fetch reading partial JSON.
    shell("sudo -n sh -c 'umask 077; cat > " + REMOTE +
          "/fixture.new; mv " + REMOTE + "/fixture.new " + REMOTE + "/fixture.json'",
          json.dumps({'keys': keys, 'status': status}))


def requests():
    raw = shell('sudo -n cat ' + REMOTE + '/requests.jsonl')
    return [json.loads(line) for line in raw.splitlines()]


def login(name, expected='accepted'):
    args = {'run_id': 'jwks-' + name + '-' + uuid.uuid4().hex[:8],
            'username': 'vps-staff-admin', 'password': secrets()['TEST_ADMIN_PASSWORD']}
    probe({**args, 'action': 'start'})
    result = probe({**args, 'action': 'finish', 'expect': expected})
    return {'case': name, 'assertion': 'passed', 'probe': result}


def upstream_keys():
    with urllib.request.urlopen(JWKS, timeout=20) as response:
        return json.load(response)['keys']


def start():
    assert not STATE.exists(), 'Complete or clean up the previous drill first'
    assert subprocess.run(['systemctl', '--user', 'is-active', '--quiet',
                           'hou1-test-monitor.timer']).returncode != 0, 'Pause the rehearsal monitor before the timed drill'
    provider = api(PROVIDER)
    assert provider['client_id'] == 'pearland-paws-staff-test'
    original = provider['signing_key']
    old = upstream_keys()
    assert len(old) == 1 and old[0]['use'] == 'sig'
    state = {'original_key': original, 'generated_key': None,
             'old_keys': old, 'results': [login('baseline')],
             'app_before': json.loads(shell("sudo -n docker inspect pearlandpaws-test-astro-app-1 --format '{{json .State}}'"))['StartedAt']}
    STATE.write_text(json.dumps(state))
    STATE.chmod(0o600)
    try:
        shell('sudo -n install -d -m 700 ' + REMOTE)
        shell("sudo -n sh -c 'umask 077; cat > " + REMOTE + "/recover.py'",
              Path(__file__).with_name('jwks_recovery.py').read_text())
        shell("sudo -n sh -c 'umask 077; cat > " + REMOTE + "/restore.json'",
              json.dumps({'original_key': original}))
        shell('sudo -n systemd-run --collect --unit=' + UNIT +
              ' --on-active=40m /usr/bin/python3 ' + REMOTE + '/recover.py')
        key = api('crypto/certificatekeypairs/generate/', 'POST', {
            'common_name': 'issue26-jwks-overlap-test', 'validity_days': 1, 'alg': 'rsa'})
        state['generated_key'] = key['pk']
        STATE.write_text(json.dumps(state))
        api(PROVIDER, 'PATCH', {'signing_key': key['pk']})
        new = upstream_keys()
        assert len(new) == 1 and new[0]['kid'] != old[0]['kid']
        # Native Authentik publication is measured before inserting the fixture.
        state['new_keys'] = new
        state['authentik_native_overlap'] = False
        api(PROVIDER, 'PATCH', {'signing_key': original})
        assert upstream_keys() == old
        STATE.write_text(json.dumps(state))
        shell("sudo -n sh -c 'umask 077; cat > " + REMOTE + "/proxy.py'",
              Path(__file__).with_name('jwks_proxy.py').read_text())
        publish(old + new)
        setup = r'''
from pathlib import Path
import json,subprocess,time
root=Path('/root/hou1-jwks-security')
conf=Path('/opt/hou1-netbird/ingress/authentik-test.conf')
assert not (root/'ingress.original').exists(), 'Previous drill requires cleanup'
assert 'jwks-security-proxy' not in conf.read_text()
assert conf.read_text().count('    # OIDC discovery')==1
(root/'ingress.original').write_bytes(conf.read_bytes())
(root/'requests.jsonl').write_text('')
image=subprocess.run(['docker','inspect','hou1-authentik-test-authentik-server-1','--format','{{.Config.Image}}'],check=True,capture_output=True,text=True).stdout.strip()
subprocess.run(['docker','run','-d','--name','hou1-jwks-security-proxy','--network','hou1-authentik-test','--network-alias','jwks-security-proxy','--user','0','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','--cpus','0.5','--pids-limit','32','--mount','type=bind,src='+str(root)+',dst=/state','--entrypoint','python',image,'/state/proxy.py'],check=True,capture_output=True)
time.sleep(2)
block=''' + repr('''    # TEMPORARY issue #26 public-key overlap fixture.
    location = /application/o/pearland-paws-staff-test/jwks/ {
        set $jwks_fixture jwks-security-proxy:9081;
        proxy_pass http://$jwks_fixture;
        proxy_set_header Host $host;
    }

''') + r'''
conf.write_text(conf.read_text().replace('    # OIDC discovery',block+'    # OIDC discovery'))
subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-t'],check=True,capture_output=True)
subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
print('Private JWKS fixture installed; no published port')
'''
        shell('sudo -n python3 -c ' + shlex.quote(setup))
        # The genuinely new kid forces a fetch even if the old cache is warm.
        api(PROVIDER, 'PATCH', {'signing_key': key['pk']})
        state['results'].append(login('new_key_refreshes_to_overlap'))
        seen = requests()
        assert len(seen) >= 1 and seen[-1]['status'] == 200
        assert set(seen[-1]['kids']) == {old[0]['kid'], new[0]['kid']}
        state['cache_started_at'] = seen[-1]['time']
        count = len(seen)
        api(PROVIDER, 'PATCH', {'signing_key': original})
        state['results'].append(login('old_key_accepted_during_overlap'))
        api(PROVIDER, 'PATCH', {'signing_key': key['pk']})
        state['results'].append(login('new_key_accepted_during_overlap'))
        assert len(requests()) == count, 'Expected overlap cache reuse'
        publish(new)
        api(PROVIDER, 'PATCH', {'signing_key': original})
        state['results'].append(login('withdrawn_old_key_still_cached'))
        assert len(requests()) == count
        api(PROVIDER, 'PATCH', {'signing_key': key['pk']})
        STATE.write_text(json.dumps(state))
        print(json.dumps({'phase': 'cache_warmed', 'natural_expiry_not_before':
                          state['cache_started_at'] + 905, 'results': state['results']}), flush=True)
    except BaseException:
        cleanup()
        raise


def finish(wait=False):
    state = json.loads(STATE.read_text())
    while wait and time.time() < state['cache_started_at'] + 905:
        time.sleep(min(30, state['cache_started_at'] + 905 - time.time()))
    elapsed = time.time() - state['cache_started_at']
    assert elapsed >= 905, 'Wait for the real 15-minute cache lifetime before finish'
    seen = requests()
    assert seen[-1]['time'] == state['cache_started_at'], 'Another probe refreshed JWKS; repeat the timed drill'
    before = len(seen)
    publish(state['new_keys'], status=503)
    state['results'].append(login('expired_cache_jwks_unavailable_fails_closed', 'rejected'))
    assert any(r['status'] == 503 for r in requests()[before:])
    publish(state['new_keys'])
    api(PROVIDER, 'PATCH', {'signing_key': state['original_key']})
    state['results'].append(login('withdrawn_old_key_rejected_after_cache_expiry', 'rejected'))
    api(PROVIDER, 'PATCH', {'signing_key': state['generated_key']})
    state['results'].append(login('current_key_accepted_after_cache_recovery'))
    started = json.loads(shell("sudo -n docker inspect pearlandpaws-test-astro-app-1 --format '{{json .State}}'"))['StartedAt']
    assert started == state['app_before']
    report = {'environment': 'test', 'authentik_native_overlap': False,
              'overlap_source': 'temporary JWKS fixture using real Authentik public keys',
              'cache_ttl_seconds': 900, 'elapsed_real_seconds': round(elapsed, 2),
              'app_not_restarted': True, 'clock_unmodified': True,
              'results': state['results'], 'jwks_requests': requests()}
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


def cleanup():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    api(PROVIDER, 'PATCH', {'signing_key': state['original_key']})
    shell('sudo -n sh -c ' + shlex.quote(
        'if test -f ' + REMOTE + '/recover.py; then python3 ' + REMOTE + '/recover.py; fi'))
    # Restore and validate direct publication before deleting the generated key.
    with urllib.request.urlopen(JWKS, timeout=20) as response:
        assert json.load(response)['keys'] == state['old_keys']
    if state['generated_key']:
        api('crypto/certificatekeypairs/' + state['generated_key'] + '/', 'DELETE')
    check = shell('sudo -n python3 -c ' + shlex.quote('''from pathlib import Path
import hashlib,json,subprocess
root=Path('/root/hou1-jwks-security')
conf=Path('/opt/hou1-netbird/ingress/authentik-test.conf')
backup=root/'ingress.original'
assert not backup.exists() or backup.read_bytes()==conf.read_bytes()
names=subprocess.run(['docker','ps','-a','--format','{{.Names}}'],check=True,capture_output=True,text=True).stdout.splitlines()
assert 'hou1-jwks-security-proxy' not in names
print(json.dumps({'ingress_restored_byte_exact':True,'ingress_sha256':hashlib.sha256(conf.read_bytes()).hexdigest(),'proxy_removed':True}))
'''))
    shell('sudo -n systemctl stop ' + UNIT + '.timer')
    shell('sudo -n rm -rf ' + REMOTE)
    STATE.rename(ROOT / 'jwks-overlap-state.completed.json')
    (ROOT / 'jwks-overlap-cleanup.json').write_text(check)
    print(check, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('start', 'finish', 'cleanup'), required=True)
    parser.add_argument('--wait', action='store_true', help='Finish after natural cache expiry; use a managed foreground process')
    args = parser.parse_args()
    phase = args.phase
    if phase == 'start':
        start()
    elif phase == 'finish':
        try:
            finish(wait=args.wait)
        finally:
            cleanup()
    else:
        cleanup()
