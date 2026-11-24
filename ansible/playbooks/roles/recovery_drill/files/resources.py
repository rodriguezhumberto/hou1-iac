"""Explicit bounds for a constrained isolated recovery attempt."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

MIB = 1024 ** 2
# memory, additional swap (MiB); totals are 2 GiB RAM and 512 MiB swap.
LIMITS = {'postgresql': (384, 64), 'authentik-server': (640, 192),
          'authentik-worker': (384, 128), 'netbird-server': (256, 64),
          'dashboard': (64, 0), 'astro-app': (256, 64), 'proxy': (64, 0)}


def profile(cfg):
    value = cfg.get('resource_profile', 'standard')
    if value not in ('standard', 'constrained'):
        raise ValueError('Unknown recovery resource profile')
    return value


def apply(cfg, compose):
    if profile(cfg) != 'constrained':
        return
    if set(compose['services']) != set(LIMITS):
        raise ValueError('Unexpected constrained recovery services')
    for name, service in compose['services'].items():
        memory, swap = LIMITS[name]
        service.update(mem_limit=memory * MIB, memswap_limit=(memory + swap) * MIB,
                       pids_limit=256)


def inspect(names):
    return json.loads(subprocess.check_output(['docker', 'inspect', *names],
                                             stderr=subprocess.DEVNULL, timeout=15))


def sample():
    info = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(info['MemAvailable'].split()[0]) * 1024
    full = next(line for line in Path('/proc/pressure/memory').read_text().splitlines()
                if line.startswith('full '))
    pressure = float(dict(part.split('=') for part in full.split()[1:])['avg10'])
    return available, pressure


def primary(cfg, root):
    live = inspect(cfg['primary_containers'])
    value = {c['Name']: {'id': c['Id'], 'started': c['State']['StartedAt']} for c in live}
    if not all(c['State']['Running'] and not c['State'].get('OOMKilled') for c in live):
        raise RuntimeError('Primary stack stopped or was OOM-killed')
    baseline = root / 'live-baseline.json'
    if baseline.exists() and json.loads(baseline.read_text()) != value:
        raise RuntimeError('Primary stack changed during constrained recovery')
    if not baseline.exists():
        baseline.write_text(json.dumps(value) + '\n')


def containers(cfg):
    ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter',
                                  'label=com.docker.compose.project=' + cfg['project']], timeout=15).decode().split()
    values = inspect(ids) if ids else []
    for item in values:
        name = item['Config']['Labels']['com.docker.compose.service']
        if name not in LIMITS:
            raise RuntimeError('Unexpected constrained container')
        memory, swap = LIMITS[name]
        if (item['HostConfig']['Memory'] != memory * MIB
                or item['HostConfig']['MemorySwap'] != (memory + swap) * MIB):
            raise RuntimeError('Constrained container memory limits differ')
        if item['State'].get('OOMKilled'):
            raise RuntimeError('Constrained recovery container was OOM-killed: ' + name)


def execute(cfg, root, command, output, inputs=None, timeout=1000):
    """Monitor the attempt while its independently capped containers execute."""
    primary(cfg, root)
    report_path = root / 'resource-report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {
        'profile': 'constrained', 'ram_limit_bytes': 2048 * MIB,
        'additional_swap_limit_bytes': 512 * MIB, 'aborted': False}
    deadline = time.monotonic() + timeout
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE if inputs is not None else subprocess.DEVNULL,
                              stdout=output, stderr=subprocess.STDOUT, start_new_session=True) as process:
            try:
                if inputs is not None:
                    process.stdin.write(inputs); process.stdin.close()
                while process.poll() is None:
                    available, pressure = sample()
                    report['minimum_available_bytes'] = min(report.get('minimum_available_bytes', available), available)
                    report['maximum_full_pressure_avg10'] = max(report.get('maximum_full_pressure_avg10', 0), pressure)
                    if available < 384 * MIB or pressure >= 5:
                        raise RuntimeError('Constrained recovery stopped for host memory pressure')
                    containers(cfg)
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Constrained recovery phase deadline exceeded')
                    time.sleep(2)
                containers(cfg)
                primary(cfg, root)
                return process.returncode
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
    except Exception as error:
        report['aborted'] = True
        report['error_type'] = type(error).__name__
        report['reason'] = str(error)[:240]
        # Stop only this attempt. Ansible's always block then removes it and
        # verifies the primary baseline; a stop failure remains visible.
        subprocess.run(['docker', 'compose', '--project-directory', str(root), '-p', cfg['project'],
                        '-f', str(root / 'compose.json'), 'stop', '--timeout', '10'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=90)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + '\n')
