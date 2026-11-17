#!/usr/bin/env python3
"""Render private lab inventories and select managed access after a fresh check."""

import argparse
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

from ssh_target import private_file

SPEC = importlib.util.spec_from_file_location('lab_workflow', Path(__file__).with_name('deploy-lab.py'))
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_mapping(path):
    value = yaml.safe_load(private_file(path).read_text())
    require(isinstance(value, dict), 'Declare private variables as a mapping')
    return value


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    workflow.write_private(path, value)


def bindings(config, service, host):
    values = {'netbird_ip': host['ssh_host_ip'], 'netbird_subnet': '100.91.0.0/16',
              'netbird_server_group': 'test-servers', 'netbird_management_url': config['management_url']}
    if service == 'hou1':
        values.update(cms_environment='test', authentik_environment='test', deploy_environment='test')
    else:
        values.update(hou4_environment='test')
    return values


def render(source, destination):
    source = private_file(source)
    declaration = json.loads(source.read_text())
    require(set(declaration) == {'version', 'environment', 'workflow', 'hosts'}
            and type(declaration['version']) is int and declaration['version'] == 1
            and declaration['environment'] == 'test'
            and set(declaration['hosts']) == set(workflow.SERVICES),
            'Declare one version 1 test inventory source for both hosts')
    template = private_file(declaration['workflow'])
    config = workflow.load_config(template)
    require(config['version'] == 2, 'Inventory generation requires a version 2 workflow')
    for host in config['hosts'].values(): workflow.check_revision(host)
    # Preserve the public schema; application is derived by the workflow loader.
    rendered = json.loads(template.read_text())
    output = Path(destination)
    require(output.is_absolute() and '..' not in output.parts and not output.exists()
            and not output.is_symlink(), 'Render into a new absolute snapshot directory')
    require(not any(output.resolve().is_relative_to(Path(host['repository']).resolve())
                    for host in config['hosts'].values()), 'Keep generated private inventories outside repositories')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sources = {str(source): digest(source), str(template): digest(template)}
    with tempfile.TemporaryDirectory(prefix='.lab-render-', dir=output.parent) as folder:
        temporary = Path(folder)
        for service, host in config['hosts'].items():
            selected = declaration['hosts'][service]
            require(set(selected) == {'variables', 'group_vars', 'bootstrap_address',
                                     'bootstrap_target', 'bootstrap_user'},
                    'Declare explicit variable sources and bootstrap identity for each host')
            variables = private_file(selected['variables'])
            sources[str(variables)] = digest(variables)
            values = read_mapping(variables)
            require(set(values) == {'all', 'environment'}
                    and all(isinstance(v, dict) for v in values.values()),
                    'Separate shared overrides from environment variables')
            all_vars, environment_vars = values['all'], values['environment']
            require(all(isinstance(k, str) for v in values.values() for k in v), 'Variable names must be strings')
            bootstrap = str(ipaddress.IPv4Address(selected['bootstrap_address']))
            require(any(ipaddress.ip_address(bootstrap) in ipaddress.ip_network(cidr)
                        for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')),
                    'Select an RFC1918 bootstrap address for the isolated VM')
            require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', selected['bootstrap_target'])
                    and selected['bootstrap_target'] not in (host['target'], 'localhost')
                    and re.fullmatch(r'[a-z_][a-z0-9_-]*', selected['bootstrap_user'])
                    and selected['bootstrap_user'] != 'root', 'Declare a distinct bootstrap alias and non-root user')
            fixed = {**bindings(config, service, host), 'test_vps_public_ip': bootstrap}
            for name, value in fixed.items():
                for scope in (all_vars, environment_vars):
                    require(name not in scope or scope[name] == value, 'A source variable contradicts the lab target contract')
                    scope.pop(name, None)
            require(not any(name in scope for name in ('ansible_host', 'node_bootstrap')
                            for scope in (all_vars, environment_vars)),
                    'Connection address and bootstrap state belong to generated profiles')
            environment_vars.update(fixed)
            if service == 'hou1':
                release = {'application_image_reference': config['application']['image'],
                           'application_image_revision': config['application']['revision'],
                           'deploy_ref': config['application']['revision'],
                           'rehearsal_staff_ref': config['application']['revision'],
                           'rehearsal_staff_bundle': config['release']['source_bundle'],
                           'rehearsal_staff_bundle_ref': config['release']['bundle_ref']}
                require(not any(name in scope for name in release for scope in (all_vars, environment_vars)),
                        'Release bindings come from the workflow receipt and bundle, not variable copies')
                environment_vars.update(release)
            root = temporary / service
            shared = Path(host['repository']) / 'ansible/inventory/group_vars/all/vars.yml'
            require(shared.is_file(), 'The selected repository shared-variable source is missing')
            sources[str(shared)] = digest(shared)
            # Live references preserve the repository owner; copied defaults would
            # become hidden overrides on every later repository update.
            target = root / 'group_vars/all/00-repository.yml'
            target.parent.mkdir(mode=0o700, parents=True)
            target.symlink_to(shared)
            write(root / 'group_vars/all/90-overrides.yml', yaml.safe_dump(all_vars, sort_keys=False))
            write(root / 'group_vars/test_env/00-environment.yml',
                  yaml.safe_dump(environment_vars, sort_keys=False))
            require(isinstance(selected['group_vars'], dict), 'Declare scoped group-variable file references')
            for name, path in selected['group_vars'].items():
                require(re.fullmatch(r'test_env(?:\.sops)?\.(?:json|ya?ml)', name),
                        'Only scoped test_env group-variable references are supported')
                private_file(path); sources[str(path)] = digest(path)
                # Keep ciphertext at the plugin's exact sibling filename. The
                # regular vars plugin reads every YAML file inside group dirs.
                target = (root / 'group_vars' / name if '.sops.' in name else
                          root / 'group_vars/test_env' / ('90-' + name))
                target.symlink_to(path)
            for mode in ('bootstrap', 'managed'):
                initial = mode == 'bootstrap'
                alias = selected['bootstrap_target'] if initial else host['target']
                group = service + ('_test_init' if initial else '_test')
                connection = {'ansible_host': bootstrap if initial else host['ssh_host_ip'],
                              'node_bootstrap': initial}
                if initial: connection['ansible_user'] = selected['bootstrap_user']
                inventory = {'all': {'children': {'test_env': {
                    'children': {group: {'hosts': {alias: connection}}}}}}}
                if service == 'hou1':
                    inventory['all']['children']['isolated_test_vps'] = {'hosts': {alias: {}}}
                write(root / (mode + '-inventory.yml'), '# Generated by scripts/prepare-lab.py.\n'
                      + yaml.safe_dump(inventory, sort_keys=False))
            rendered['hosts'][service]['inventory'] = str(output / service / 'managed-inventory.yml')
        require(len({item['bootstrap_address'] for item in declaration['hosts'].values()}) == 2,
                'Keep bootstrap hosts distinct')
        require(all(digest(path) == value for path, value in sources.items()),
                'An input changed while rendering; retry with unchanged sources')
        write(temporary / 'workflow.json', json.dumps(rendered, indent=2) + '\n')
        outputs = {str(path.relative_to(temporary)): digest(path)
                   for path in temporary.rglob('*') if path.is_file()}
        write(temporary / 'render.json', json.dumps({'version': 1, 'environment': 'test',
            'sources': sources, 'outputs': outputs}, indent=2) + '\n')
        temporary.rename(output)
    return output / 'workflow.json'


def check_snapshot(path):
    path = private_file(path)
    root = path.parent
    record = json.loads(private_file(root / 'render.json').read_text())
    require(record['version'] == 1 and record['environment'] == 'test', 'Select a generated lab snapshot')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name != 'render.json'}
    require(actual == set(record['outputs']), 'The inventory snapshot has unexpected or missing files')
    for name, value in record['outputs'].items():
        item = Path(name)
        require(not item.is_absolute() and '..' not in item.parts and digest(root / item) == value,
                'The generated snapshot changed; render a new snapshot')
    return workflow.load_config(path)


def handoff(snapshot, active):
    active = private_file(active)
    require(not active.resolve().is_relative_to(Path(snapshot).parent.resolve()),
            'Keep the active manifest outside the generated snapshot')
    lock_path = active.with_name(active.name + '.lock')
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = check_snapshot(snapshot)
        previous = active.read_bytes()
        old = workflow.load_config(active)
        require(old['run_directory'] == config['run_directory'],
                'Retain the shared workflow run directory during handoff')
        for service in workflow.SERVICES:
            for name in ('hostname', 'ssh_host_ip', 'ssh_fingerprint'):
                require(old['hosts'][service][name] == config['hosts'][service][name],
                        'The active and generated workflows select different hosts; establish a separate lab manifest')
        run_lock = Path(config['run_directory']) / '.workflow.lock'
        descriptor = os.open(run_lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as running:
            fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
            activate(config, snapshot, active, previous)


def activate(config, snapshot, active, previous):
    run = Path(tempfile.mkdtemp(prefix='handoff-', dir=config['run_directory']))
    print('Private handoff run: ' + str(run), flush=True)
    runner = workflow.Workflow(config, run)
    runner.execute('handoff')
    check_snapshot(snapshot)
    runner.check_inputs()
    require(active.read_bytes() == previous, 'The active manifest changed during handoff')
    # The snapshot stays immutable. Only the small active manifest changes,
    # and only after both fresh VPN policy probes have succeeded.
    def select():
        write(run / 'previous-workflow.json', previous.decode())
        descriptor, temporary = tempfile.mkstemp(prefix='.lab-active-', dir=active.parent)
        os.close(descriptor)
        try:
            workflow.write_private(temporary, private_file(snapshot).read_text())
            os.replace(temporary, active)
        finally:
            Path(temporary).unlink(missing_ok=True)
        write(run / 'activation.json', json.dumps({'version': 1, 'snapshot': str(snapshot),
            'previous_sha256': hashlib.sha256(previous).hexdigest(), 'active_sha256': digest(active)}, indent=2) + '\n')
    runner.status = 'running'
    runner.step('select-managed-inventory', select)
    runner.status = 'passed'
    runner.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    generate = commands.add_parser('render')
    generate.add_argument('--config', required=True)
    generate.add_argument('--output', required=True)
    activate = commands.add_parser('handoff')
    activate.add_argument('--snapshot', required=True)
    activate.add_argument('--active', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.operation == 'render':
            print('Generated workflow: ' + str(render(args.config, args.output)))
        else:
            handoff(args.snapshot, args.active)
    except workflow.WorkflowError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, subprocess.SubprocessError):
        print('Lab preparation failed; check private declarations and run logs', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
