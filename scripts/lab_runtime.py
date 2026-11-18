"""Read-only target probe, sent over SSH by deploy-lab.py without installation."""

import json
from pathlib import Path
import platform
import subprocess
import sys
import urllib.error
import urllib.request


def output(argv):
    return subprocess.check_output(argv, stderr=subprocess.DEVNULL, text=True, timeout=30)


def runtime_contract(content):
    """An absent manifest is legacy; declared contracts must be understood exactly."""
    if content is None:
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate application runtime field')
            result[key] = value
        return result
    declared = json.loads(content, object_pairs_hook=unique)
    expected = {'schema': 1, 'liveness_path': '/api/health.json',
                'readiness_path': '/api/ready.json', 'stop_grace_seconds': 30}
    if (not isinstance(declared, dict) or declared != expected
            or type(declared['schema']) is not int
            or type(declared['stop_grace_seconds']) is not int):
        raise ValueError('Unsupported application runtime contract')
    return declared


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_project(project, application, runtime=None):
    command = ['docker', 'compose', '--project-directory', project['directory']]
    if project.get('project'):
        command += ['--project-name', project['project']]
    if project.get('file'):
        command += ['--file', str(Path(project['directory']) / project['file'])]
    declared = output(command + ['config', '--services']).split()
    services = project.get('services', declared)
    if not services or any(service not in declared for service in services):
        raise ValueError('Empty project')
    containers = {}
    application_found = False
    for service in services:
        ids = output(command + ['ps', '--all', '--quiet', service]).split()
        if not ids:
            raise ValueError('Missing service')
        # State includes private health-command output. Keep it in memory and
        # return only IDs after checking the selected status fields.
        states = [json.loads(line) for line in output(
            ['docker', 'inspect', '--format', '{{json .State}}', *ids]).splitlines()]
        if len(states) != len(ids) or any(
                not state['Running'] or state.get('Health', {}).get('Status', 'healthy') != 'healthy'
                for state in states):
            raise ValueError('Unready service')
        containers[project['directory'] + ':' + service] = ids
        if application and service == 'astro-app':
            images = [json.loads(line) for line in output(
                ['docker', 'inspect', '--format', '{{json .Config.Image}}', *ids]).splitlines()]
            if len(images) != len(ids) or any(image != application['image'] for image in images):
                raise ValueError('Application image differs')
            if runtime:
                for container in ids:
                    response = output(['docker', 'exec', container, 'curl', '--fail', '--silent',
                                   '--show-error', '--max-time', '3',
                                   '--write-out', '\n%{http_code}',
                                   'http://127.0.0.1:4321' + runtime['readiness_path']])
                    body, status = response.rsplit('\n', 1)
                    ready = json.loads(body)
                    if (status != '200' or not isinstance(ready, dict)
                            or set(ready) != {'ok'} or ready['ok'] is not True):
                        raise ValueError('Application is not ready')
            application_found = True
    return containers, application_found


def probe(contract):
    if platform.node() != contract['hostname']:
        raise ValueError('Hostname differs')
    devices = json.loads(output(['ip', '-j', '-4', 'address', 'show', 'dev', contract['interface']]))
    addresses = {address['local'] for device in devices for address in device.get('addr_info', [])}
    if contract['address'] not in addresses:
        raise ValueError('VPN address missing')
    report = {'hostname': platform.node(), 'address_present': True}
    if contract.get('handoff'):
        policy = {}
        for line in output(['sshd', '-T']).splitlines():
            key, _, value = line.partition(' ')
            policy.setdefault(key, []).append(value)
        if (policy.get('passwordauthentication') != ['no']
                or policy.get('kbdinteractiveauthentication') != ['no']
                or policy.get('permitrootlogin') != ['no']):
            raise ValueError('SSH authentication is not ready for managed access')
        listeners = policy.get('listenaddress', [])
        if not listeners or any(value.rsplit(':', 1)[0] != contract['address'] for value in listeners):
            raise ValueError('SSH must listen only on the selected VPN address')
        sockets = [line.split()[3] for line in output(['ss', '-H', '-lntp']).splitlines()
                   if '"sshd"' in line]
        if not sockets or any(value.rsplit(':', 1)[0] != contract['address'] for value in sockets):
            raise ValueError('The running SSH daemon differs from the selected VPN listener')
        active = subprocess.run(['systemctl', 'is-active', 'ssh.socket'],
                                capture_output=True, text=True, timeout=15)
        if active.returncode not in (3, 4) or active.stdout.strip() not in ('inactive', 'unknown'):
            raise ValueError('SSH socket activation can bypass the selected listener')
        fallback = ('ansible-ssh-bootstrap-fallback.timer' if contract['service'] == 'hou1'
                    else 'ansible-hou4-ssh-bootstrap-fallback.timer')
        active = subprocess.run(['systemctl', 'is-active', fallback],
                                capture_output=True, text=True, timeout=15)
        if active.returncode not in (3, 4) or active.stdout.strip() not in ('inactive', 'unknown'):
            raise ValueError('The bootstrap fallback timer is still active')
        report['ssh_policy_verified'] = True
    if not contract['verify']:
        return report

    application = contract['application'] if contract['tls'] else None
    runtime = None
    if application:
        revision = output(['git', '-C', contract['source'], 'rev-parse', 'HEAD']).strip()
        if revision != application['revision'] or output(
                ['git', '-C', contract['source'], 'status', '--porcelain']).strip():
            raise ValueError('Application source differs')
        manifest = Path(contract['source']) / 'runtime-contract.json'
        if manifest.is_symlink():
            raise ValueError('Application runtime contract must be a regular file')
        runtime = runtime_contract(manifest.read_text() if manifest.exists() else None)
        report.update(application_revision=revision, source_clean=True)
    application_found = False
    report['containers'] = {}
    for project in contract['compose']:
        containers, found = check_project(project, application, runtime)
        report['containers'].update(containers)
        application_found |= found
    if contract['tls']:
        if not application_found:
            raise ValueError('Application project missing')
        if runtime:
            report['application_ready'] = True
        output(['python3', '/usr/local/libexec/hou1-tls-receive', '--verify'])
        report['tls_verified'] = True

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    report['https'] = []
    for check in contract['https']:
        try:
            with opener.open(check['url'], timeout=15) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        if status != check['status']:
            raise ValueError('HTTPS status differs')
        report['https'].append({'url': check['url'], 'status': status})
    return report


def main():
    try:
        print(json.dumps(probe(json.load(sys.stdin))))
    except Exception:
        # Do not expose parser errors, health output, TLS diagnostics or HTTP bodies.
        print(json.dumps({'failed': True}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
