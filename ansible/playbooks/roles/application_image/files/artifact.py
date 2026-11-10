#!/usr/bin/env python3
"""Validate the fixed application artifact and its deployed/recovery identity."""
import argparse
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = 'ghcr.io/rodriguezhumberto/pearland-paws/app'
SOURCE = 'https://github.com/rodriguezhumberto/pearland-paws'


def validate_reference(image, revision):
    if (not isinstance(image, str)
            or not re.fullmatch(re.escape(REPOSITORY) + r'@sha256:[a-f0-9]{64}', image)
            or not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision)):
        raise ValueError('Require the application GHCR digest and full source revision')


def validate_receipt(receipt, revision):
    if (not isinstance(receipt, dict)
            or set(receipt) != {'schema', 'image', 'revision', 'platform', 'image_id'}
            or receipt['schema'] != 1 or receipt['revision'] != revision
            or receipt['platform'] != 'linux/amd64'
            or not isinstance(receipt['image_id'], str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', receipt['image_id'])):
        raise ValueError('Invalid saved application image receipt')
    validate_reference(receipt['image'], revision)
    return receipt


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=900)


def verify(image, revision, pull=False):
    validate_reference(image, revision)
    if pull:
        run('docker', 'pull', '--platform', 'linux/amd64', image)
    info, = json.loads(run('docker', 'image', 'inspect', image))
    labels = info['Config'].get('Labels') or {}
    if (info['Architecture'] != 'amd64' or info['Os'] != 'linux'
            or image not in info.get('RepoDigests', [])
            or labels.get('org.opencontainers.image.revision') != revision
            or labels.get('org.opencontainers.image.source') != SOURCE
            or labels.get('io.pearlandpaws.staff-offboarding-schema') != '1'
            or info['Config']['User'] != 'appuser'):
        raise ValueError('Application image does not match the selected source and runtime contract')
    return validate_receipt(dict(schema=1, image=image, revision=revision,
                                 platform='linux/amd64', image_id=info['Id']), revision)


def running(root):
    root = Path(root)
    path = root / 'application-image.json'
    if not path.exists():
        # Existing source-built backups retain their original recovery method.
        # A digest-deployed container without its receipt must fail closed.
        container, = run('docker', 'compose', '--project-directory', str(root),
                         'ps', '--all', '-q', 'astro-app').decode().split()
        info, = json.loads(run('docker', 'inspect', container))
        if '@sha256:' in info['Config']['Image']:
            raise ValueError('Digest deployment is missing its application image receipt')
        return None
    revision = run('git', '-C', str(root), 'rev-parse', 'HEAD').decode().strip()
    receipt = validate_receipt(json.loads(path.read_text()), revision)
    validate_compose(root, receipt)
    if verify(receipt['image'], revision) != receipt:
        raise ValueError('Saved application receipt differs from the installed artifact')
    container, = run('docker', 'compose', '--project-directory', str(root),
                     'ps', '--all', '-q', 'astro-app').decode().split()
    info, = json.loads(run('docker', 'inspect', container))
    if info['Image'] != receipt['image_id'] or info['Config']['Image'] != receipt['image']:
        raise ValueError('Running application differs from the recorded artifact')
    return receipt


def validate_compose(root, receipt):
    import yaml
    root = Path(root)
    service = yaml.safe_load((root / 'docker-compose.yml').read_text())['services']['astro-app']
    override = yaml.safe_load((root / 'docker-compose.override.yml').read_text()) or {}
    extra = override.get('services', {}).get('astro-app', {})
    if (service.get('image') != receipt['image'] or 'build' in service
            or 'build' in extra or extra.get('image', receipt['image']) != receipt['image']):
        raise ValueError('Saved Compose configuration contradicts the application receipt')


def saved(payload, revision):
    """Reject incomplete or contradictory digest-backed recovery metadata."""
    import yaml
    payload = Path(payload)
    metadata = json.loads((payload / 'recovery.json').read_text())
    path = payload / 'app-config/application-image.json'
    service = yaml.safe_load((payload / 'app-config/docker-compose.yml').read_text())['services']['astro-app']
    if 'application_image' not in metadata:
        if path.exists() or '@sha256:' in service.get('image', ''):
            raise ValueError('Digest-backed recovery is missing its authenticated artifact metadata')
        return None
    receipt = validate_receipt(metadata['application_image'], revision)
    if not path.is_file() or json.loads(path.read_text()) != receipt:
        raise ValueError('Saved application receipt contradicts the recovery metadata')
    validate_compose(payload / 'app-config', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--pull', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.image, args.revision, args.pull)))
    except Exception:
        parser.exit(1, 'Application artifact verification failed; no build or replacement was attempted.\n')
