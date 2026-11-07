"""Root-side recovery for the isolated overlap fixture; no controller RPC needed."""

import json
from pathlib import Path
import subprocess

ROOT = Path('/root/hou1-jwks-security')
INGRESS = Path('/opt/hou1-netbird/ingress/authentik-test.conf')
AUTH_REQUEST = '''
import json,os,sys,urllib.request
r=json.load(sys.stdin)
req=urllib.request.Request('http://127.0.0.1:9000/api/v3/providers/oauth2/1/',
 data=json.dumps({'signing_key':r['original_key']}).encode(),method='PATCH',
 headers={'Authorization':'Bearer '+os.environ['AUTHENTIK_BOOTSTRAP_TOKEN'],
          'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=20) as response:
 assert json.load(response)['signing_key']==r['original_key']
'''


def recover(root=ROOT, ingress=INGRESS):
    state = json.loads((root / 'restore.json').read_text())
    failures = []

    def attempt(command, data=None):
        try:
            result = subprocess.run(command, input=data, text=True, capture_output=True, timeout=40)
            if result.returncode:
                failures.append('recovery_step_failed')
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            failures.append('recovery_step_failed')
            return False

    # Independently restore ingress even when Authentik is unavailable.
    attempt(['docker', 'exec', '-i', 'hou1-authentik-test-authentik-server-1',
             'python', '-c', AUTH_REQUEST], json.dumps(state))
    backup = root / 'ingress.original'
    if backup.exists():
        try:
            ingress.write_bytes(backup.read_bytes())
            if attempt(['docker', 'exec', 'pearlandpaws-test-nginx-1', 'nginx', '-t']):
                attempt(['docker', 'exec', 'pearlandpaws-test-nginx-1', 'nginx', '-s', 'reload'])
        except OSError:
            failures.append('ingress_restore_failed')
    attempt(['docker', 'rm', '-f', 'hou1-jwks-security-proxy'])
    if failures:
        raise RuntimeError('Overlap recovery incomplete; inspect privately')


if __name__ == '__main__':
    recover()
