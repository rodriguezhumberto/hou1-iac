"""Restore exact pre-drill ingress and remove the unpublished token proxy."""

from controller import shell

print(
    shell("""sudo -n python3 - <<'REMOTE'
from pathlib import Path
import hashlib,json,subprocess
root=Path('/root/hou1-security-proxy');backup=root/'ingress.original'
conf=Path('/opt/hou1-netbird/ingress/authentik-test.conf')
assert backup.exists(), 'recovery backup missing'
original=backup.read_bytes();conf.write_bytes(original)
subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-t'],check=True,capture_output=True)
subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
assert conf.read_bytes()==original
subprocess.run(['docker','rm','-f','hou1-oidc-security-proxy'],check=True,capture_output=True)
assert b'oidc-security-proxy' not in conf.read_bytes()
print(json.dumps({'ingress_restored_byte_exact':True,'ingress_sha256':hashlib.sha256(original).hexdigest(),'proxy_removed':True}))
# Keep a private recovery copy until the evidence is recorded; allow next drill.
backup.rename(root/'ingress.restored')
REMOTE""")
)
