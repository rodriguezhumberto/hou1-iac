"""Install the unpublished, temporary fault proxy after saving original ingress bytes."""

from controller import shell
from pathlib import Path
import json

command = (
    """sudo -n python3 - <<'REMOTE'
from pathlib import Path
import subprocess,time,urllib.request
root=Path('/root/hou1-security-proxy')
conf=Path('/opt/hou1-netbird/ingress/authentik-test.conf')
backup=root/'ingress.original'
assert not backup.exists(), 'existing drill backup: recover before starting another drill'
backup.write_bytes(conf.read_bytes())
(root/'control.json').write_text('{}')
image=subprocess.run(['docker','inspect','hou1-authentik-test-authentik-server-1','--format','{{.Config.Image}}'],capture_output=True,text=True,check=True).stdout.strip()
subprocess.run(['docker','run','-d','--name','hou1-oidc-security-proxy','--network','hou1-authentik-test','--network-alias','oidc-security-proxy','--user','0','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','--cpus','0.5','--pids-limit','32','--mount','type=bind,src='+str(root)+',dst=/state','--entrypoint','python',image,'/state/proxy.py','--host','auth-test.vps-test.pearlandpaws.com','--client-id','pearland-paws-staff-test','--state','/state'],check=True,stdout=subprocess.DEVNULL)
block="""
    + repr("""    # TEMPORARY issue #26 token fault-injection route. Remove after drill.
    location = /application/o/token/ {
        set $security_proxy oidc-security-proxy:9080;
        proxy_pass http://$security_proxy;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
    }

""")
    + """
try:
 conf.write_text(conf.read_text().replace('    # OIDC discovery',block+'    # OIDC discovery'))
 subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-t'],check=True,capture_output=True)
 subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
except BaseException:
 conf.write_bytes(backup.read_bytes())
 subprocess.run(['docker','exec','pearlandpaws-test-nginx-1','nginx','-s','reload'],capture_output=True)
 subprocess.run(['docker','rm','-f','hou1-oidc-security-proxy'],capture_output=True)
 backup.unlink()
 raise
print('temporary token route installed; original ingress saved')
REMOTE"""
)
print(shell(command))
