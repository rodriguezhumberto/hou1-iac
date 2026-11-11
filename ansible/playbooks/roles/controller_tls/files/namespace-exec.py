#!/usr/bin/python3 -I
"""Root-owned fixed-job entry point; drop privileges before TLS code runs."""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

CONFIG = Path('/etc/controller-renewal/exec.json')
IMAGE = 'netbirdio/netbird@sha256:eca5b274c635bfbb04ec6724a0712d1146190dd450eea199eeb83916fdd94965'
MODES = {'controller-tls.py': {'--deploy', '--publish-status', '--check-config', '--dry-run'},
         'controller-grafana-tls.py': {'--deploy', '--check-config', '--dry-run'},
         'publish-renewal-metrics.py': {''}}


def private(path, owner):
    path = Path(path)
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or path.stat().st_uid != owner or path.stat().st_mode & 0o022):
        raise ValueError('Unexpected job file ownership')
    return path


def invocation(config, job, container):
    user = pwd.getpwuid(config['uid'])
    if config['uid'] == 0 or config['gid'] != user.pw_gid or job not in config['jobs']:
        raise ValueError('Unknown non-root renewal job')
    host = container['HostConfig']
    if (not container['State']['Running'] or container['Config']['Image'] != IMAGE
            or host['NetworkMode'] != 'bridge' or host['Privileged']
            or host['CapAdd'] != ['NET_ADMIN'] or host.get('PortBindings')):
        raise ValueError('Unexpected renewal network namespace')
    entry = config['jobs'][job]
    program = private(entry['program'], config['uid'])
    private(entry['config'], config['uid'])
    if program.name not in MODES or entry['mode'] not in MODES[program.name]:
        raise ValueError('Unsupported renewal operation')
    argv = ['/usr/bin/nsenter', '--target', str(container['State']['Pid']), '--net',
            '/usr/bin/setpriv', '--reuid', str(user.pw_uid), '--regid', str(user.pw_gid),
            '--init-groups', '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
            '/usr/bin/python3', str(program), '--config', entry['config']]
    if entry['mode']:
        argv.append(entry['mode'])
    environment = {'HOME': user.pw_dir, 'USER': user.pw_name, 'LOGNAME': user.pw_name,
                   'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                   'XDG_RUNTIME_DIR': '/run/user/' + str(user.pw_uid),
                   'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + str(user.pw_uid) + '/bus'}
    return argv, environment


def main():
    if os.getuid() != 0 or len(sys.argv) != 2 or not re.fullmatch(r'[a-z][a-z0-9-]{1,80}', sys.argv[1]):
        raise ValueError('Require one fixed job identifier')
    private(CONFIG, 0)
    if CONFIG.parent.stat().st_uid != 0 or CONFIG.parent.stat().st_mode & 0o077:
        raise ValueError('Executor configuration must be root-private')
    config = json.loads(CONFIG.read_text())
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,62}', config['container']):
        raise ValueError('Invalid namespace selector')
    result = subprocess.run(['/usr/bin/docker', 'inspect', config['container']],
                            check=True, capture_output=True, timeout=10)
    container, = json.loads(result.stdout)
    argv, environment = invocation(config, sys.argv[1], container)
    os.execve(argv[0], argv, environment)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit('Scoped renewal execution refused; private diagnostics withheld.')
