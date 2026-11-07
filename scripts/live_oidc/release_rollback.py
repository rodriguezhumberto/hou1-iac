"""Roll back only the test staff image, then restore the exact candidate image.

The prior test image must include persistent subject blocking. A five-minute
host timer rolls forward independently if the controller disconnects. No data
restore, production change or NetBird restart is performed.
"""

import json
import shlex

from controller import ROOT, shell
from recovery_gate import positive

RECOVERY = '/root/hou1-issue37-release-recover.py'
UNIT = 'hou1-issue37-release-guardian'
OLD = 'pearlandpaws-test-offboarding:2f133f32aae5656a4dea1b2649b9af166ff16aa9'
BASE = ['docker', 'compose', '-p', 'pearlandpaws-test',
        '-f', '/opt/pearlandpaws-test/docker-compose.yml',
        '-f', '/opt/pearlandpaws-test/docker-compose.override.yml',
        '-f', '/opt/pearlandpaws-test/issue37-security.override.yml']
UP = ['up', '-d', '--no-deps', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '120', 'astro-app']


def remote(code):
    return shell(shlex.join(['sudo', '-n', 'python3', '-c', code]))


def inspect():
    code = '''import json,subprocess
x=json.loads(subprocess.check_output(['docker','inspect','pearlandpaws-test-astro-app-1']))[0]
env=dict(v.split('=',1) for v in x['Config']['Env'])
assert env['STAFF_ENVIRONMENT']=='test'
assert env['STAFF_OIDC_ISSUER']=='https://auth-test.vps-test.pearlandpaws.com/application/o/pearland-paws-staff-test/'
assert x['Config']['Labels']['io.pearlandpaws.staff-offboarding-schema']=='1'
assert x['State']['Health']['Status']=='healthy'
print(json.dumps({'image_id':x['Image'],'revision':x['Config']['Labels'].get('org.opencontainers.image.revision')}))
'''
    return json.loads(remote(code))


def main():
    before = inspect()
    old_code = '''import subprocess,json
x=json.loads(subprocess.check_output(''' + repr(['docker','image','inspect',OLD]) + '''))[0]
labels=x['Config']['Labels']
assert labels['io.pearlandpaws.staff-offboarding-schema']=='1'
assert labels['org.opencontainers.image.revision']=='2f133f32aae5656a4dea1b2649b9af166ff16aa9'
print(json.dumps({'image_id':x['Id'],'revision':labels['org.opencontainers.image.revision']}))
'''
    old = json.loads(remote(old_code))
    assert old['image_id'] != before['image_id']
    recovery = '''import subprocess,pathlib
subprocess.run('''+repr(BASE+UP)+''',check=True,capture_output=True,timeout=150)
for args in [['nginx','-t'],['nginx','-s','reload']]:
 subprocess.run(['docker','exec','pearlandpaws-test-nginx-1',*args],check=True,capture_output=True,timeout=30)
p=pathlib.Path('/opt/pearlandpaws-test/issue37-rollback.override.yml')
p.unlink(missing_ok=True)
'''
    shell('sudo -n tee '+RECOVERY+' >/dev/null', recovery)
    shell('sudo -n chmod 0700 '+RECOVERY)
    shell('sudo -n systemd-run --collect --unit='+UNIT+' --on-active=5m /usr/bin/python3 '+RECOVERY)
    report = {'environment':'test','candidate':before,'rollback_image':old}
    try:
        code='''import pathlib,subprocess
p=pathlib.Path('/opt/pearlandpaws-test/issue37-rollback.override.yml'); assert not p.exists()
p.write_text('''+repr('services:\n  astro-app:\n    image: '+OLD+'\n')+'''); p.chmod(0o600)
subprocess.run('''+repr(BASE+['-f','/opt/pearlandpaws-test/issue37-rollback.override.yml']+UP)+''',check=True,capture_output=True,timeout=150)
for args in [['nginx','-t'],['nginx','-s','reload']]:
 subprocess.run(['docker','exec','pearlandpaws-test-nginx-1',*args],check=True,capture_output=True,timeout=30)
'''
        remote(code)
        assert inspect()['image_id'] == old['image_id']
        report['rollback_admin'] = positive()
        report['rollback_reader'] = positive(True)
    finally:
        shell('sudo -n python3 '+RECOVERY)
        assert inspect() == before
        shell('sudo -n systemctl stop '+UNIT+'.timer')
        shell('sudo -n rm '+RECOVERY)
    report['candidate_restored_exactly'] = True
    report['restored_admin'] = positive()
    report['restored_reader'] = positive(True)
    (ROOT/'release-rollback-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__ == '__main__': main()
