#!/usr/bin/env python3
"""Coordinate reconciliation of two existing, explicitly selected test hosts."""

import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import yaml

import lab_runtime
from lab_backup_client import write_private
import lab_contracts
from lab_capture_guard import CaptureGuard, PROGRAM as CAPTURE_GUARD_PROGRAM
from ssh_target import pinned_ssh, private_file

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('hou1', 'hou4')
SHA = lab_contracts.SHA
GHCR_IMAGE = lab_contracts.GHCR_IMAGE


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


def load_config(path):
    config = json.loads(private_file(path).read_text())
    version = config.get('version')
    require(type(version) is int and version in (1, 2) and config.get('environment') == 'test',
            'Only version 1 or 2 test workflows are supported')
    release_field = 'application' if version == 1 else 'release'
    require(set(config) == {'version', 'environment', 'namespace', 'management_url',
            release_field, 'ansible_bin_directory', 'collections_path', 'age_key_file',
            'run_directory', 'hosts', 'https_checks'}, 'Declare the complete workflow configuration')
    if version == 2:
        config['application'] = lab_contracts.load_release(config['release'])
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
        fields = {'repository', 'revision', 'inventory', 'inputs', 'target',
                  'hostname', 'ssh', 'ssh_host_ip', 'ssh_fingerprint', 'compose'}
        require(set(host) == fields | ({'capabilities'} if version == 2 else set()),
                'Declare the complete host contract')
        if version == 2:
            require(isinstance(host['capabilities'], list), 'Declare additional referenced capability files as a list')
            for source in host['capabilities']:
                private_file(source)
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
            require(set(project) <= {'directory', 'project', 'file', 'services'} and 'directory' in project,
                    'Use declared Compose directories with optional project/file selection')
            require(re.fullmatch(r'/[A-Za-z0-9_./-]+', project['directory']) and
                    '..' not in Path(project['directory']).parts, 'Use absolute runtime directories')
            if 'project' in project:
                require(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project['project']), 'Invalid Compose project name')
            if 'file' in project:
                require(re.fullmatch(r'[A-Za-z0-9_.-]+\.ya?ml', project['file']), 'Use a Compose filename within its directory')
            if 'services' in project:
                require(isinstance(project['services'], list) and bool(project['services']) and
                        all(isinstance(service, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', service)
                            for service in project['services']) and
                        len(set(project['services'])) == len(project['services']),
                        'Declare unique Compose services when selecting a project subset')
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
               ANSIBLE_HOST_PATTERN_MISMATCH='error',
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
    if config['version'] == 2:
        selection = contract_selection(service)
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


def contract_selection(service):
    """Select only contract inputs; never serialize passwords, tokens or all vars."""
    files = '{' + ', '.join(repr(name) + ': ' + name + " | default('')"
                           for name in lab_contracts.CAPABILITY_VARIABLES[service]) + '}'
    fields = {'files': files, 'backup_enabled':
              ('hou1_backup_enabled' if service == 'hou1' else 'hou4_backup_enabled') + ' | default(false) | bool'}
    if service == 'hou1':
        fields.update(identity_profile='authentik_identity_profiles[authentik_environment]',
                      staff_client_id='staff_oidc_client_id', staff_issuer='staff_oidc_issuer',
                      netbird_client_id='netbird_authentik_client_id',
                      staff_users="authentik_staff_users | default([]) | map('dict2items') "
                                  "| map('selectattr', 'key', 'in', ['username', 'groups']) | map('items2dict') | list",
                      authentik_backup_recipient="authentik_backup_age_recipient | default('')",
                      backup_recipient="hou1_backup_recipient | default('')",
                      backup_receiver_ip="hou1_backup_receiver_ip | default('')",
                      source_bundle='rehearsal_staff_bundle', bundle_ref='rehearsal_staff_bundle_ref',
                      source_revision='rehearsal_staff_ref')
    else:
        fields['backup_source_ip'] = "hou4_backup_source_ip | default('')"
        fields['backup_source_host_key'] = "hou4_backup_source_host_key | default('')"
    contracts = '{' + ', '.join(repr(name) + ': ' + expr for name, expr in fields.items()) + '}'
    source = ', "source": app_source_directory' if service == 'hou1' else ''
    return '{{ {"interface": netbird_interface' + source + ', "contracts": ' + contracts + '} | to_json }}'


class Workflow:
    def __init__(self, config, run):
        self.config, self.run = config, run
        self.results, self.transports, self.selections = [], {}, {}
        self.inputs = {s: input_fingerprints(h) for s, h in config['hosts'].items()}
        self.capabilities = {}
        if config['version'] == 2:
            self.capabilities = lab_contracts.fingerprints(
                [config['age_key_file'], *(config['release'][name] for name in ('receipt', 'source_bundle'))]
                + [p for host in config['hosts'].values() for p in host['capabilities']])
        self.phase = None
        self.status = 'running'
        self.capture = None

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
        value = {'version': 1, 'environment': 'test',
            'scope': 'managed test hosts', 'phase': self.phase, 'status': self.status,
            'configuration_sha256': hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest(),
            'inputs': self.inputs,
            'revisions': {s: h['revision'] for s, h in self.config['hosts'].items()},
            'application': self.config['application'], 'steps': self.results}
        if self.config['version'] == 2:
            value['capabilities'] = self.capabilities
        if hasattr(self, 'finalization'):
            value['finalization'] = self.finalization
        write_private(temporary, json.dumps(value, indent=2) + '\n')
        temporary.replace(path)

    def check_inputs(self):
        if self.capture is not None:
            self.capture.check()
        for service, host in self.config['hosts'].items():
            check_revision(host)
            require(input_fingerprints(host) == self.inputs[service],
                    'Deployment inputs changed during this attempt; rerun preflight')
        require(lab_contracts.fingerprints(self.capabilities) == self.capabilities,
                'Referenced capability files changed during this attempt; rerun input checks')

    def playbook(self, service, play, log_name, local=False, extra_args=()):
        host = self.config['hosts'][service]
        self.check_inputs()
        require(transport(host) == self.transports[service], 'SSH settings changed during this attempt')
        argv = [str(Path(self.config['ansible_bin_directory']) / 'ansible-playbook'),
                '-i', host['inventory'], str(play), '--limit', host['target']]
        for source in host['inputs']:
            argv += ['-e', '@' + source]
        fixed, user, port = self.transports[service]
        overrides = {'target_hosts': host['target']}
        overrides['identity_admission_capture_admitted'] = self.capture is not None
        if local:
            overrides.update(ansible_connection='local', ansible_become=False)
        else:
            # The security role launches its own delegated SSH command. Bind it
            # to this same verified transport, including over older extra-vars.
            overrides['security_netbird_ssh_options'] = fixed[1:]
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
        argv += ['-e', json.dumps(overrides), *extra_args]
        log = self.run / (log_name + '.log')
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            run = self.capture.run if self.capture is not None and not local else subprocess.run
            result = run(argv, cwd=host['repository'], env=environment(self.config, host),
                         stdout=stream, stderr=subprocess.STDOUT)
        require(result.returncode == 0, f'{log_name} failed; see the private run log')

    def prepare_inputs(self, service):
        host = self.config['hosts'][service]
        fixed, user, port = transport(host)
        self.transports[service] = fixed, user, port
        guard = self.run / (service + '-guard.yml')
        selection = self.run / (service + '-selection.json')
        write_private(guard, yaml.safe_dump(guard_play(self.config, service, user, port, selection)))
        self.playbook(service, guard, service + '-inputs', local=True)
        require(selection.is_file(), 'The inventory selected no host; preflight did not run')
        self.selections[service] = json.loads(selection.read_text())

    def preflight(self, service):
        self.prepare_inputs(service)
        self.probe(service, verify=False)

    def validate_contracts(self):
        self.check_inputs()
        paths = lab_contracts.capability_paths(self.config, self.selections)
        effective = lab_contracts.fingerprints(paths)
        require(all(effective.get(p) == value for p, value in self.capabilities.items()),
                'Referenced capability files changed before contract validation')
        self.capabilities = effective
        lab_contracts.validate(self.config, self.selections,
                               environment(self.config, self.config['hosts']['hou1']))
        self.check_inputs()

    def probe(self, service, verify=True, handoff=False):
        host = self.config['hosts'][service]
        self.check_inputs()
        fixed, _, _ = self.transports[service]
        require(transport(host) == self.transports[service], 'SSH settings changed during this attempt')
        contract = {'hostname': host['hostname'], 'address': host['ssh_host_ip'],
                    'interface': self.selections[service]['interface'], 'verify': verify, 'compose': host['compose'],
                    'tls': service == 'hou1', 'https': self.config['https_checks'] if service == 'hou4' else []}
        if handoff:
            contract.update(handoff=True, service=service)
        if service == 'hou1':
            contract.update(source=self.selections[service]['source'], application=self.config['application'])
        program = Path(lab_runtime.__file__).read_text()
        command = [*fixed, host['ssh_host_ip'], shlex.join(['sudo', '-n', 'python3', '-c', program])]
        result = subprocess.run(command, input=json.dumps(contract), capture_output=True, text=True, timeout=300)
        require(result.returncode == 0, service + ' private access or readiness check failed')
        report = json.loads(result.stdout)
        require(not report.get('failed'), service + ' private access or readiness check failed')
        suffix = '-handoff.json' if handoff else '-runtime.json' if verify else '-access.json'
        path = self.run / (service + suffix)
        write_private(path, json.dumps(report, indent=2) + '\n')

    def backup_transport(self, service):
        return transport(self.config['hosts'][service])

    @contextmanager
    def capture_guard(self, name="capture-admission"):
        self.check_inputs()
        host = self.config['hosts']['hou1']
        fixed, _, _ = self.transports['hou1']
        require(transport(host) == self.transports['hou1'], 'SSH settings changed before capture admission')
        command = [*fixed, '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                   host['ssh_host_ip'], shlex.join(['sudo', '-n', 'python3', '-u', '-c',
                                                  CAPTURE_GUARD_PROGRAM, '--hold'])]
        with CaptureGuard(command, self.run / (name + '.log')) as guard:
            self.capture = guard
            try:
                yield
            finally:
                self.capture = None

    def execute(self, phase, recovery_identity=None, resume=None):
        self.phase = phase
        if phase == 'finalize':
            require(self.config['version'] == 2 and recovery_identity,
                    'Finalization requires version 2 inputs and --recovery-identity')
            private_file(recovery_identity)
        if self.config['version'] == 2:
            for service in SERVICES:
                self.step(service + '-inputs', lambda s=service: self.prepare_inputs(s))
            self.step('input-contracts', self.validate_contracts)
        else:
            require(phase != 'inputs', 'The inputs phase requires a version 2 workflow')
            for service in SERVICES:
                self.step(service + '-preflight', lambda s=service: self.preflight(s))
        if phase == 'finalize':
            from lab_finalize import Finalization
            finalization = Finalization(self, recovery_identity, resume)
            for service in SERVICES:
                self.step(service + '-preflight', lambda s=service: self.probe(s, verify=False))
            finalization.execute()
            self.status = 'passed'
            self.save()
            return
        with ExitStack() as stack:
            if phase == 'reconcile':
                self.step('capture-admission', lambda: stack.enter_context(self.capture_guard()))
            if self.config['version'] == 2 and phase != 'inputs':
                for service in SERVICES:
                    self.step(service + '-preflight', lambda s=service: self.probe(s, verify=False))
            if phase == 'handoff':
                for service in SERVICES:
                    self.step(service + '-handoff', lambda s=service: self.probe(s, verify=False, handoff=True))
            if phase not in ('preflight', 'inputs', 'handoff'):
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
    parser.add_argument('--phase', choices=('plan', 'inputs', 'handoff', 'preflight', 'reconcile', 'verify', 'finalize'), default='plan')
    parser.add_argument('--recovery-identity', help='Controller-only offline age identity; required for finalize')
    parser.add_argument('--resume', help='Previous private finalization receipt; recheck inputs and reuse its point')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config = load_config(args.config)
        require(args.phase == 'finalize' or not (args.recovery_identity or args.resume),
                'Recovery identity and resume apply only to finalize')
        if args.phase == 'plan':
            inputs = ['hou1-inputs', 'hou4-inputs', 'input-contracts'] if config['version'] == 2 else []
            access = ['hou1-preflight', 'hou4-preflight']
            admission = ['capture-admission']
            print(json.dumps({'scope': 'managed test hosts; no bootstrap',
                              'sequence': inputs + (admission + access if inputs else access + admission) + ['hou1-reconcile',
                                           'hou1-verify', 'hou4-reconcile', 'hou4-verify']}, indent=2))
            return 0
        with (Path(config['run_directory']) / '.workflow.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),
                                        dir=config['run_directory']))
            print('Private run directory: ' + str(run), flush=True)
            runner = Workflow(config, run)
            try:
                if args.phase == 'finalize':
                    runner.execute(args.phase, args.recovery_identity, args.resume)
                else:
                    runner.execute(args.phase)
            except BaseException:
                runner.status = 'failed'
                runner.save()
                raise
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
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
