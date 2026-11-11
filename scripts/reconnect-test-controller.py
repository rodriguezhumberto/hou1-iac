#!/usr/bin/env python3
"""Reconnect only the disposable test VPN container, preserving split DNS."""
import argparse
import json
import re
import shlex
import subprocess
import time

CONTAINER = 'hou1-vps-admin-test'
REMOTE = r'''
from pathlib import Path
import ipaddress,json,socket,subprocess,sys,urllib.parse
RESOLVER = Path('/etc/resolv.conf')
RECEIPT = Path('/var/lib/hou1-test-controller-resolver.json')
HOSTS = Path('/etc/hosts')
BEGIN = '# BEGIN HOU1 TEST CONTROLLER DNS'
END = '# END HOU1 TEST CONTROLLER DNS'
PRIVATE_HOSTS = {'staff-test.vps-test.pearlandpaws.com':'100.91.0.10',
                 'test.vps-test.pearlandpaws.com':'100.91.0.10',
                 'grafana-test.vps-test.pearlandpaws.com':'100.91.0.22'}

def pin_hosts(hosts):
    original = HOSTS.read_text()
    lines, managed = [], False
    for line in original.splitlines(keepends=True):
        if line.rstrip('\r\n') == BEGIN:
            assert not managed
            managed = True
        elif line.rstrip('\r\n') == END:
            assert managed
            managed = False
        elif not managed:
            lines.append(line)
    assert not managed
    prefix = '' if not hosts else '\n'.join([BEGIN, *(address+' '+host for host,address in sorted(hosts.items())), END, ''])
    HOSTS.write_text(prefix+''.join(lines))

def prepare():
    if RECEIPT.exists():
        original = json.loads(RECEIPT.read_text())['original']
    else:
        original = RESOLVER.read_text()
        assert '# HOU1 dedicated test controller DNS' not in original
        with RECEIPT.open('x') as output:
            output.write(json.dumps({'original':original})+'\n')
        RECEIPT.chmod(0o600)
    # Resolve the public management name before the VPN's local DNS starts.
    RESOLVER.write_text(original)
    pin_hosts({})
    return original

def configure():
    p = subprocess.run(['netbird','status','--json'],capture_output=True,text=True,timeout=10)
    if p.returncode:
        return {'ready':False}
    state = json.loads(p.stdout)
    assert urllib.parse.urlsplit(state['management']['url']).hostname == 'netbird-test.vps-test.pearlandpaws.com'
    if not state['management']['connected'] or not any(
        server.get('enabled') and not server.get('error') and
        'staff-test.vps-test.pearlandpaws.com' in server.get('domains', [])
        for server in state.get('dnsServers', [])):
        return {'ready':False}
    address = str(ipaddress.ip_interface(state['netbirdIp']).ip)
    assert address == '100.91.205.101', 'Not the dedicated test administration peer'
    # This bare musl container has no OS per-domain DNS manager. Its local
    # NetBird resolver serves split domains, not general public recursion.
    # Verify those records directly; keep normal public DNS for Authentik.
    for host, expected in PRIVATE_HOSTS.items():
        result = subprocess.run(['nslookup','-type=A',host,address],capture_output=True,text=True,timeout=10)
        if result.returncode:
            return {'ready':False}
        answers = {line.split(':',1)[1].strip() for line in result.stdout.splitlines()
                   if line.startswith('Address:') and '#' not in line}
        assert answers == {expected}, 'Unexpected private DNS answer'
    try:
        pin_hosts(PRIVATE_HOSTS)
        for host, expected in PRIVATE_HOSTS.items():
            answers = {i[4][0] for i in socket.getaddrinfo(host,443,family=socket.AF_INET,type=socket.SOCK_STREAM)}
            assert answers == {expected}, 'Private hostname resolution failed'
        socket.getaddrinfo('auth-test.vps-test.pearlandpaws.com',443,type=socket.SOCK_STREAM)
    except BaseException:
        pin_hosts({})
        raise
    return {'ready':True,'dedicated_client_reconnected':True,'private_dns_answers_verified':True,'public_dns_retained':True}

if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare()
        print(json.dumps({'prepared':True}))
    else:
        assert sys.argv[1] == 'configure'
        print(json.dumps(configure()))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-host', required=True)
    args = parser.parse_args()
    assert re.fullmatch(r'[A-Za-z0-9_.@:-]+', args.ssh_host) and not args.ssh_host.startswith('-')
    def remote(argv):
        return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',args.ssh_host,
                               shlex.join(argv)],capture_output=True,text=True,check=True,timeout=30).stdout
    def configure(operation):
        return json.loads(remote(['docker','exec','-i',CONTAINER,'python3','-c',REMOTE,operation]))
    configure('prepare')
    remote(['docker','restart',CONTAINER])
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        report = configure('configure')
        if report['ready']:
            print(json.dumps(report))
            return
        time.sleep(1)
    raise TimeoutError('Dedicated test VPN DNS did not become ready')


if __name__ == '__main__':
    main()
