#!/usr/bin/env python3
"""Install source-owned ingress assets without mixing the checkout and runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

MARKER = 'application-source.json'
PENDING = '.application-source-pending.json'
ASSETS = ('nginx', 'landing-page')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular application file')


def validate_record(data):
    if (not isinstance(data, dict)
            or set(data) != {'schema', 'source_root', 'revision', 'files'} or data['schema'] != 1
            or not isinstance(data['source_root'], str) or not Path(data['source_root']).is_absolute()
            or not re.fullmatch(r'[a-f0-9]{40}', data['revision']) or not isinstance(data['files'], dict)):
        raise ValueError('Invalid application source record')
    validate_files(data['files'])
    return data


def validate_files(files):
    if not isinstance(files, dict):
        raise ValueError('Invalid application asset mapping')
    for name, value in files.items():
        if (not isinstance(name, str) or not Path(name).parts or Path(name).parts[0] not in ASSETS
                or '..' in Path(name).parts or Path(name).is_absolute()
                or (value is not None and (not isinstance(value, str)
                    or not re.fullmatch(r'[a-f0-9]{64}', value)))):
            raise ValueError('Invalid managed application asset')


def read_owned(path):
    if not path.exists() and not path.is_symlink():
        return None
    regular(path)
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
        raise ValueError('Source ownership records must be owned by their deployment user')
    return json.loads(path.read_text())


def record(root):
    data = read_owned(root / MARKER)
    return validate_record(data) if data is not None else None


def pending(root, previous):
    intent = read_owned(root / PENDING)
    if intent is None:
        return None
    if (not isinstance(intent, dict)
            or set(intent) != {'schema', 'before', 'after', 'files_before'} or intent['schema'] != 1):
        raise ValueError('Invalid pending source installation')
    if intent['before'] is not None:
        validate_record(intent['before'])
    validate_record(intent['after'])
    validate_files(intent['files_before'])
    if (previous not in [intent['before'], intent['after']]
            or intent['files_before'].keys() != intent['after']['files'].keys()):
        raise ValueError('Pending source installation disagrees with ownership record')
    return intent


def file_state(path):
    if any(parent.is_symlink() for parent in path.parents) or path.is_symlink():
        raise ValueError('Runtime assets must not traverse symlinks')
    if not path.exists():
        return None
    regular(path)
    return digest(path)


def guard(root):
    if root.is_symlink():
        raise ValueError('Runtime root must not be a symlink')
    previous = record(root)
    intent = pending(root, previous)
    for name in ('.env', 'docker-compose.yml', 'docker-compose.override.yml'):
        path = root / name
        if path.exists() or path.is_symlink():
            regular(path)
    if intent:
        for name, expected in intent['after']['files'].items():
            if file_state(root / name) not in [intent['files_before'][name], expected]:
                raise ValueError('A pending runtime asset was changed locally')
    elif previous:
        for name, expected in previous['files'].items():
            if file_state(root / name) != expected:
                raise ValueError('A managed runtime asset was changed locally')
    if (root / '.git').exists():
        # Generated Compose was deliberately modified by the previous deployment.
        managed = {'docker-compose.yml', *(previous['files'] if previous else []),
                   *(intent['after']['files'] if intent else [])}
        changed = git(root, 'diff', 'HEAD', '--name-only', '-z').decode().split('\0')
        if any(name and name not in managed for name in changed):
            raise ValueError('Runtime checkout has local source modifications; preserve them before migration')
    return previous


def sync_path(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        sync_path(directory.parent)


def atomic_record(path, data, mode):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output, indent=2, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(mode)
        sync_path(temporary)
        temporary.replace(path)
        sync_path(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def validate_roots(source, root):
    if (not source.is_absolute() or not root.is_absolute() or '..' in source.parts
            or '..' in root.parts or source.is_symlink()
            or any(parent.is_symlink() for parent in source.parents)
            or any(parent.is_symlink() for parent in root.parents)
            or source.resolve() == root.resolve() or source.resolve() in root.resolve().parents
            or root.resolve() in source.resolve().parents):
        raise ValueError('Source and runtime must be separate absolute directories')


def install(source, root, revision):
    validate_roots(source, root)
    previous = guard(root)
    if git(source, 'rev-parse', 'HEAD').decode().strip() != revision:
        raise ValueError('Source checkout does not match the verified image revision')
    tracked = git(source, 'ls-files', '-z').decode().split('\0')
    regular(source / 'docker-compose.yml')
    if 'docker-compose.override.yml' in tracked:
        raise ValueError('Selected application source owns the infrastructure Compose override')
    desired = {}
    for name in tracked:
        if name and Path(name).parts[0] in ASSETS:
            path = source / name
            regular(path)
            if any(parent.is_symlink() for parent in path.parents):
                raise ValueError('Source assets must not traverse symlinks')
            desired[name] = digest(path)
    if 'nginx/nginx.conf.template' not in desired:
        raise ValueError('Selected source lacks its Nginx runtime template')
    # Refuse unowned collisions before copying any file. Existing clean legacy
    # checkout assets are allowed; Git's diff guard above checks their contents.
    legacy = set(git(root, 'ls-files', '-z').decode().split('\0')) if (root / '.git').exists() else set()
    existing_intent = pending(root, previous)
    for name, value in desired.items():
        path = root / name
        if any(parent.is_symlink() for parent in path.parents):
            raise ValueError('Runtime assets must not traverse symlinks')
        if path.exists() or path.is_symlink():
            regular(path)
            owned = ((previous and name in previous['files'])
                     or (existing_intent and name in existing_intent['after']['files']))
            if not owned and name not in legacy and digest(path) != value:
                raise ValueError('An operator-owned runtime asset would be overwritten')
    # Persist exact permitted before/after bytes before changing runtime assets.
    # A retry accepts those two states only, preserving local modification guards.
    retired = {name: None for name in (previous['files'] if previous else legacy)
               if Path(name).parts and Path(name).parts[0] in ASSETS and name not in desired}
    data = dict(schema=1, source_root=str(source), revision=revision, files={**retired, **desired})
    intent = pending(root, previous)
    if intent and intent['after'] != data:
        raise ValueError('Resume the pending source revision before selecting another release')
    changed = previous != data
    ensure_directory(root)
    if not intent and (changed or any(file_state(root / name) != value for name, value in data['files'].items())):
        intent = dict(schema=1, before=previous, after=data,
                      files_before={name: file_state(root / name) for name in data['files']})
        atomic_record(root / PENDING, intent, 0o600)
    for name, value in desired.items():
        target = root / name
        if not target.exists() or digest(target) != value or target.stat().st_mode & 0o777 != 0o644:
            ensure_directory(target.parent)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
            try:
                shutil.copyfile(source / name, temporary)
                temporary.chmod(0o644)
                sync_path(temporary)
                temporary.replace(target)
                sync_path(target.parent)
            finally:
                temporary.unlink(missing_ok=True)
            changed = True
    for name in (previous['files'] if previous else legacy):
        if Path(name).parts and Path(name).parts[0] in ASSETS and name not in desired:
            if (root / name).exists():
                (root / name).unlink()
                sync_path((root / name).parent)
                changed = True
    if previous != data:
        atomic_record(root / MARKER, data, 0o644)
    (root / PENDING).unlink(missing_ok=True)
    sync_path(root)
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--source')
    parser.add_argument('--revision')
    args = parser.parse_args()
    try:
        if args.source:
            validate_roots(Path(args.source), Path(args.runtime))
        if args.source and args.revision:
            changed = install(Path(args.source), Path(args.runtime), args.revision)
        else:
            guard(Path(args.runtime))
            changed = False
        print(json.dumps({'changed': changed}))
    except Exception:
        parser.exit(1, 'Application source installation refused; preserve local changes and review source/runtime ownership.\n')
