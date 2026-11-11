#!/usr/bin/python3
"""Fixed root TLS receiver. The renewal account cannot choose paths or commands."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import tempfile
import time

from hou1_tls_material import MAX_BYTES, run, validate_bundle, validate_lineages

CONFIG = Path('/etc/hou1-tls/receiver.json')
STATE = Path('/var/lib/hou1-tls')


def private_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.tls-', delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(),0o600)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def read_config():
    if os.geteuid() != 0 or CONFIG.is_symlink() or CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o077:
        raise PermissionError('Root-owned private receiver configuration required')
    c = json.loads(CONFIG.read_text())
    validate_lineages(c['lineages'])
    for key in ('certificate_directory','app_directory'):
        path = Path(c[key])
        if not path.is_absolute() or not re.fullmatch(r'/[A-Za-z0-9_/-]+',str(path)):
            raise ValueError('Invalid fixed receiver root')
    if (c['certificate_directory'] != c['app_directory']+'/certbot/conf'
            or c['environment'] not in ('test','production')):
        raise ValueError('Invalid receiver contract')
    return c


def nginx(c, *args):
    return run(['docker','compose','--project-directory',c['app_directory'],'exec','-T','nginx','nginx',*args])


def served(c, metadata):
    context = ssl.create_default_context()
    for name, item in c['lineages'].items():
        for host in item['probe_hosts']:
            with socket.create_connection(('127.0.0.1',443), timeout=10) as raw:
                with context.wrap_socket(raw,server_hostname=host) as connection:
                    if hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest() != metadata[name]['sha256']:
                        raise ValueError('Served certificate differs from the approved material')


def snapshot(path, root):
    if path.is_symlink():
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Certificate symlink escapes its fixed root')
        return ('link',os.readlink(path))
    if path.exists():
        if not path.is_file():raise ValueError('Certificate path must be a regular file')
        return ('file',path.read_bytes())
    return ('absent',None)


def install(c, bundle, bootstrap=False):
    metadata = validate_bundle(bundle,c['environment'],c['lineages'])
    root = Path(c['certificate_directory'])
    if root.is_symlink() or (root/'live').is_symlink():
        raise ValueError('Certificate directories cannot be symlinks')
    paths = []
    for name, item in bundle['certificates'].items():
        folder = root/'live'/name
        if folder.is_symlink():raise ValueError('Certificate lineage cannot be a symlink')
        for filename, key in [('fullchain.pem','fullchain'),('privkey.pem','private_key')]:
            path = folder/filename
            paths.append((path,item[key].encode(),snapshot(path,root)))
    initial = bootstrap and all(previous[0]=='absent' for _,_,previous in paths)
    if not initial:
        nginx(c,'-t')
    changed = any(not path.is_file() or path.read_bytes()!=data for path,data,_ in paths)
    try:
        if changed:
            for path,data,_ in paths:
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                private_write(path,data)
        if not initial:
            nginx(c,'-t')
            if changed:nginx(c,'-s','reload')
            # Nginx reload is asynchronous; retry only verification, never installation.
            for attempt in range(10):
                try:served(c,metadata);break
                except Exception:
                    if attempt==9:raise
                    time.sleep(0.2)
    except Exception:
        if changed:
            for path,_,previous in reversed(paths):
                kind,data = previous
                if kind=='link':
                    path.unlink(missing_ok=True);path.symlink_to(data)
                elif kind=='file':private_write(path,data)
                else:path.unlink(missing_ok=True)
            nginx(c,'-t');nginx(c,'-s','reload')
        raise RuntimeError('TLS deployment failed; previous certificate files restored') from None
    return {'passed':True,'changed':changed,'bootstrap_only':initial,
            'verified_served_certificate':not initial,
            'sha256':{name:item['sha256'] for name,item in metadata.items()}}


def publish_status(c, data, now=None):
    now = time.time() if now is None else now
    if set(data) != {'environment','last_success','last_run_success','observed_at'} or data['environment'] != c['environment']:
        raise ValueError('Renewal status belongs to another environment')
    for key in ('last_success','observed_at'):
        if type(data[key]) not in (int,float) or not math.isfinite(data[key]) or not 0 <= data[key] <= now+30:
            raise ValueError('Invalid renewal timestamp')
    if (type(data['last_run_success']) is not int or data['last_run_success'] not in (0,1)
            or abs(now-data['observed_at']) > 120 or data['last_success'] > data['observed_at']):
        raise ValueError('Stale or invalid renewal status')
    status=STATE/'controller-status.json'
    if status.exists():
        previous=json.loads(status.read_text())
        if previous.get('environment')!=c['environment'] or data['observed_at']<previous['observed_at']:
            raise ValueError('Renewal status cannot move backwards or cross environments')
    expiries=[]
    for name in c['lineages']:
        path=Path(c['certificate_directory'])/'live'/name/'fullchain.pem'
        date=run(['openssl','x509','-in',str(path),'-noout','-enddate']).decode().strip().split('=',1)[1]
        expiries.append(ssl.cert_time_to_seconds(date))
    data={**data,'certificate_not_after':min(expiries)}
    private_write(STATE/'controller-status.json',json.dumps(data).encode())
    return {'passed':True}


def main():
    c=read_config()
    if sys.argv[1:] not in ([],['--bootstrap'],['--metrics']):raise ValueError('Unknown receiver action')
    payload=sys.stdin.buffer.read(MAX_BYTES+1)
    if len(payload)>MAX_BYTES:raise ValueError('Certificate payload too large')
    data=json.loads(payload)
    with open(STATE/'receiver.lock','a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt=publish_status(c,data) if sys.argv[1:]==['--metrics'] else install(c,data,sys.argv[1:]==['--bootstrap'])
    print(json.dumps(receipt))


if __name__=='__main__':
    try:main()
    except Exception:
        print('TLS receiver failed; private diagnostics withheld.',file=sys.stderr)
        raise SystemExit(1)
