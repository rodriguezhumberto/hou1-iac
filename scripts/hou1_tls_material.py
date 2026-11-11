"""Shared, bounded TLS material validation. Never include private inputs in errors."""
import hashlib
import re
import ssl
import subprocess
import tempfile
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
HOST = r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+'


def run(args, data=None):
    return subprocess.run(args, input=data, capture_output=True, check=True, timeout=20).stdout


def validate_lineages(lineages):
    if not isinstance(lineages, dict) or not 1 <= len(lineages) <= 10:
        raise ValueError('Declare bounded certificate lineages')
    probes = set()
    for name, item in lineages.items():
        if not re.fullmatch(HOST, name) or not isinstance(item, dict) or set(item) != {'domains', 'probe_hosts'}:
            raise ValueError('Invalid certificate lineage')
        for key in ('domains', 'probe_hosts'):
            values = item[key]
            if (not isinstance(values, list) or not 1 <= len(values) <= 30 or len(set(values)) != len(values)
                    or any(not isinstance(v, str) or not re.fullmatch((r'(?:\*\.)?' if key == 'domains' else '') + HOST, v) for v in values)):
                raise ValueError('Invalid certificate names')
        if name not in item['domains'] or probes.intersection(item['probe_hosts']):
            raise ValueError('Ambiguous certificate coverage')
        probes.update(item['probe_hosts'])


def validate_bundle(bundle, environment, lineages):
    validate_lineages(lineages)
    if (environment not in ('test', 'production') or bundle.get('environment') != environment
            or set(bundle.get('certificates', {})) != set(lineages)):
        raise ValueError('Certificate bundle does not match the selected environment')
    metadata = {}
    with tempfile.TemporaryDirectory(prefix='hou1-tls-check-') as directory:
        cert, key = Path(directory)/'cert.pem', Path(directory)/'key.pem'
        for name, expected in lineages.items():
            item = bundle['certificates'][name]
            if not isinstance(item, dict) or set(item) != {'fullchain', 'private_key'}:
                raise ValueError('Invalid certificate material')
            for value in item.values():
                if not isinstance(value, str) or not 0 < len(value.encode()) < MAX_BYTES // 2:
                    raise ValueError('Invalid certificate size')
            cert.write_text(item['fullchain']); cert.chmod(0o600)
            key.write_text(item['private_key']); key.chmod(0o600)
            run(['openssl','x509','-in',str(cert),'-noout','-checkend','86400'])
            sans = run(['openssl','x509','-in',str(cert),'-noout','-ext','subjectAltName']).decode()
            if set(re.findall(r'DNS:([^,\s]+)', sans)) != set(expected['domains']):
                raise ValueError('Certificate SANs differ from the configured allowlist')
            for host in expected['probe_hosts']:
                run(['openssl','x509','-in',str(cert),'-noout','-checkhost',host])
                run(['openssl','verify','-purpose','sslserver','-untrusted',str(cert),'-verify_hostname',host,str(cert)])
            public_cert = run(['openssl','x509','-in',str(cert),'-pubkey','-noout'])
            if public_cert != run(['openssl','pkey','-in',str(key),'-pubout']):
                raise ValueError('Certificate and private key do not match')
            der = run(['openssl','x509','-in',str(cert),'-outform','DER'])
            expiry = run(['openssl','x509','-in',str(cert),'-noout','-enddate']).decode().strip().split('=',1)[1]
            metadata[name] = {'sha256':hashlib.sha256(der).hexdigest(), 'expires':ssl.cert_time_to_seconds(expiry)}
    return metadata
