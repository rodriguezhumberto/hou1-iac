#!/usr/bin/env python3
"""Assert known production/default-denied egress from host and workload namespaces.

Private config carries destination addresses. Public report contains only counts,
rule counters and test container names, never production hosts or credentials.
"""

import argparse
import ipaddress
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--config", required=True)
a = p.parse_args()
path = Path(a.config)
c = json.loads(path.read_text())
assert c["environment"] == "test"
remote = r'''
import json,subprocess,socket,sys
c=json.load(sys.stdin)
for address in c['targets']:socket.inet_pton(socket.AF_INET6 if ':' in address else socket.AF_INET,address)
def counters():
 x=json.loads(subprocess.run(['nft','--json','list','table','inet','hou1_test_isolation'],capture_output=True,text=True,check=True).stdout)
 result={}
 for item in x['nftables']:
  if 'rule' not in item:continue
  r=item['rule'];result.setdefault(r['chain'],0)
  if not any('reject' in e or 'drop' in e for e in r['expr']):continue
  result[r['chain']]+=sum(e['counter']['packets'] for e in r['expr'] if 'counter' in e)
 return result
before=counters()
for address in c['targets']:
 try:socket.create_connection((address,443),timeout=2)
 except OSError:pass
 else:raise AssertionError('host production destination reachable')
after=counters();assert after['host_output']-before['host_output']>=len(c['targets'])
# A benchmark-only address has no external service; rule counters prove the rejection.
start=counters()
try:socket.create_connection(('198.18.0.1',443),timeout=2)
except OSError:pass
else:raise AssertionError('unapproved egress allowed')
assert counters()['host_output']>start['host_output']
probe="""import json,socket,sys
x=json.load(sys.stdin);result={}
for name,address in x.items():
 try:socket.create_connection((address,443),timeout=2)
 except OSError as e:result[name]={'blocked':True,'errno':e.errno}
 else:raise AssertionError('workload egress reachable')
from pathlib import Path
routes=Path('/proc/net/route').read_text().splitlines()[1:]
print(json.dumps({'attempts':result,'ipv4_default_route_present':any(line.split()[1]=='00000000' for line in routes)}))
"""
image=subprocess.run(['docker','inspect','hou1-authentik-test-authentik-server-1','--format','{{.Config.Image}}'],capture_output=True,text=True,check=True).stdout.strip()
reports=[]
for container in c['containers']:
 assert container.startswith(('hou1-authentik-test-','hou1-netbird-test-','pearlandpaws-test-'))
 start=counters()
 request={'production_ipv4':next(a for a in c['targets'] if ':' not in a),'production_ipv6':next(a for a in c['targets'] if ':' in a),'unapproved_ipv4':'198.18.0.1'}
 r=subprocess.run(['docker','run','--rm','-i','--network','container:'+container,'--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','96m','--pids-limit','32','--entrypoint','python',image,'-c',probe],input=json.dumps(request),capture_output=True,text=True,check=True)
 namespace=json.loads(r.stdout);result=namespace['attempts'];end=counters()
 hit_firewall=end['container_forward']-start['container_forward']>=2
 no_route=not namespace['ipv4_default_route_present'] and all(result[k]['errno']==101 for k in ('production_ipv4','unapproved_ipv4'))
 assert hit_firewall or no_route,'IPv4 evidence '+json.dumps({'container':container,'namespace':namespace,'before':start,'after':end})
 assert all(v['blocked'] for v in result.values())
 reports.append({'container':container,'enforcement':'nftables' if hit_firewall else 'internal network without external route','checks':result,'forward_counter_increase':end['container_forward']-start['container_forward']})
print(json.dumps({'environment':'test','host_ipv4_targets':sum(':' not in t for t in c['targets']),'host_ipv6_targets':sum(':' in t for t in c['targets']),'host_targets_denied':True,'host_deny_counter_increase':after['host_output']-before['host_output'],'unapproved_host_egress_denied':True,'workloads':reports,'ipv6_forwarding':subprocess.run(['sysctl','-n','net.ipv6.conf.all.forwarding'],capture_output=True,text=True,check=True).stdout.strip()}))
'''
r = subprocess.run(
    c["ssh"] + ["sudo -n python3 -c " + __import__("shlex").quote(remote)],
    input=json.dumps(
        {
            "targets": c["isolation_probe_targets"],
            "containers": c["isolation_probe_containers"],
        }
    ),
    capture_output=True,
    text=True,
)
if r.returncode:
    raise RuntimeError("private isolation assertion failed: " + r.stderr[-1500:])
report = json.loads(r.stdout)
out = path.parent / "isolation-drill-report.json"
out.write_text(json.dumps(report, indent=2))
out.chmod(0o600)
print(
    json.dumps(
        {
            "host_targets_denied": report["host_targets_denied"],
            "workload_namespaces_verified": len(report["workloads"]),
        }
    )
)
