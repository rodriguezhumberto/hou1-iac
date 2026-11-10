#!/usr/bin/env python3
"""Run each isolated recovery phase once; retain failures until cleanup."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def config():
    cfg = json.loads((ROOT / 'drill-input.json').read_text())
    if (cfg['environment'] != 'test' or cfg['root'] != str(ROOT) or ROOT.parent != Path('/var/tmp')
            or cfg['project'] != ROOT.name or not re.fullmatch(r'hou1-recovery-drill-[a-z0-9][a-z0-9-]{5,39}', ROOT.name)):
        raise ValueError('Not a dedicated isolated recovery root')
    return cfg


def compose(cfg, *args):
    return ['docker', 'compose', '--project-directory', str(ROOT), '-p', cfg['project'], '-f', str(ROOT / 'compose.json'), *args]


def write(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2) + '\n')


def cleanup(cfg):
    # Do not pass unvalidated mounts/network modes to Compose, including during cleanup.
    saved = json.loads((ROOT / 'compose.json').read_text())
    if saved['networks']['recovery'] != {'internal': True, 'name': cfg['project']}:
        raise ValueError('Unexpected recovery network definition')
    for service in saved['services'].values():
        if (service.get('ports') or service.get('privileged') or service.get('network_mode')
                or any(not mount.split(':', 1)[0].startswith(str(ROOT) + '/') for mount in service.get('volumes', []))):
            raise ValueError('Unexpected recovery service boundary')
    subprocess.run(compose(cfg, 'down', '--remove-orphans'), capture_output=True, check=True, timeout=120)
    remaining = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + cfg['project']])
    if remaining.strip():
        raise ValueError('Drill containers remain')
    baseline_path = ROOT / 'live-baseline.json'
    unchanged = None
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        live = json.loads(subprocess.check_output(['docker', 'inspect', *baseline]))
        unchanged = {c['Name']: dict(id=c['Id'], started=c['State']['StartedAt']) for c in live} == baseline
        if not unchanged or not all(c['State']['Running'] for c in live):
            raise ValueError('Primary stack changed or stopped')
    if subprocess.run(['docker', 'network', 'inspect', cfg['project']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise ValueError('Drill network remains')
    report = {'drill_containers_removed': True, 'drill_network_removed': True, 'primary_ids_starttimes_unchanged': unchanged}
    write('cleanup-report.json', report)
    return report


def run(phase):
    os.umask(0o077)
    cfg = config()
    if phase == 'cleanup':
        return cleanup(cfg)
    phases = ['build', 'start', 'identity', 'http']
    if (ROOT / 'first-error.json').exists() or (ROOT / (phase + '-result.json')).exists():
        raise ValueError('This attempt is complete or failed; use a new drill ID')
    index = phases.index(phase)
    if index and not (ROOT / (phases[index - 1] + '-result.json')).exists():
        raise ValueError('Previous recovery phase has not passed')
    result = {'phase': phase, 'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        inputs = None
        if phase == 'identity':
            command = compose(cfg, 'exec', '-T', '-e', 'RECOVERY_ADMIN_USERNAME=' + cfg['admin_username'],
                              '-e', 'RECOVERY_READER_USERNAME=' + cfg['reader_username'], 'authentik-server', 'ak', 'shell')
            probe = json.loads((ROOT / 'health-input.json').read_text())
            credentials = {key: probe[key] for key in ('TEST_ADMIN_PASSWORD', 'TEST_READER_PASSWORD')}
            # Send credentials through captured stdin, never Compose arguments or logs.
            inputs = ('recovery_probe_secrets = ' + repr(credentials) + '\n').encode() + (ROOT / 'identity_smoke.py').read_bytes()
        else:
            command = [sys.executable, str(ROOT / {'build': 'build.py', 'start': 'runtime.py', 'http': 'check.py'}[phase])]
        with (ROOT / (phase + '-private.log')).open('xb') as output:
            process = subprocess.run(command, input=inputs, stdout=output, stderr=subprocess.STDOUT, timeout=1000)
        if process.returncode:
            raise RuntimeError('Recovery phase failed')
        if phase == 'identity':
            for line in (ROOT / 'identity-private.log').read_text().splitlines():
                if line.startswith('RECOVERY_IDENTITY_REPORT='):
                    write('identity-report.json', json.loads(line.split('=', 1)[1]))
            if not (ROOT / 'identity-report.json').exists():
                raise ValueError('Missing identity assertions')
            if not json.loads((ROOT / 'identity-report.json').read_text()).get('fixture_passwords_match_recovery_point'):
                raise ValueError('Missing recovered fixture credential verification')
        if phase == 'http':
            report = json.loads((ROOT / 'http-report.json').read_text())
            if not report['passed']:
                raise ValueError('Missing successful HTTP assertions')
            for key in ('original_cms_table_counts', 'original_netbird_table_counts'):
                report.pop(key, None)
            report['harness_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in ROOT.glob('*.py')}
            report['scope'] = 'Isolated component restore; no production, host replacement, external Google or VPN peer cutover'
            write('recovery-report.json', report)
        result['passed'] = True
        write(phase + '-result.json', result)
        return result
    except Exception as error:
        result.update(passed=False, error_type=type(error).__name__)
        write('first-error.json', result)
        raise RuntimeError('Recovery failed; protected phase log retained and retries refused') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['build', 'start', 'identity', 'http', 'cleanup'])
    try:
        print(json.dumps(run(parser.parse_args().phase)))
    except Exception:
        parser.exit(1, 'Recovery phase stopped. Inspect protected diagnostics; do not reuse this attempt.\n')
