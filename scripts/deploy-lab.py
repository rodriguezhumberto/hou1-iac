#!/usr/bin/env python3
"""Coordinate reconciliation of two existing, explicitly selected test hosts."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import yaml

import lab_runtime
from ssh_target import pinned_ssh, private_file

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('hou1', 'hou4')
SHA = r'[a-f0-9]{40}'
GHCR_IMAGE = (r'ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*'
              r'(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[a-f0-9]{64}')


class WorkflowError(ValueError):
    """A declared prerequisite or workflow step failed."""


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def directory(value):
    path = Path(value).expanduser()
    require(path.is_absolute() and path.is_dir() and not path.is_symlink(),
            'Use existing absolute directories')
    return path


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def load_config(path):
    config = json.loads(private_file(path).read_text())
    require(set(config) == {'version', 'environment', 'namespace', 'management_url',
            'application', 'ansible_bin_directory', 'collections_path', 'age_key_file',
            'run_directory', 'hosts', 'https_checks'}, 'Declare the complete workflow configuration')
    require(type(config['version']) is int and config['version'] == 1 and config['environment'] == 'test',
            'Only version 1 test workflows are supported')
    namespace = config['namespace']
    require(isinstance(namespace, str) and 'test' in namespace and
            len(namespace) <= 253 and
            re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', namespace),
            'Declare a test DNS namespace')
    require(config['management_url'] == 'https://netbird-test.' + namespace,
            'Select the namespace-specific HTTPS management origin')
    require(set(config['application']) == {'revision', 'image'} and
            re.fullmatch(SHA, config['application']['revision']) and
            re.fullmatch(GHCR_IMAGE, config['application']['image']),
            'Declare the immutable application revision and GHCR digest reference')
    private_file(config['age_key_file'])
    binaries = directory(config['ansible_bin_directory'])
    require(os.access(binaries / 'ansible-playbook', os.X_OK), 'The selected Ansible executable is missing')
    directory(config['collections_path'])
    require(set(config['hosts']) == set(SERVICES), 'Declare exactly HOU1 and HOU4')
    for service, host in config['hosts'].items():
        require(set(host) == {'repository', 'revision', 'inventory', 'inputs', 'target',
                'hostname', 'ssh', 'ssh_host_ip', 'ssh_fingerprint', 'compose'},
                'Declare the complete host contract')
        repo = directory(host['repository'])
        require(re.fullmatch(SHA, host['revision']) and
                (repo / f'ansible/playbooks/{service}.yml').is_file() and
                (repo / 'ansible/playbooks/tasks/validate-target.yml').is_file() and
                (repo / 'ansible/ansible.cfg').is_file(), 'Select a supported repository and immutable revision')
        private_file(host['inventory'])
        require(isinstance(host['inputs'], list), 'Declare an ordered list of private extra-variable files')
        for source in host['inputs']:
            values = yaml.safe_load(private_file(source).read_text())
            require(isinstance(values, dict) and all(isinstance(k, str) and not k.startswith('ansible_')
                    for k in values), 'Keep Ansible connection and execution variables in inventory, not extra-vars')
        require(re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*', host['target']) and
                host['target'] != 'localhost' and
                re.fullmatch(r'[a-z0-9][a-z0-9-]*', host['hostname']), 'Declare one inventory alias and expected hostname')
        address = ipaddress.ip_address(host['ssh_host_ip'])
        require(address in ipaddress.ip_network('100.91.0.0/16'), 'Use the test overlay, never bootstrap or production addresses')
        require(isinstance(host['compose'], list) and bool(host['compose']), 'Declare the Compose projects to verify')
        for project in host['compose']:
            require(set(project) <= {'directory', 'project', 'file'} and 'directory' in project,
                    'Use declared Compose directories with optional project/file selection')
            require(re.fullmatch(r'/[A-Za-z0-9_./-]+', project['directory']) and
                    '..' not in Path(project['directory']).parts, 'Use absolute runtime directories')
            if 'project' in project:
                require(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project['project']), 'Invalid Compose project name')
            if 'file' in project:
                require(re.fullmatch(r'[A-Za-z0-9_.-]+\.ya?ml', project['file']), 'Use a Compose filename within its directory')
    require(Path(config['hosts']['hou1']['repository']).resolve() == ROOT,
            'Run the workflow from the selected HOU1 checkout')
    for field in ('repository', 'inventory', 'target', 'hostname', 'ssh_host_ip'):
        require(config['hosts']['hou1'][field] != config['hosts']['hou4'][field], 'Keep the two host identities separate')
    require(isinstance(config['https_checks'], list) and bool(config['https_checks']), 'Declare end-to-end HTTPS checks')
    for check in config['https_checks']:
        require(set(check) == {'url', 'status'}, 'Declare a URL and exact expected status')
        url = urlsplit(check['url'])
        require(url.scheme == 'https' and url.hostname and url.hostname.endswith('.' + namespace)
                and not url.username and not url.password and not url.fragment and not url.query
                and url.port in (None, 443) and type(check['status']) is int
                and 200 <= check['status'] < 500 and not 300 <= check['status'] < 400,
                'HTTPS checks must remain within the selected namespace without credentials or redirects')
    run_root = directory(config['run_directory'])
    require(run_root.stat().st_uid == os.getuid() and not run_root.stat().st_mode & 0o077,
            'The run directory must be operator-owned and mode 0700')
    require(not any(run_root.resolve().is_relative_to(Path(h['repository']).resolve())
                    for h in config['hosts'].values()), 'Keep private run logs outside both repositories')
    return config


def environment(config, host):
    # Select the repository's configuration, rather than inheriting another
    # project's plugin paths, SSH overrides, logging or decryption identities.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ANSIBLE_', 'SOPS_'))}
    repo = Path(host['repository'])
    env.update(ANSIBLE_CONFIG=str(repo / 'ansible/ansible.cfg'),
               ANSIBLE_COLLECTIONS_PATH=config['collections_path'],
               ANSIBLE_COLLECTIONS_SCAN_SYS_PATH='False', ANSIBLE_HOST_KEY_CHECKING='True',
               ANSIBLE_ROLES_PATH=str(repo / 'ansible/playbooks/roles'),
               ANSIBLE_FILTER_PLUGINS=str(repo / 'ansible/playbooks/filter_plugins'),
               ANSIBLE_ACTION_PLUGINS=str(repo / 'ansible/playbooks/action_plugins'),
               ANSIBLE_NOCOLOR='1', SOPS_AGE_KEY_FILE=config['age_key_file'],
               PATH=config['ansible_bin_directory'] + os.pathsep + os.environ['PATH'])
    return env


def check_revision(host):
    def git(*args):
        return subprocess.check_output(['git', '-C', host['repository'], *args], text=True,
                                       stderr=subprocess.DEVNULL, timeout=15).strip()
    require(git('rev-parse', 'HEAD') == host['revision'], 'Repository HEAD differs from the declared revision')
    require(not git('status', '--porcelain', '--untracked-files=no'), 'Commit or isolate tracked changes before execution')
    require(not git('ls-files', '--others', '--exclude-standard', '--', 'ansible', 'scripts'),
            'Untracked deployment code prevents a reproducible execution')


def input_fingerprints(host):
    inventory = Path(host['inventory'])
    sources = {inventory, *(Path(p) for p in host['inputs'])}
    for name in ('group_vars', 'host_vars'):
        root = inventory.parent / name
        if root.exists():
            sources.update(p for p in root.rglob('*') if p.is_file())
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}


def transport(host):
    pinned = pinned_ssh({'environment': 'test', **host})
    parsed = subprocess.check_output(['ssh', '-G', *pinned[1:]], text=True,
                                     stderr=subprocess.DEVNULL, timeout=15).splitlines()
    values = dict(line.split(' ', 1) for line in parsed if ' ' in line)
    identities = [line.split(' ', 1)[1] for line in parsed if line.startswith('identityfile ')]
    require(len(identities) == 1 and '%' not in identities[0], 'Use one explicit SSH IdentityFile')
    identity = Path(identities[0]).expanduser()
    require(identity.is_absolute() and identity.is_file(), 'The SSH identity file is missing')
    require(values.get('proxycommand', 'none') == 'none' and values.get('proxyjump', 'none') == 'none',
            'Run within the lab transport; SSH proxy commands are not supported')
    # Freeze the same checked key, user and address for both probes and Ansible.
    # Do not depend on an alias-only SSH block also matching an inventory IP.
    fixed = ['ssh', '-F', '/dev/null', *pinned[3:-1], '-i', str(identity)]
    return fixed, values['user'], int(values['port'])


def guard_play(config, service, user, port, destination):
    host = config['hosts'][service]
    tests = ["inventory_hostname in groups.get('test_env', [])",
             'ansible_play_hosts_all | length == 1', 'not (node_bootstrap | default(true) | bool)',
             f'ansible_host == {host["ssh_host_ip"]!r}', f'netbird_ip == {host["ssh_host_ip"]!r}',
             f'ansible_user == {user!r}', f'(ansible_port | default(22) | int) == {port}',
             "netbird_subnet == '100.91.0.0/16'", f'netbird_management_url == {config["management_url"]!r}',
             "netbird_interface is match('^[a-zA-Z0-9_-]{1,15}$')"]
    if service == 'hou1':
        tests += [f'application_image_revision == {config["application"]["revision"]!r}',
                  f'application_image_reference == {config["application"]["image"]!r}',
                  '(deploy_ref | default(application_image_revision)) == application_image_revision',
                  'hou1_controller_managed_tls | default(false) | bool',
                  "app_source_directory is match('^/')", 'app_source_directory != app_directory']
    selection = ('{{ {"interface": netbird_interface, "source": app_source_directory} | to_json }}'
                 if service == 'hou1' else '{{ {"interface": netbird_interface} | to_json }}')
    return [{'name': 'Validate managed lab inputs without host connections', 'hosts': host['target'],
             'gather_facts': False, 'become': False, 'tasks': [
                 {'ansible.builtin.import_tasks': str(Path(host['repository']) / 'ansible/playbooks/tasks/validate-target.yml')},
                 {'name': 'Bind the workflow to the selected managed test host',
                  'ansible.builtin.assert': {'that': tests}, 'no_log': True},
                 {'name': 'Record the selected interface after successful guards',
                  'ansible.builtin.copy': {'content': selection,
                                           'dest': str(destination), 'mode': '0600'},
                  'delegate_to': 'localhost', 'no_log': True},
             ]}]


class Workflow:
    def __init__(self, config, run):
        self.config, self.run = config, run
        self.results, self.transports, self.selections = [], {}, {}
        self.inputs = {s: input_fingerprints(h) for s, h in config['hosts'].items()}
        self.phase = None
        self.status = 'running'

    def step(self, name, operation):
        record = {'step': name, 'status': 'running'}
        self.results.append(record)
        self.save()
        print(name, flush=True)
        try:
            operation()
        except BaseException:
            record['status'] = 'failed'
            self.status = 'failed'
            self.save()
            raise
        record['status'] = 'passed'
        self.save()

    def save(self):
        path = self.run / 'receipt.json'
        temporary = path.with_suffix('.tmp')
        write_private(temporary, json.dumps({'version': 1, 'environment': 'test',
            'scope': 'managed test hosts', 'phase': self.phase, 'status': self.status,
            'configuration_sha256': hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest(),
            'inputs': self.inputs,
            'revisions': {s: h['revision'] for s, h in self.config['hosts'].items()},
            'application': self.config['application'], 'steps': self.results}, indent=2) + '\n')
        temporary.replace(path)

    def playbook(self, service, play, log_name, local=False):
        host = self.config['hosts'][service]
        check_revision(host)
        require(input_fingerprints(host) == self.inputs[service], 'Deployment inputs changed during this attempt; rerun preflight')
        require(transport(host) == self.transports[service], 'SSH settings changed during this attempt')
        argv = [str(Path(self.config['ansible_bin_directory']) / 'ansible-playbook'),
                '-i', host['inventory'], str(play), '--limit', host['target']]
        for source in host['inputs']:
            argv += ['-e', '@' + source]
        fixed, user, port = self.transports[service]
        overrides = {'target_hosts': host['target']}
        if local:
            overrides.update(ansible_connection='local', ansible_become=False)
        else:
            # Extra-vars also affect delegated tasks. Freeze the remote transport
            # only on its inventory host and retain local controller execution.
            frozen = {'ansible_connection': 'ssh', 'ansible_ssh_executable': 'ssh',
                      'ansible_ssh_common_args': shlex.join(fixed[1:]),
                      'ansible_ssh_extra_args': '', 'ansible_ssh_args': '',
                      'ansible_host': host['ssh_host_ip'], 'ansible_ssh_host': host['ssh_host_ip'],
                      'ansible_user': user, 'ansible_ssh_user': user, 'ansible_port': port,
                      'ansible_ssh_port': port, 'ansible_private_key_file': fixed[-1],
                      'ansible_ssh_private_key_file': fixed[-1]}
            overlay = self.run / (log_name + '-transport.json')
            # Use the last inventory's host_vars too: existing host_vars take
            # precedence over host variables embedded in inventory itself.
            variables = self.run / 'host_vars'
            variables.mkdir(mode=0o700, exist_ok=True)
            write_private(variables / (host['target'] + '.json'), json.dumps(frozen))
            write_private(variables / 'localhost.json', json.dumps({
                'ansible_connection': 'local', 'ansible_host': '127.0.0.1',
                'ansible_ssh_host': '127.0.0.1',
                'ansible_python_interpreter': '{{ ansible_playbook_python }}'}))
            write_private(overlay, json.dumps({'all': {'hosts': {host['target']: {}, 'localhost': {}}}}))
            argv += ['-i', str(overlay)]
        argv += ['-e', json.dumps(overrides)]
        log = self.run / (log_name + '.log')
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            result = subprocess.run(argv, cwd=host['repository'], env=environment(self.config, host),
                                    stdout=stream, stderr=subprocess.STDOUT)
        require(result.returncode == 0, f'{log_name} failed; see the private run log')

    def preflight(self, service):
        host = self.config['hosts'][service]
        fixed, user, port = transport(host)
        self.transports[service] = fixed, user, port
        guard = self.run / (service + '-guard.yml')
        selection = self.run / (service + '-selection.json')
        write_private(guard, yaml.safe_dump(guard_play(self.config, service, user, port, selection)))
        self.playbook(service, guard, service + '-inputs', local=True)
        require(selection.is_file(), 'The inventory selected no host; preflight did not run')
        self.selections[service] = json.loads(selection.read_text())
        self.probe(service, verify=False)

    def probe(self, service, verify=True):
        host = self.config['hosts'][service]
        check_revision(host)
        require(input_fingerprints(host) == self.inputs[service], 'Deployment inputs changed during this attempt; rerun preflight')
        fixed, _, _ = self.transports[service]
        require(transport(host) == self.transports[service], 'SSH settings changed during this attempt')
        contract = {'hostname': host['hostname'], 'address': host['ssh_host_ip'],
                    'interface': self.selections[service]['interface'], 'verify': verify, 'compose': host['compose'],
                    'tls': service == 'hou1', 'https': self.config['https_checks'] if service == 'hou4' else []}
        if service == 'hou1':
            contract.update(source=self.selections[service]['source'], application=self.config['application'])
        program = Path(lab_runtime.__file__).read_text()
        command = [*fixed, host['ssh_host_ip'], shlex.join(['sudo', '-n', 'python3', '-c', program])]
        result = subprocess.run(command, input=json.dumps(contract), capture_output=True, text=True, timeout=300)
        require(result.returncode == 0, service + ' private access or readiness check failed')
        report = json.loads(result.stdout)
        require(not report.get('failed'), service + ' private access or readiness check failed')
        path = self.run / (service + ('-runtime.json' if verify else '-access.json'))
        write_private(path, json.dumps(report, indent=2) + '\n')

    def execute(self, phase):
        self.phase = phase
        for service in SERVICES:
            self.step(service + '-preflight', lambda s=service: self.preflight(s))
        if phase != 'preflight':
            for service in SERVICES:
                if phase == 'reconcile':
                    play = Path(self.config['hosts'][service]['repository']) / f'ansible/playbooks/{service}.yml'
                    self.step(service + '-reconcile', lambda s=service, p=play: self.playbook(s, p, s + '-reconcile'))
                self.step(service + '-verify', lambda s=service: self.probe(s))
        self.status = 'passed'
        self.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--phase', choices=('plan', 'preflight', 'reconcile', 'verify'), default='plan')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config = load_config(args.config)
        if args.phase == 'plan':
            print(json.dumps({'scope': 'managed test hosts; no bootstrap',
                              'sequence': ['hou1-preflight', 'hou4-preflight', 'hou1-reconcile',
                                           'hou1-verify', 'hou4-reconcile', 'hou4-verify']}, indent=2))
            return 0
        with (Path(config['run_directory']) / '.workflow.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),
                                        dir=config['run_directory']))
            print('Private run directory: ' + str(run), flush=True)
            Workflow(config, run).execute(args.phase)
    except WorkflowError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Workflow interrupted; inspect private logs before retrying', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, subprocess.SubprocessError):
        print('Workflow failed; check the declared inputs and private run logs', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
