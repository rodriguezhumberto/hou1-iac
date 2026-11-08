"""Incremental, bounded aggregation of the fixed credential-free Nginx schema."""
import json
import math
import os
from pathlib import Path
import tempfile

BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10)
SERVICES = ('staff', 'authentik', 'netbird', 'other')
CLASSES = ('1xx', '2xx', '3xx', '4xx', '5xx')
MAX_BATCH = 16 * 1024 * 1024


def initial():
    return {'version': 1, 'inode': None, 'offset': 0, 'retained': {}, 'last_callback_time': 0,
            'requests': {s: {c: 0 for c in CLASSES} for s in SERVICES},
            'login': {'start_failure': 0, 'callback_success': 0, 'callback_failure': 0, 'callback_unknown': 0},
            'consecutive_failures': 0, 'callback_count': 0, 'callback_sum': 0,
            'buckets': [0 for _ in BUCKETS]}


def update(state, line):
    data = json.loads(line)
    if set(data) != {'v', 'time', 'service', 'route', 'status', 'duration', 'result'}:
        raise ValueError('Unexpected metrics schema')
    if data['v'] != 1 or data['service'] not in SERVICES or data['route'] not in ('request', 'login', 'callback'):
        raise ValueError('Unexpected metrics labels')
    if type(data['status']) is not int or not 100 <= data['status'] <= 599:
        raise ValueError('Invalid response status')
    for key in ('time', 'duration'):
        if type(data[key]) not in (int, float) or not math.isfinite(data[key]) or data[key] < 0:
            raise ValueError('Invalid time')
    if data['result'] not in ('none', 'started', 'success', 'failure', 'unknown'):
        raise ValueError('Invalid result')
    state['requests'][data['service']][str(data['status']//100)+'xx'] += 1
    if data['service'] == 'staff' and data['route'] == 'login' and data['result'] == 'failure':
        state['login']['start_failure'] += 1
    if data['service'] == 'staff' and data['route'] == 'callback' and data['result'] in ('success', 'failure', 'unknown'):
        result = data['result']
        state['login']['callback_'+result] += 1
        if data['time'] >= state['last_callback_time']:
            state['consecutive_failures'] = 0 if result == 'success' else state['consecutive_failures'] + 1
            state['last_callback_time'] = data['time']
        state['callback_count'] += 1
        state['callback_sum'] += data['duration']
        for i, limit in enumerate(BUCKETS):
            state['buckets'][i] += int(data['duration'] <= limit)


def advance(state, path):
    """Read current/retained rotations once. Commit neither offset nor counts on error."""
    path = Path(path)
    files = sorted(path.parent.glob(path.name+'.[0-9]*'), key=lambda p: p.stat().st_mtime_ns) + [path]
    if not path.is_file() or path.is_symlink():
        raise ValueError('Missing metrics log')
    current = path.stat()
    if state['inode'] is None:
        files = [path]
    else:
        positions = [i for i, p in enumerate(files) if p.stat().st_ino == state['inode']]
        if not positions:
            raise ValueError('Unread metrics rotation lost')
        # Revisit retained inodes for final writes made while Nginx reopens logs.
        files = [p for i, p in enumerate(files) if i >= positions[0] or str(p.stat().st_ino) in state['retained']]
    remaining = MAX_BATCH
    for item in files:
        if item.is_symlink():
            raise ValueError('Unexpected log symlink')
        with item.open('rb') as stream:
            info = os.fstat(stream.fileno())
            offset = state['offset'] if info.st_ino == state['inode'] else state['retained'].get(str(info.st_ino), 0)
            if info.st_size < offset:
                raise ValueError('Metrics log truncated; use rename rotation')
            stream.seek(offset)
            while True:
                line = stream.readline(1025)
                if not line:
                    break
                if len(line) > 1024 or remaining < len(line):
                    raise ValueError('Metrics batch exceeds bound')
                if not line.endswith(b'\n'):
                    # Keep the offset before an incomplete current-file write.
                    if item != path:
                        raise ValueError('Incomplete rotated metrics record')
                    break
                update(state, line)
                remaining -= len(line)
                offset += len(line)
            state['retained'][str(info.st_ino)] = offset
            if item == path:
                state['inode'], state['offset'] = info.st_ino, offset
    inodes = {str(p.stat().st_ino) for p in files}
    state['retained'] = {k: v for k, v in state['retained'].items() if k in inodes}
    return state


def render(state):
    rows = ['# TYPE hou1_nginx_requests_total counter']
    for service, values in state['requests'].items():
        for status, value in values.items():
            rows.append(f'hou1_nginx_requests_total{{service="{service}",status_class="{status}"}} {value}')
    rows.append('# TYPE hou1_staff_login_total counter')
    for key, value in state['login'].items():
        phase, result = key.split('_', 1)
        rows.append(f'hou1_staff_login_total{{phase="{phase}",result="{result}"}} {value}')
    rows += ['# TYPE hou1_staff_login_consecutive_failures gauge',
             f'hou1_staff_login_consecutive_failures {state["consecutive_failures"]}',
             '# TYPE hou1_staff_callback_duration_seconds histogram']
    for limit, count in zip(BUCKETS, state['buckets']):
        rows.append(f'hou1_staff_callback_duration_seconds_bucket{{le="{limit}"}} {count}')
    rows += [f'hou1_staff_callback_duration_seconds_bucket{{le="+Inf"}} {state["callback_count"]}',
             f'hou1_staff_callback_duration_seconds_count {state["callback_count"]}',
             f'hou1_staff_callback_duration_seconds_sum {state["callback_sum"]}']
    return '\n'.join(rows)+'\n'


def collect(log, state_path):
    state_path = Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else initial()
    if state['version'] != 1:
        raise ValueError('Unsupported metrics state')
    state = advance(state, log)
    text = render(state)
    fd, temp = tempfile.mkstemp(prefix='.requests-', dir=state_path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, state_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return text
