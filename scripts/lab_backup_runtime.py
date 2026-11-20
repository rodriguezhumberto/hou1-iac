"""Fixed test-host backup operations, sent over pinned SSH by the matrix."""

from contextlib import ExitStack
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time

SOURCE = Path('/etc/hou1-backup/source.json')
RECEIVER = Path('/etc/hou4-backup/receiver.json')
POINT_FILES = ('recovery.age', 'manifest.json', 'receipt.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def output(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=30)


def unit(name):
    fields = 'ActiveState,Result,MainPID,ExecMainPID,ExecMainStatus,ExecMainStartTimestampMonotonic,RuntimeMaxUSec'
    return dict(line.split('=', 1) for line in output(
        ['systemctl', 'show', name, '--property=' + fields]).splitlines() if '=' in line)


def digest(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def point_files(path):
    require(path.is_dir() and not path.is_symlink(), 'Invalid recovery directory')
    require({p.name for p in path.iterdir()} == set(POINT_FILES), 'Unexpected recovery point files')
    require(all((path / name).is_file() and not (path / name).is_symlink() for name in POINT_FILES),
            'Recovery point files must be regular files')


def completed(root):
    """Hash committed ciphertext and metadata, excluding private staging."""
    result = {}
    for path in root.iterdir():
        if path.name.startswith('.'):
            continue
        point_files(path)
        manifest = json.loads((path / 'manifest.json').read_text())
        receipt = json.loads((path / 'receipt.json').read_text())
        require(manifest.get('schema') == 1 and manifest.get('environment') == 'test'
                and manifest.get('id') == path.name
                and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', path.name), 'Invalid retained manifest')
        require(type(manifest.get('size')) is int and manifest['size'] > 0
                and (path / 'recovery.age').stat().st_size == manifest['size'], 'Retained size differs')
        hashes = {}
        for name in POINT_FILES:
            with (path / name).open('rb') as stream:
                hashes[name] = digest(stream)
        require(hashes['recovery.age'] == manifest.get('sha256') == receipt.get('sha256'),
                'Retained ciphertext or receipt differs')
        result[path.name] = {'manifest': manifest, 'hashes': hashes}
    return result


def receiver_config(contract):
    config = json.loads(RECEIVER.read_text())
    require(config.get('environment') == 'test'
            and config.get('source_ip') == contract['source_ip']
            and config.get('repository') == '/var/lib/hou4-backups/test/hou1',
            'Receiver does not match the declared test source')
    require(not Path(config['repository']).is_symlink(), 'Receiver repository cannot be a symlink')
    return config


def source_state(allow_empty=False):
    config = json.loads(SOURCE.read_text())
    require(config.get('environment') == 'test', 'Source must be an established test backup')
    root = Path(config['work_root'])
    require(root == Path('/var/lib/hou1-backup'), 'Unexpected source work directory')
    manifest, status = None, {'last_success': 0, 'last_run_success': 0}
    try:
        status = json.loads((root / 'last-export.json').read_text())
        with (root / 'ready.export').open('rb') as stream:
            manifest = json.loads(stream.readline(8193))
    except FileNotFoundError:
        require(allow_empty and not status['last_success'], 'Successful source point is missing')
    return {'manifest': manifest, 'status': status,
            'recipient': config['recipient'], 'intent': (root / 'restart-intent.json').exists(),
            'service': unit('hou1-backup-prepare.service'), 'timer': unit('hou1-backup-prepare.timer')}


def receiver_state(contract):
    config = receiver_config(contract)
    root = Path(config['repository'])
    partials = [p.name for p in root.iterdir() if p.name.startswith(('.incoming-', '.verified-'))]
    return {'points': completed(root), 'partials': partials, 'service': unit('hou4-backup.service')}


def ssh_child(parent, config):
    children = Path(f'/proc/{parent}/task/{parent}/children')
    if not children.exists():
        return None
    for value in children.read_text().split():
        pid = int(value)
        try:
            argv = Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        except FileNotFoundError:
            continue
        if (argv and argv[0] == b'ssh' and argv[-2:] ==
                [('hou1-backup@' + config['source_ip']).encode(), b'export-v1']
                and config['identity'].encode() in argv):
            return pid
    return None


def kill_transfer(pid, parent, config):
    # pidfd prevents a recycled PID from receiving the interruption signal.
    descriptor = os.pidfd_open(pid)
    try:
        require(ssh_child(parent, config) == pid, 'The collection SSH child changed')
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    finally:
        os.close(descriptor)


def preserved_prior(root, prior, expected):
    """Open prior files before a successful retry can legitimately prune them."""
    with ExitStack() as stack:
        opened = {}
        for name, record in prior.items():
            path = root / name
            point_files(path)
            opened[name] = {part: stack.enter_context((path / part).open('rb')) for part in POINT_FILES}
        require(not (root / expected['id']).exists(), 'An interrupted partial point was published')
        for name, files in opened.items():
            require({part: digest(stream) for part, stream in files.items()} == prior[name]['hashes'],
                    'The previous recovery point changed during interruption')


def interrupt(contract):
    config = receiver_config(contract)
    root = Path(config['repository'])
    before = receiver_state(contract)
    expected = contract['expected']
    require(before['points'] and not before['partials'], 'Require a verified prior point and no staging')
    require(before['service']['ActiveState'] in ('inactive', 'failed'), 'Collection is already running')
    require(before['service']['RuntimeMaxUSec'] not in ('infinity', '0'), 'Upgrade the receiver runtime limit first')
    require(expected['environment'] == 'test' and expected['size'] > 3 * 1024 * 1024,
            'The live interruption drill needs ciphertext larger than three MiB')
    require(expected.get('schema') == 1 and type(expected.get('created')) is int
            and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', str(expected.get('id', '')))
            and re.fullmatch(r'[a-f0-9]{64}', str(expected.get('sha256', ''))), 'Invalid expected recovery point')
    require(expected['created'] > max(v['manifest']['created'] for v in before['points'].values()),
            'Capture a fresh uncollected source point first')
    output(['systemctl', 'start', '--no-block', 'hou4-backup.service'])
    deadline = time.monotonic() + 120
    parent = None
    interrupted = None
    while time.monotonic() < deadline:
        if parent is None:
            state = unit('hou4-backup.service')
            if int(state['MainPID']) > 0:
                parent = int(state['MainPID'])
            else:
                time.sleep(0.02)
                continue
        pid = ssh_child(parent, config)
        require(Path(f'/proc/{parent}').exists(), 'Collector exited before a partial interruption was observed')
        for stage in root.glob('.incoming-*'):
            path = stage / 'recovery.age'
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if pid is not None and 0 < size < expected['size'] - 1024 * 1024:
                kill_transfer(pid, parent, config)
                interrupted = {'ssh_pid': pid, 'partial_bytes': size}
                break
        if interrupted is not None:
            break
        require(not (root / expected['id']).exists(), 'Transfer finished before a partial interruption was observed')
        time.sleep(0.005)
    require(interrupted is not None, 'No partial collection was observed before the deadline')
    preserved_prior(root, before['points'], expected)
    # No service restart or second start request: the installed collector retries.
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        state = unit('hou4-backup.service')
        if state['ActiveState'] not in ('active', 'activating', 'deactivating'):
            break
        time.sleep(0.2)
    else:
        raise TimeoutError('Collection retry did not finish')
    require(state['Result'] == 'success' and state['ExecMainStatus'] == '0'
            and int(state['ExecMainPID']) == parent, 'The same collector did not recover successfully')
    after = receiver_state(contract)
    require(not after['partials'] and expected['id'] in after['points'], 'Retry left staging or no committed point')
    require(after['points'][expected['id']]['manifest'] == expected, 'Committed point differs from the source')
    return {'interrupted': interrupted, 'collector_pid': parent, 'prior_preserved': True,
            'partial_not_published': True, 'same_collector_retried': True, 'point': expected,
            'staging_removed': True, 'service': state}


def run(contract):
    require(contract.get('environment') == 'test' and contract.get('role') in ('hou1', 'hou4'),
            'Explicit test-host backup contract required')
    require(platform.node() == contract['hostname'], 'Unexpected backup host')
    address = ipaddress.ip_address(contract['address'])
    require(address in ipaddress.ip_network('100.91.0.0/16'), 'Backup drill requires the test overlay')
    addresses = json.loads(output(['ip', '-j', 'address', 'show', 'dev', contract['interface']]))
    require(any(item.get('local') == str(address) for link in addresses for item in link.get('addr_info', [])),
            'The declared test overlay address is absent')
    action = contract['operation']
    if action == 'access':
        return {'private_access': True}
    if contract['role'] == 'hou1':
        require(action in ('source-state', 'source-status', 'ensure-capture', 'activate-timer', 'capture'), 'Unsupported source operation')
        state = source_state(allow_empty=True) if action in ('source-status', 'ensure-capture') else source_state()
        if action == 'ensure-capture':
            require(not state['intent'] or state['service']['ActiveState'] in ('active', 'activating'),
                    'Interrupted writers require recovery before capture')
            if state['service']['ActiveState'] in ('inactive', 'failed'):
                # Fresh recovery is inactive and needs the same lock as capture.
                # The controller requests this only after releasing admission.
                output(['systemctl', 'start', 'hou1-backup-recover.service'])
                if not state['status']['last_success']:
                    output(['systemctl', 'start', '--no-block', 'hou1-backup-prepare.service'])
        if action == 'activate-timer':
            require(not state['intent'] and state['status']['last_success'] > 0
                    and state['manifest'] == contract['expected'], 'Schedule requires the verified source point')
            output(['systemctl', 'start', 'hou1-backup-prepare.timer'])
        if action == 'capture':
            require(not state['intent'] and state['service']['ActiveState'] in ('inactive', 'failed'),
                    'Source capture or writer recovery is already active')
            output(['systemctl', 'start', '--no-block', 'hou1-backup-prepare.service'])
        return state
    require(action in ('receiver-state', 'collect', 'interrupt'), 'Unsupported receiver operation')
    if action == 'interrupt':
        return interrupt(contract)
    state = receiver_state(contract)
    if action == 'collect':
        expected = contract['expected']
        retained = state['points'].get(expected['id'])
        if retained:
            require(retained['manifest'] == expected, 'Retained point differs from selected source')
        elif state['service']['ActiveState'] in ('inactive', 'failed'):
            require(state['service']['RuntimeMaxUSec'] not in ('infinity', '0'), 'Receiver runtime must be bounded')
            output(['systemctl', 'start', '--no-block', 'hou4-backup.service'])
    return state


if __name__ == '__main__':
    try:
        require(os.geteuid() == 0, 'Root backup observer required')
        print(json.dumps(run(json.load(sys.stdin))))
    except Exception as error:
        print(json.dumps({'failed': type(error).__name__, 'message': str(error)}))
        sys.exit(1)
