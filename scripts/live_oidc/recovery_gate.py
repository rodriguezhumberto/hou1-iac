"""Phased outage/fresh-restore assertions for the approved isolated VPS only.

No NetBird restart, issuer swap, production mutation or controller-key upload.
Use HOU1_SECURITY_WORKDIR and run prepare, outage, restore, finish in order.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import urllib.request
import uuid

from controller import AUTH, ROOT, api, probe, secrets, shell

REMOTE = '/root/hou1-oidc-recovery'
UNIT = 'hou1-oidc-restore-guardian'
REPORT = ROOT / 'recovery-report.json'


def record(report):
    report['updated_at'] = datetime.now(timezone.utc).isoformat()
    REPORT.write_text(json.dumps(report, indent=2) + '\n')


def identity():
    with urllib.request.urlopen(AUTH + '/application/o/pearland-paws-staff-test/.well-known/openid-configuration', timeout=20) as r:
        discovery = json.load(r)
    assert discovery['issuer'] == AUTH + '/application/o/pearland-paws-staff-test/'
    assert discovery['jwks_uri'] == discovery['issuer'] + 'jwks/'
    with urllib.request.urlopen(discovery['jwks_uri'], timeout=20) as r:
        keys = json.load(r)
    users = api('core/users/?page_size=1000')
    assert not users['pagination'].get('next')
    stable = sorted((u['uuid'], u['is_active'], sorted(u['groups'])) for u in users['results'])
    return {'issuer': discovery['issuer'], 'jwks_sha256': hashlib.sha256(
        json.dumps(keys, sort_keys=True).encode()).hexdigest(),
        'identity_access_sha256': hashlib.sha256(json.dumps(stable).encode()).hexdigest()}


def args(name, reader=False):
    return {'run_id': 'recovery-' + name + '-' + uuid.uuid4().hex[:8],
            'username': 'vps-staff-reader' if reader else 'vps-staff-admin',
            'password': secrets()['TEST_READER_PASSWORD' if reader else 'TEST_ADMIN_PASSWORD']}


def positive(reader=False):
    a = args('positive', reader)
    probe({**a, 'action': 'start'})
    r = probe({**a, 'action': 'finish', 'reader_write': reader})
    assert r['protected_status'] == 200 and r['callback_replay_rejected'] and r['logout_revoked']
    return r


def prepare():
    assert not REPORT.exists(), 'Use a new private workdir for each recovery drill'
    assert ROOT.stat().st_mode & 0o077 == 0
    report = {'environment': 'test', 'started_at': datetime.now(timezone.utc).isoformat(),
              'before': identity(), 'baseline_admin': positive(), 'baseline_reader': positive(True)}
    shell('sudo -n test ! -e ' + REMOTE)
    shell('sudo -n mkdir -m 0700 ' + REMOTE)
    worker = Path(__file__).with_name('recovery_worker.py').read_text()
    shell('sudo -n tee ' + REMOTE + '/worker.py >/dev/null', worker)
    shell('sudo -n chmod 0700 ' + REMOTE + '/worker.py')
    # backup.sh uses the already configured host recovery recipient/identity.
    shell('sudo -n sh -c ' + shlex.quote('umask 077; /opt/hou1-authentik/test/backup.sh > ' + REMOTE + '/backup.log 2>&1'))
    info = json.loads(shell("sudo -n python3 - <<'PY'\nimport hashlib,json,pathlib,shutil\nr=pathlib.Path('/root/hou1-oidc-recovery'); candidates=sorted(pathlib.Path('/var/backups/hou1-authentik/test').glob('authentik-*.tar.gz.age.success'))\np=pathlib.Path(str(candidates[-1])[:-len('.success')]); expected=pathlib.Path(str(p)+'.sha256').read_text().split()[0]\nassert hashlib.sha256(p.read_bytes()).hexdigest()==expected\nshutil.copyfile(p,r/'controller-backup.tar.gz.age'); shutil.copyfile(str(p)+'.sha256',r/'controller-backup.tar.gz.age.sha256')\nprint(json.dumps({'backup_id':p.name,'sha256':expected,'bytes':p.stat().st_size}))\nPY"))
    encrypted = ROOT / info['backup_id']
    # Bytes stay captured; only the encrypted archive is stored on the controller.
    from controller import SSH
    raw = subprocess.run(SSH + ['sudo -n cat ' + REMOTE + '/controller-backup.tar.gz.age'], capture_output=True, check=True, timeout=60).stdout
    assert hashlib.sha256(raw).hexdigest() == info['sha256']
    encrypted.write_bytes(raw)
    encrypted.chmod(0o600)
    (ROOT / (info['backup_id'] + '.sha256')).write_text(info['sha256'] + '  ' + info['backup_id'] + '\n')
    decrypted = subprocess.run(['age', '--decrypt', '--identity', '/var/tmp/hou1-vps-test/gates/host-age.key', str(encrypted)],
                               capture_output=True, check=True, timeout=60).stdout
    with tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:gz') as archive:
        members = set(archive.getnames())
        assert members == {'postgresql.sql', 'persistent.tar', 'compose.yaml', 'authentik.env', 'oidc-clients.env', 'authentik-secrets.enc.yaml'}
        assert archive.getmember('postgresql.sql').size > 0
    del decrypted
    # Upload the controller copy back and verify it, proving restore source is off-VPS.
    result = subprocess.run(SSH + ['sudo -n tee ' + REMOTE + '/controller-backup.tar.gz.age >/dev/null'], input=encrypted.read_bytes(), capture_output=True, timeout=60)
    assert result.returncode == 0
    shell('sudo -n systemd-run --collect --unit=' + UNIT + ' --on-active=20m /usr/bin/python3 ' + REMOTE + '/worker.py recover')
    report['backup'] = {**info, 'off_vps_checksum_and_decryption': 'passed', 'controller_key_uploaded': False}
    report['guardian_armed'] = True
    record(report)
    return report


def outage(report):
    assert report['guardian_armed'] and 'outage' not in report
    a = args('inflight')
    probe({**a, 'action': 'start'})
    shell('sudo -n docker compose -p hou1-authentik-test -f /opt/hou1-authentik/test/compose.yaml stop authentik-server authentik-worker')
    try:
        denied = probe({**a, 'action': 'finish', 'expect': 'rejected'})
        fresh = probe({**args('fresh'), 'action': 'start', 'expect_provider_outage': True})
        assert denied['protected_status'] == fresh['protected_status'] == 401
        shell('sudo -n true')  # Fresh pinned SSH connection while IdP is down.
        owner = subprocess.run(['python3', '/var/tmp/hou1-vps-test/check-local-owner.py'], capture_output=True, timeout=90)
        assert owner.returncode == 0, 'Independent NetBird local owner failed'
        # Reuse the management PAT from the current rebuilt deployment in memory.
        token = json.loads(subprocess.run(['sops','--decrypt','--output-type','json',
            '/var/tmp/hou1-vps-test/rebuild/netbird-token.enc.yaml'],
            env={**os.environ,'SOPS_AGE_KEY_FILE':'/var/tmp/hou1-vps-test/test-age.key'},
            capture_output=True,check=True,timeout=30).stdout)
        assert token['netbird_management_url'] == 'https://netbird-test.vps-test.pearlandpaws.com'
        request = urllib.request.Request(token['netbird_management_url']+'/api/peers',
                    headers={'Authorization':'Token '+token['netbird_api_token']})
        with urllib.request.urlopen(request, timeout=20) as response:
            assert response.status == 200 and isinstance(json.load(response), list)
        report['outage'] = {'inflight_callback': denied, 'fresh_login': fresh,
                            'private_ssh': 'passed', 'netbird_local_owner_pkce_api': 'passed', 'netbird_pat': 'passed'}
        record(report)
    finally:
        # Recover independently even when a negative assertion fails.
        shell('sudo -n systemd-run --collect --unit=hou1-oidc-start-after-outage /usr/bin/docker compose -p hou1-authentik-test -f /opt/hou1-authentik/test/compose.yaml up -d --wait --wait-timeout 600')
    return report


def restore(report):
    assert 'outage' in report and 'restore_started_at' not in report
    report['restore_started_at'] = datetime.now(timezone.utc).isoformat()
    record(report)
    shell('sudo -n systemd-run --collect --unit=hou1-oidc-fresh-restore /usr/bin/python3 ' + REMOTE + '/worker.py restore')
    return {'fresh_restore_started': True, 'guardian_armed': True}


def finish(report):
    shell('sudo -n test -f ' + REMOTE + '/restore-command-passed')
    assert identity() == report['before'], 'Issuer/signing keys changed across restore'
    report['restored_admin'] = positive()
    report['restored_reader'] = positive(True)
    report['restore'] = {'fresh_database_and_config': 'passed', 'issuer_and_signing_keys': 'unchanged',
                         'restore_source': 'verified off-VPS encrypted copy'}
    shell('sudo -n touch ' + REMOTE + '/completed')
    shell('sudo -n systemctl stop ' + UNIT + '.timer')
    report['guardian_disarmed_after_assertions'] = True
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    record(report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('prepare','outage','restore','finish','recover'))
    phase = parser.parse_args().phase
    if phase == 'prepare':
        result = prepare()
    elif phase == 'recover':
        shell('sudo -n systemd-run --collect --unit=hou1-oidc-manual-recovery /usr/bin/python3 ' + REMOTE + '/worker.py recover')
        result = {'original_state_recovery_started': True}
    else:
        report = json.loads(REPORT.read_text())
        assert report['environment'] == 'test'
        result = {'outage':outage,'restore':restore,'finish':finish}[phase](report)
    print(json.dumps(result), flush=True)
