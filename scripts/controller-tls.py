#!/usr/bin/env python3
"""SOPS-backed controller ACME issuance, scoped delivery and renewal status."""
import argparse
import base64
import fcntl
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import uuid

from hou1_tls_material import validate_bundle, validate_lineages

IMAGE='certbot/dns-cloudflare:v5.1.0'


def run(args, **kwargs):
    return subprocess.run(args,capture_output=True,check=True,timeout=60,**kwargs).stdout


def private_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.tls-',delete=False) as stream:
        temporary=Path(stream.name)
        try:
            stream.write(data);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o600)
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)


def private_file(path):
    path=Path(path)
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or path.stat().st_uid!=os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError('Controller inputs must be private files owned by the operator')
    return path


def read_config(path, transport=False):
    path=private_file(path)
    root=path.parent
    if root.stat().st_uid!=os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('Controller state directory must be private')
    c=json.loads(path.read_text())
    if c.get('environment') not in ('test','production'):raise ValueError('Select a known environment')
    validate_lineages(c['lineages'])
    if (not isinstance(c['email'],str) or not re.fullmatch(r'[^\s@]+@[^\s@]+',c['email'])
            or not isinstance(c['recipients'],list) or not c['recipients']
            or any(not re.fullmatch(r'age1[0-9a-z]{58}',r) for r in c['recipients'])):
        raise ValueError('Declare the ACME email and age recipients')
    private_file(c['age_key_file'])
    if not Path(c['dns_secret_source']).is_absolute() or not Path(c['dns_secret_source']).is_file():
        raise ValueError('Supply the encrypted DNS-only bundle')
    for binary in ('sops','openssl','docker'):
        if not shutil.which(binary):raise ValueError('Missing controller dependency')
    if transport:
        network=ipaddress.IPv4Network(c['netbird_subnet'])
        if not network.subnet_of(ipaddress.IPv4Network('100.64.0.0/10')) or ipaddress.IPv4Address(c['netbird_ip']) not in network:
            raise ValueError('TLS delivery requires the declared VPN subnet')
        private_file(c['ssh_config'])
        if not re.fullmatch(r'[A-Za-z0-9_-]+',c['ssh_host']):raise ValueError('Select a pinned SSH profile')
        options=dict(line.split(' ',1) for line in run(['ssh','-G','-F',c['ssh_config'],c['ssh_host']]).decode().splitlines() if ' ' in line)
        if (options.get('hostname')!=c['netbird_ip'] or options.get('user')!='hou1-renewal'
                or options.get('stricthostkeychecking') not in ('yes','true')
                or options.get('identitiesonly') not in ('yes','true')
                or options.get('batchmode') not in ('yes','true')
                or options.get('passwordauthentication') not in ('no','false')
                or options.get('proxycommand','none')!='none' or options.get('proxyjump','none')!='none'):
            raise ValueError('SSH must use the fixed renewal account, VPN peer and pinned keys')
        known=Path(options.get('userknownhostsfile',''))
        if (not known.is_absolute() or known.is_symlink() or not known.is_file() or known==Path('/dev/null')
                or known.stat().st_uid!=os.getuid() or known.stat().st_mode & 0o022 or not known.stat().st_size):
            raise ValueError('SSH must use a populated known-hosts file')
        private_file(options.get('identityfile',''))
    return c,root


def decrypt(path,c):
    return json.loads(run(['sops','--decrypt','--output-type','json',str(path)],
        env={**os.environ,'SOPS_AGE_KEY_FILE':c['age_key_file']}))


def encrypted(value,c,root):
    return run(['sops','--encrypt','--age',','.join(c['recipients']),'--input-type','json','--output-type','json','/dev/stdin'],
        input=json.dumps(value).encode(),cwd=root,env={**os.environ,'SOPS_AGE_KEY_FILE':c['age_key_file']})


def ssh(c, action, data):
    return json.loads(run(['ssh','-F',c['ssh_config'],c['ssh_host'],action],input=json.dumps(data).encode()))


def extract_state(state,c,certs):
    if state.get('environment')!=c['environment'] or state.get('lineages')!=c['lineages']:
        raise ValueError('ACME state belongs to another certificate contract')
    raw=base64.b64decode(state['archive'],validate=True)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        members=archive.getmembers()
        if len(members)>2000 or sum(m.size for m in members)>32*1024*1024:
            raise ValueError('ACME state exceeds its bound')
        archive.extractall(certs,filter='data')


def dns_token(c):
    data=decrypt(c['dns_secret_source'],c)
    if set(data)!={'cloudflare_dns_api_token'} or not re.fullmatch(r'[A-Za-z0-9_-]{10,256}',data['cloudflare_dns_api_token']):
        raise ValueError('Use a DNS-only encrypted token bundle')
    return data['cloudflare_dns_api_token']


def check_ready(c,root):
    dns_token(c)
    bundle=decrypt(root/'certificates.enc.json',c)
    metadata=validate_bundle(bundle,c['environment'],c['lineages'])
    state=decrypt(root/'acme.enc.json',c)
    if state.get('environment')!=c['environment'] or state.get('lineages')!=c['lineages']:
        raise ValueError('ACME state does not match the selected certificate contract')
    receipt=json.loads((root/'tls-success.json').read_text())
    if (receipt.get('passed') is not True or receipt.get('environment')!=c['environment']
            or receipt.get('sha256')!={name:item['sha256'] for name,item in metadata.items()}
            or type(receipt.get('recorded_at')) not in (int,float)
            or not -30 <= time.time()-receipt['recorded_at'] <= 26*3600):
        raise ValueError('Verify deployment of the selected bundle before enabling its timer')


def issue(c,root,dry_run=False,deploy=False):
    state_path=root/'acme.enc.json'
    bundle_path=root/'certificates.enc.json'
    token=dns_token(c)
    with tempfile.TemporaryDirectory(prefix='hou1-acme-',dir=root) as directory:
        work=Path(directory);certs=work/'conf';certs.mkdir()
        if state_path.exists():extract_state(decrypt(state_path,c),c,certs)
        if dry_run and not state_path.exists():raise ValueError('Issue initial certificates before an ACME dry-run')
        credential=work/'cloudflare.ini'
        private_write(credential,('dns_cloudflare_api_token = '+token+'\n').encode())
        for name in ('work','logs'):(work/name).mkdir()
        for name,item in c['lineages'].items():
            container='hou1-tls-'+uuid.uuid4().hex
            command=['docker','run','--rm','--name',container,'--user',f'{os.getuid()}:{os.getgid()}',
                '-v',f'{certs}:/etc/letsencrypt','-v',f'{credential}:/cloudflare.ini:ro',
                '-v',f'{work/"work"}:/work','-v',f'{work/"logs"}:/logs',IMAGE,
                'certonly','--non-interactive','--agree-tos','--email',c['email'],
                '--cert-name',name,'--dns-cloudflare','--dns-cloudflare-credentials','/cloudflare.ini',
                '--dns-cloudflare-propagation-seconds','120','--keep-until-expiring','--work-dir','/work','--logs-dir','/logs']
            for domain in item['domains']:command+=['-d',domain]
            if dry_run:command+=['--dry-run']
            try:result=subprocess.run(command,capture_output=True,timeout=720)
            finally:subprocess.run(['docker','rm','--force',container],capture_output=True,timeout=30)
            private_write(root/'certbot.log',(result.stdout+result.stderr).replace(token.encode(),b'[redacted]'))
            if result.returncode:raise RuntimeError('Certbot failed; inspect the private redacted log')
        if dry_run:return {'passed':True,'mode':'dry-run','environment':c['environment']}
        bundle={'environment':c['environment'],'certificates':{}}
        for name in c['lineages']:
            folder=certs/'live'/name
            for filename in ('fullchain.pem','privkey.pem'):
                if not (folder/filename).resolve().is_relative_to(certs.resolve()):raise ValueError('ACME material escapes controller work directory')
            bundle['certificates'][name]={'fullchain':(folder/'fullchain.pem').read_text(),'private_key':(folder/'privkey.pem').read_text()}
        metadata=validate_bundle(bundle,c['environment'],c['lineages'])
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w:gz') as archive:archive.add(certs,arcname='.')
        state={'environment':c['environment'],'lineages':c['lineages'],'archive':base64.b64encode(payload.getvalue()).decode()}
        encrypted_state=encrypted(state,c,root);encrypted_bundle=encrypted(bundle,c,root)
        if state_path.exists():private_write(root/'acme.previous.enc.json',state_path.read_bytes())
        if bundle_path.exists():private_write(root/'certificates.previous.enc.json',bundle_path.read_bytes())
        private_write(state_path,encrypted_state);private_write(bundle_path,encrypted_bundle)
        report={'passed':True,'environment':c['environment'],'mode':'issue','dns_credentials_shipped':False}
        if deploy:
            receipt=ssh(c,'deploy-v1',bundle)
            if (receipt.get('passed') is not True or receipt.get('verified_served_certificate') is not True
                    or receipt.get('sha256')!={name:item['sha256'] for name,item in metadata.items()}):
                raise RuntimeError('Receiver did not verify the expected served certificates')
            report.update(mode='deploy',recorded_at=time.time(),sha256=receipt['sha256'])
            private_write(root/'tls-success.json',json.dumps(report).encode())
        return report


def publish(c,root,now=None):
    now=time.time() if now is None else now
    last=0;success=0
    try:
        saved=json.loads((root/'tls-success.json').read_text())
        if saved['environment']==c['environment'] and saved['passed'] is True:last=saved['recorded_at']
        attempt=json.loads((root/'tls-last-run.json').read_text())
        success=int(attempt['passed'] is True)
        result=run(['systemctl','--user','show','hou1-tls-'+c['environment']+'.service','-p','Result','--value']).decode().strip()
        if result!='success':success=0
    except Exception:success=0
    receipt=ssh(c,'status-v1',{'environment':c['environment'],'last_success':last,'last_run_success':success,'observed_at':now})
    if receipt.get('passed') is not True:raise RuntimeError('Renewal status was not accepted')
    return {'passed':True,'mode':'publish-status'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    modes=parser.add_mutually_exclusive_group(required=True)
    for mode in ('issue','dry-run','deploy','publish-status','check-config'):modes.add_argument('--'+mode,action='store_true')
    args=parser.parse_args()
    c,root=read_config(args.config,transport=args.deploy or args.publish_status or args.check_config)
    if args.check_config:
        check_ready(c,root)
        print(json.dumps({'configuration_valid':True}));return
    if args.publish_status:
        print(json.dumps(publish(c,root)));return
    with open(root/'controller.lock','a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            report=issue(c,root,dry_run=args.dry_run,deploy=args.deploy)
            if args.deploy:private_write(root/'tls-last-run.json',json.dumps(report).encode())
        except Exception:
            if args.deploy:private_write(root/'tls-last-run.json',json.dumps({'passed':False}).encode())
            raise
    print(json.dumps({key:value for key,value in report.items() if key!='sha256'}))


if __name__=='__main__':
    def terminate(signum, frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,terminate)
    try:main()
    except Exception:
        print(json.dumps({'passed':False,'diagnostics':'Private controller inputs or redacted log; values withheld.'}))
        raise SystemExit(1)
