#!/usr/bin/python3
"""Apply a Compose project, retrying changed static inputs until activation succeeds."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import tempfile

LAB_DNS_POLICY = Path('/etc/hou-lab-dns-firewall.json')
LAB_DNS_HELPER = '/usr/local/libexec/hou-lab-dns-firewall'


def run(argv, directory):
    return subprocess.run(argv, cwd=directory, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=600).stdout


def fingerprint(definition, document):
    resources = {'networks': {name: document.get('networks', {}).get(name)
                             for name in definition.get('networks', {})},
                 'volumes': {mount['source']: document.get('volumes', {}).get(mount['source'])
                             for mount in definition.get('volumes', []) if mount['type'] == 'volume'}}
    digest = hashlib.sha256(json.dumps([definition, resources], sort_keys=True).encode())
    for mount in definition.get('volumes', []):
        if mount['type'] != 'bind' or not mount.get('read_only', False):
            continue
        source = Path(mount['source'])
        metadata = source.lstat()
        # Directory mounts expose atomic child replacements directly. Their
        # owners validate/reload ingress and renewed certificates separately.
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError('Static file mounts must be regular files')
        digest.update(json.dumps([str(source), metadata.st_mode, metadata.st_uid,
                                  metadata.st_gid]).encode())
        with source.open('rb') as content:
            for block in iter(lambda: content.read(1024 * 1024), b''):
                digest.update(block)
    return digest.hexdigest()


def atomic_state(path, services):
    descriptor, name = tempfile.mkstemp(prefix='.compose-inputs-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'version': 1, 'services': services}, output, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def container_ids(command, directory, services):
    return {name: run(command + ['ps', '--quiet', name], directory).decode().strip()
            for name in services}


def require_address(address, interface):
    """Refuse activation until the expected address exists on its interface."""
    output = subprocess.check_output(['ip', '-j', '-4', 'address', 'show', 'dev', interface],
                                     stderr=subprocess.DEVNULL, timeout=10)
    addresses = [item['local'] for device in json.loads(output)
                 for item in device.get('addr_info', []) if item.get('family') == 'inet']
    if address not in addresses:
        raise ValueError('Required VPN address is not ready')


def reconcile(directory, command, selected=None, validate_nginx=False, dependencies=True):
    document = json.loads(run(command + ['config', '--format', 'json'], directory))
    definitions = document['services']
    services = selected or sorted(definitions)
    if not services or any(name not in definitions for name in services):
        raise ValueError('Expected declared Compose services')
    current = {name: fingerprint(definitions[name], document) for name in services}
    state_path = directory / '.compose-applied-inputs.json'
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    applied = previous.get('services', {}) if previous.get('version') == 1 else {}
    before = container_ids(command, directory, services)
    if LAB_DNS_POLICY.exists():
        runpy.run_path(LAB_DNS_HELPER)['prepare_compose'](
            directory, command, document, selected, run)
    # Replace existing consumers first: waiting on their old unhealthy startup
    # configuration would prevent a repaired input from ever being activated.
    changed = [name for name in services if current[name] != applied.get(name) and before[name]]
    if changed:
        run(command + ['up', '-d', '--no-build', '--no-deps', '--force-recreate',
                       '--wait', '--wait-timeout', '120', *changed], directory)
    up = command + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '120']
    if not dependencies:
        up.append('--no-deps')
    run(up + (selected or []), directory)
    after = container_ids(command, directory, services)
    if validate_nginx:
        run(command + ['exec', '-T', 'nginx', 'nginx', '-t'], directory)
    # Failed activation/validation keeps the previous state for a retry, even
    # when Ansible has already installed the new files during the first attempt.
    applied.update(current)
    atomic_state(state_path, {name: value for name, value in applied.items() if name in definitions})
    return bool(changed or any(before[name] != after[name] for name in services))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--project')
    parser.add_argument('--file')
    parser.add_argument('--services', nargs='+')
    parser.add_argument('--no-deps', action='store_true')
    parser.add_argument('--validate-nginx', action='store_true')
    parser.add_argument('--require-address')
    parser.add_argument('--interface')
    args = parser.parse_args()
    if bool(args.require_address) != bool(args.interface):
        parser.error('--require-address and --interface must be supplied together')
    if args.require_address:
        require_address(args.require_address, args.interface)
    command = ['docker', 'compose']
    if args.project:
        command += ['--project-name', args.project]
    if args.file:
        command += ['--file', args.file]
    with (args.directory / '.compose-apply.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        changed = reconcile(args.directory, command, args.services, args.validate_nginx, not args.no_deps)
    print(json.dumps({'changed': changed}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Compose activation failed; diagnostics withheld') from None
