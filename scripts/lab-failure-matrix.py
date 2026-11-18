#!/usr/bin/env python3
"""Run the non-destructive HOU1/HOU4 failure matrix on declared test hosts.

The workflow file stays the source of host, release and transport inputs. A
private ``failure_matrix`` section names the Compose service to interrupt for
each scenario. Use ``scripts/lab-network.py`` around this command so all SSH
and DNS access stays inside the dedicated test transport.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'scripts' / 'deploy-lab.py'
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('deploy_lab', DEPLOY)
deploy_lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_lab)

SCENARIOS = ('application', 'monitoring', 'dependency')
SERVICE_NAME = r'[a-zA-Z0-9][a-zA-Z0-9_.-]*'


class MatrixError(ValueError):
    """A matrix input, transport or recovery assertion failed."""


def require(condition, message):
    if not condition:
        raise MatrixError(message)


def private_json(path):
    source = deploy_lab.private_file(path)
    try:
        value = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MatrixError('The private matrix input is unreadable') from error
    require(isinstance(value, dict), 'The private matrix input must be an object')
    return source, value


def load_config(path):
    """Validate the normal workflow while keeping matrix declarations private."""
    source, raw = private_json(path)
    matrix = raw.pop('failure_matrix', None)
    require(isinstance(matrix, dict), 'Declare a private failure_matrix section')
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.workflow-', dir=source.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(raw, stream)
        os.chmod(temporary, 0o600)
        config = deploy_lab.load_config(temporary)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    validate_matrix(config, matrix)
    return config, matrix


def validate_matrix(config, matrix):
    require(set(matrix) == set(SCENARIOS),
            'Declare application, monitoring and dependency matrix scenarios')
    expected_hosts = {'application': 'hou1', 'monitoring': 'hou4', 'dependency': 'hou1'}
    for name in SCENARIOS:
        value = matrix[name]
        require(isinstance(value, dict) and
                set(value) >= {'host', 'compose_index', 'service'},
                f'{name} must declare host, compose_index and service')
        require(value['host'] == expected_hosts[name], f'{name} selects the wrong host')
        host = config['hosts'][value['host']]
        index = value['compose_index']
        require(type(index) is int and 0 <= index < len(host['compose']),
                f'{name} selects an invalid Compose project')
        service = value['service']
        require(isinstance(service, str) and __import__('re').fullmatch(SERVICE_NAME, service),
                f'{name} selects an invalid Compose service')
        declared = host['compose'][index].get('services')
        if declared is not None:
            require(service in declared, f'{name} service is not declared by its Compose project')
    dependency = matrix['dependency']
    require(set(dependency) >= {'probe_query', 'readiness'},
            'The dependency scenario must declare its probe and local readiness check')
    query = dependency['probe_query']
    require(isinstance(query, str) and 1 <= len(query) <= 512 and '\n' not in query,
            'The dependency probe query must be a short single-line string')
    readiness = dependency['readiness']
    require(isinstance(readiness, dict) and set(readiness) == {'port', 'path'},
            'Declare the HOU4 readiness port and path')
    require(type(readiness['port']) is int and 1 <= readiness['port'] <= 65535,
            'The readiness port is invalid')
    require(isinstance(readiness['path'], str) and readiness['path'].startswith('/')
            and '\n' not in readiness['path'], 'The readiness path is invalid')


def compose_command(project):
    """Build a Compose prefix; the caller supplies its remote working directory."""
    command = ['docker', 'compose']
    if project.get('project'):
        command += ['--project-name', project['project']]
    if project.get('file'):
        command += ['--file', project['file']]
    return command


def compose_shell(project, action):
    directory = project['directory']
    require(isinstance(directory, str) and directory.startswith('/') and '..' not in Path(directory).parts,
            'Compose directories must be absolute and local to the host')
    return 'cd ' + shlex.quote(directory) + ' && ' + shlex.join(compose_command(project) + action)


def probe_value(payload):
    """Return the sole Prometheus scalar, rejecting missing or ambiguous data."""
    try:
        results = payload['data']['result']
        require(isinstance(results, list) and len(results) == 1,
                'The matrix probe returned an unexpected series count')
        value = results[0]['value'][1]
    except (KeyError, IndexError, TypeError) as error:
        raise MatrixError('The matrix probe response is malformed') from error
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise MatrixError('The matrix probe value is not numeric') from error


def readiness_url(readiness):
    return f'http://127.0.0.1:{readiness["port"]}{readiness["path"]}'


class Matrix:
    def __init__(self, config, matrix, run):
        self.config, self.matrix, self.run = config, matrix, run
        self.workflow = deploy_lab.Workflow(config, run)
        self.cleanup = []
        self.steps = []
        self.save('running')

    def save(self, status):
        deploy_lab.write_private(self.run / 'matrix.json', json.dumps({
            'version': 1, 'environment': 'test', 'status': status,
            'steps': self.steps}, indent=2) + '\n')

    def record(self, name, status, **fields):
        item = {'step': name, 'status': status, **fields}
        self.steps.append(item)
        self.save('running')

    def host(self, scenario):
        return self.config['hosts'][self.matrix[scenario]['host']]

    def project(self, scenario):
        declaration = self.matrix[scenario]
        return self.host(scenario)['compose'][declaration['compose_index']]

    def remote(self, scenario, command, *, capture=False, timeout=180):
        host = self.host(scenario)
        fixed, _, _ = deploy_lab.transport(host)
        argv = [*fixed, host['ssh_host_ip'], command]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise MatrixError(f'{scenario} remote operation failed')
        return result.stdout if capture else None

    def stop(self, scenario):
        service = self.matrix[scenario]['service']
        command = compose_shell(self.project(scenario), ['stop', '--timeout', '30', service])
        self.remote(scenario, 'sudo -n sh -c ' + shlex.quote(command), timeout=120)

    def restore(self, scenario):
        declaration = self.matrix[scenario]
        project = self.project(scenario)
        service = declaration['service']
        if scenario == 'application':
            command = ['sudo', '-n', '/usr/local/libexec/hou1-compose-apply', project['directory']]
            if project.get('project'):
                command += ['--project', project['project']]
            if project.get('file'):
                command += ['--file', project['file']]
            command += ['--services', service]
            self.remote(scenario, shlex.join(command), timeout=300)
        elif scenario == 'monitoring':
            self.remote(scenario, 'sudo -n /usr/local/libexec/hou4-monitoring-apply', timeout=300)
        else:
            command = compose_shell(project, ['up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', service])
            self.remote(scenario, 'sudo -n sh -c ' + shlex.quote(command), timeout=180)

    def verify_workflow(self):
        sanitized = self.run / 'workflow.json'
        value = {key: item for key, item in self.config.items() if key != 'failure_matrix'}
        if value.get('version') == 2:
            value.pop('application', None)
        deploy_lab.write_private(sanitized, json.dumps(value))
        result = subprocess.run([sys.executable, str(DEPLOY), '--config', str(sanitized), '--phase', 'verify'],
                                capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise MatrixError('Managed workflow verification failed')

    def verify_hou4_advisory(self):
        host = self.config['hosts']['hou4']
        self.workflow.transports['hou4'] = deploy_lab.transport(host)
        playbook = Path(host['repository']) / 'ansible/playbooks/hou4.yml'
        self.workflow.playbook('hou4', playbook, 'hou4-advisory', extra_args=['--tags', 'monitoring'])
        readiness = self.matrix['dependency']['readiness']
        body = self.remote('monitoring', 'curl -fsS ' + shlex.quote(readiness_url(readiness)), capture=True)
        require('ready' in body.lower(), 'HOU4 local readiness did not pass during the HOU1 outage')
        query = self.matrix['dependency']['probe_query']
        query_command = ('curl -fsS --get --data-urlencode ' + shlex.quote('query=' + query)
                         + ' http://127.0.0.1:' + str(readiness['port']) + '/api/v1/query')
        payload = json.loads(self.remote('monitoring', query_command, capture=True))
        require(probe_value(payload) == 0, 'The declared HOU1 outage probe did not report failure')

    def scenario(self, name, operation, verification):
        self.record(name, 'running')
        try:
            self.stop(name)
            self.cleanup.append(name)
            operation()
            verification()
        except BaseException:
            self.record(name, 'failed')
            raise
        finally:
            if name in self.cleanup:
                self.restore(name)
                self.cleanup.remove(name)
        self.record(name, 'passed')

    def run_all(self):
        try:
            self.verify_workflow()
            self.record('baseline', 'passed')
            self.scenario('application',
                          lambda: self.restore('application'), self.verify_workflow)
            self.scenario('monitoring',
                          lambda: self.restore('monitoring'), self.verify_workflow)
            self.scenario('dependency', self.verify_hou4_advisory, self.verify_workflow)
            self.record('final', 'passed')
            self.save('passed')
        except BaseException:
            self.save('failed')
            raise


def plan(matrix):
    return {'scope': 'declared test hosts; non-destructive service interruptions',
            'scenarios': list(SCENARIOS), 'reboot': 'separate opt-in drill'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--apply', action='store_true', help='Apply the declared test-host interruptions')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config, matrix = load_config(args.config)
        if not args.apply:
            print(json.dumps(plan(matrix), indent=2))
            return 0
        root = Path(config['run_directory'])
        with (root / '.failure-matrix.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=root))
            os.chmod(run, 0o700)
            print('Private run directory: ' + str(run), flush=True)
            Matrix(config, matrix, run).run_all()
    except MatrixError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
        print('Failure matrix stopped; inspect the private run directory', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
