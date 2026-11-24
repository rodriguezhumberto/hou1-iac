#!/usr/bin/python3
"""Fixed root TLS receiver. The renewal account cannot choose paths or commands."""
import base64
import fcntl
from contextlib import contextmanager, ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import sys
import tempfile
import time

from hou1_tls_material import MAX_BYTES, run, validate_bundle, validate_lineages

CONFIG = Path('/etc/hou1-tls/receiver.json')
STATE = Path('/var/lib/hou1-tls')
CAPTURE_LOCK = Path('/run/lock/hou1-backup-export.lock')
WRITER_INTENT = Path('/var/lib/hou1-backup/restart-intent.json')


@contextmanager
def renewal_admission(timeout=900):
    """Gateway renewals share capture admission; Ansible already holds it."""
    descriptor=os.open(CAPTURE_LOCK,os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'r+') as lock:
        info=os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():
            raise PermissionError('Invalid capture lock')
        deadline=time.monotonic()+timeout
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise TimeoutError('Capture still owns renewal admission')
                time.sleep(min(0.2,max(0,deadline-time.monotonic())))
        if WRITER_INTENT.exists():raise RuntimeError('Interrupted writers require recovery before renewal')
        yield


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_write(path, data, metadata=None):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.tls-', delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if metadata is not None:
                mode, uid, gid = metadata
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (uid, gid):
                    os.fchown(stream.fileno(), uid, gid)
                os.fchmod(stream.fileno(), mode)
            else:
                os.fchmod(stream.fileno(),0o600)
            os.fsync(stream.fileno())
            temp.replace(path)
            sync_directory(path.parent)
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
    ingress_command(c)
    return c


def ingress_command(c):
    selector = c.get('ingress_compose')
    if selector is None and 'ingress_compose' not in c:
        # Existing root-owned receiver configurations keep their original target.
        return ['docker', 'compose', '--project-directory', c['app_directory']]
    if not isinstance(selector, dict) or set(selector) != {'directory', 'project', 'file'}:
        raise ValueError('Invalid ingress Compose selector')
    directory, project, filename = (selector[key] for key in ('directory', 'project', 'file'))
    if (not all(isinstance(value, str) for value in (directory, project, filename))
            or not re.fullmatch(r'/[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*', directory)
            or (project and not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project))
            or (filename and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.ya?ml', filename))):
        raise ValueError('Invalid ingress Compose selector')
    command = ['docker', 'compose', '--project-directory', directory]
    if project:
        command += ['--project-name', project]
    if filename:
        command += ['--file', str(Path(directory) / filename)]
    return command


def nginx(c, *args):
    return run(ingress_command(c) + ['exec', '-T', 'nginx', 'nginx', *args])


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


def verify_served(c, metadata):
    # Nginx reload is asynchronous; retry verification, never installation.
    for attempt in range(10):
        try:served(c,metadata);return
        except Exception:
            if attempt==9:raise
            time.sleep(0.2)


def verify_installed(c):
    if load_activation(c) is not None:
        raise RuntimeError('Interrupted TLS activation requires bundle redelivery')
    root=Path(c['certificate_directory'])
    if root.is_symlink() or (root/'live').is_symlink():
        raise ValueError('Certificate directories cannot be symlinks')
    bundle={'environment':c['environment'],'certificates':{}}
    for name in c['lineages']:
        folder=root/'live'/name
        if folder.is_symlink():raise ValueError('Certificate lineage cannot be a symlink')
        for filename in ('fullchain.pem','privkey.pem'):snapshot(folder/filename,root)
        bundle['certificates'][name]={'fullchain':(folder/'fullchain.pem').read_text(),
                                    'private_key':(folder/'privkey.pem').read_text()}
    metadata=validate_bundle(bundle,c['environment'],c['lineages'])
    nginx(c,'-t');verify_served(c,metadata)
    return {'passed':True,'verified_served_certificate':True}


def activation_path():
    info = STATE.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PermissionError('Private receiver state directory required')
    return STATE / 'activation.json'


def activation_contract(c):
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()


def save_activation(c, paths, verify_runtime):
    entries = []
    root = Path(c['certificate_directory'])
    for path, _, previous, metadata, link_owner in paths:
        kind, data = previous
        entries.append({'path': str(path.relative_to(root)), 'kind': kind,
                        'data': base64.b64encode(data).decode() if kind == 'file' else data,
                        'metadata': metadata, 'link_owner': link_owner})
    journal = {'version': 1, 'contract': activation_contract(c),
               'phase': 'restore-before-retry', 'verify_runtime': verify_runtime,
               'previous': entries}
    payload = json.dumps(journal).encode()
    if len(payload) > 4 * MAX_BYTES:
        raise ValueError('TLS activation snapshot exceeds the private journal limit')
    private_write(activation_path(), payload)


def load_activation(c):
    path = activation_path()
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4 * MAX_BYTES):
        raise PermissionError('Invalid private TLS activation journal')
    journal = json.loads(path.read_text())
    if (set(journal) != {'version', 'contract', 'phase', 'verify_runtime', 'previous'}
            or journal['version'] != 1 or journal['contract'] != activation_contract(c)
            or journal['phase'] != 'restore-before-retry'
            or type(journal['verify_runtime']) is not bool):
        raise ValueError('TLS activation journal does not match the receiver contract')
    expected = {f'live/{name}/{filename}' for name in c['lineages']
                for filename in ('fullchain.pem', 'privkey.pem')}
    entries = journal['previous']
    if (not isinstance(entries, list) or len(entries) != len(expected)
            or any(not isinstance(entry, dict) or
                   set(entry) != {'path', 'kind', 'data', 'metadata', 'link_owner'}
                   for entry in entries)
            or {entry['path'] for entry in entries} != expected):
        raise ValueError('Invalid TLS activation entries')
    for entry in entries:
        kind, data, metadata, owner = (entry[key] for key in ('kind', 'data', 'metadata', 'link_owner'))
        if kind not in ('absent', 'file', 'link'):
            raise ValueError('Invalid TLS activation snapshot')
        if kind == 'absent':
            if any(value is not None for value in (data, metadata, owner)):
                raise ValueError('Invalid absent TLS snapshot')
            continue
        if (not isinstance(metadata, list) or len(metadata) != 3
                or any(type(value) is not int or value < 0 for value in metadata)
                or metadata[0] > 0o7777):
            raise ValueError('Invalid TLS snapshot metadata')
        if kind == 'file':
            if not isinstance(data, str) or owner is not None:
                raise ValueError('Invalid TLS file snapshot')
            entry['data'] = base64.b64decode(data, validate=True)
        elif (not isinstance(data, str) or not data or not isinstance(owner, list)
              or len(owner) != 2 or any(type(value) is not int or value < 0 for value in owner)
              or not (Path(c['certificate_directory']) / entry['path']).parent.joinpath(data).resolve().is_relative_to(
                  Path(c['certificate_directory']).resolve())):
            raise ValueError('Invalid TLS link snapshot')
    return journal


def clear_activation():
    activation_path().unlink()
    sync_directory(STATE)


def restore_activation(c, journal):
    # This operation is idempotent. Keep the journal until the entire previous
    # generation is back and Nginx has accepted it; a second interruption retries.
    root = Path(c['certificate_directory'])
    for entry in reversed(journal['previous']):
        path = root / entry['path']
        kind, data = entry['kind'], entry['data']
        if kind == 'link':
            path.unlink(missing_ok=True)
            path.symlink_to(data)
            if (path.lstat().st_uid, path.lstat().st_gid) != tuple(entry['link_owner']):
                os.lchown(path, *entry['link_owner'])
            sync_directory(path.parent)
        elif kind == 'file':
            private_write(path, data, entry['metadata'])
        elif path.exists() or path.is_symlink():
            path.unlink()
            sync_directory(path.parent)
    if journal['verify_runtime']:
        nginx(c, '-t')
        nginx(c, '-s', 'reload')
    clear_activation()


def install(c, bundle, bootstrap=False, prepare=False):
    metadata = validate_bundle(bundle,c['environment'],c['lineages'])
    root = Path(c['certificate_directory'])
    if root.is_symlink() or (root/'live').is_symlink():
        raise ValueError('Certificate directories cannot be symlinks')
    for name in c['lineages']:
        if (root/'live'/name).is_symlink():
            raise ValueError('Certificate lineage cannot be a symlink')
    pending = load_activation(c)
    if pending is not None:
        # A previous process may have left a torn pair or unactivated new files.
        # Restore before the normal preflight, which cannot parse a torn pair.
        restore_activation(c, pending)
    paths = []
    for name, item in bundle['certificates'].items():
        folder = root/'live'/name
        for filename, key in [('fullchain.pem','fullchain'),('privkey.pem','private_key')]:
            path = folder/filename
            previous = snapshot(path,root)
            info = path.stat() if previous[0] != 'absent' else None
            file_metadata = (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) if info else None
            link = path.lstat() if previous[0] == 'link' else None
            link_owner = (link.st_uid, link.st_gid) if link else None
            paths.append((path,item[key].encode(),previous,file_metadata,link_owner))
    changed = any(not path.is_file() or path.read_bytes()!=data for path,data,_,_,_ in paths)
    # An empty bootstrap can stop before ingress exists. Identical prepare
    # imports defer runtime checks until Compose has repaired stopped backends.
    initial = bootstrap and (not changed or all(previous[0]=='absent' for _,_,previous,_,_ in paths))
    verify_runtime = not (initial or (prepare and not changed))
    if verify_runtime:
        nginx(c,'-t')
    if changed:
        # Persist every original and its metadata before the first replacement.
        save_activation(c, paths, verify_runtime)
    try:
        if changed:
            for path,data,_,file_metadata,_ in paths:
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                private_write(path,data,file_metadata)
        if verify_runtime:
            nginx(c,'-t')
            if changed:nginx(c,'-s','reload')
            verify_served(c,metadata)
    except Exception:
        if changed:
            restore_activation(c, load_activation(c))
        raise RuntimeError('TLS deployment failed; previous certificate files restored') from None
    if changed:
        clear_activation()
    return {'passed':True,'changed':changed or pending is not None,'bootstrap_only':initial,
            'verified_served_certificate':verify_runtime,
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
    args=sys.argv[1:]
    if args not in ([],['--bootstrap'],['--prepare'],['--bootstrap','--prepare'],['--verify'],['--metrics'],['--renew']):
        raise ValueError('Unknown receiver action')
    if args!=['--verify']:
        payload=sys.stdin.buffer.read(MAX_BYTES+1)
        if len(payload)>MAX_BYTES:raise ValueError('Certificate payload too large')
        data=json.loads(payload)
    with ExitStack() as stack:
        if args==['--renew']:stack.enter_context(renewal_admission())
        lock=stack.enter_context(open(STATE/'receiver.lock','a'))
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args==['--metrics']:receipt=publish_status(c,data)
        elif args==['--verify']:receipt=verify_installed(c)
        else:receipt=install(c,data,bootstrap='--bootstrap' in args,prepare='--prepare' in args)
    print(json.dumps(receipt))


if __name__=='__main__':
    try:main()
    except Exception:
        print('TLS receiver failed; private diagnostics withheld.',file=sys.stderr)
        raise SystemExit(1)
