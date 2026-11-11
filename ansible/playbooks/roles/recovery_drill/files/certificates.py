"""Resolve saved certificate lineages by SAN; never assume one lineage per host."""
from pathlib import Path
import subprocess


def select_lineage(conf, hostname):
    conf = Path(conf).resolve()
    if not hostname or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for c in hostname):
        raise ValueError('Invalid recovery hostname')
    live = conf / 'live'
    directories = sorted(p for p in live.iterdir() if p.is_dir())
    directories.sort(key=lambda p: p.name != hostname)
    for folder in directories:
        cert, key = folder / 'fullchain.pem', folder / 'privkey.pem'
        if not all(p.is_file() and p.resolve().is_relative_to(conf) for p in (cert, key)):
            continue
        def openssl(*args):
            return subprocess.run(['openssl', *map(str, args)], capture_output=True, timeout=10)
        if openssl('x509', '-in', cert, '-noout', '-checkhost', hostname).returncode:
            continue
        if openssl('x509', '-in', cert, '-noout', '-checkend', '0').returncode:
            continue
        public_cert = openssl('x509', '-in', cert, '-pubkey', '-noout')
        public_key = openssl('pkey', '-in', key, '-pubout')
        if public_cert.returncode or public_key.returncode or public_cert.stdout != public_key.stdout:
            continue
        return folder.name
    raise ValueError('No valid saved certificate/key pair covers recovery hostname: ' + hostname)


def rewrite_proxy(proxy, conf):
    import re
    mapping = {}
    for host in re.findall(r'server_name ([a-z0-9.-]+);', proxy):
        mapping[host] = select_lineage(conf, host)
    for host, lineage in mapping.items():
        proxy = proxy.replace('/etc/letsencrypt/live/' + host + '/',
                              '/etc/letsencrypt/live/' + lineage + '/')
    return proxy, mapping
