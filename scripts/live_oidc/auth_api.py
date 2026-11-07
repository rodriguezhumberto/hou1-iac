"""Private test RPC; keep the bootstrap token inside the Authentik container."""

import json
import subprocess
import sys

request = json.load(sys.stdin)
assert request['path'].startswith((
    'providers/oauth2/', 'propertymappings/provider/scope/',
    'crypto/certificatekeypairs/', 'core/users/', 'core/groups/', 'schema/',
))
worker = r'''
import json, os, sys, urllib.request
r = json.load(sys.stdin)
req = urllib.request.Request('http://127.0.0.1:9000/api/v3/' + r['path'],
    data=None if 'body' not in r else json.dumps(r['body']).encode(),
    method=r.get('method', 'GET'), headers={
        'Authorization': 'Bearer ' + os.environ['AUTHENTIK_BOOTSTRAP_TOKEN'],
        'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=20) as response:
    print(response.read().decode() or '{}')
'''
result = subprocess.run([
    'docker', 'exec', '-i', 'hou1-authentik-test-authentik-server-1',
    'python', '-c', worker,
], input=json.dumps(request), capture_output=True, text=True, timeout=30)
if result.returncode:
    raise SystemExit('Isolated Authentik RPC failed; response withheld')
print(result.stdout)
