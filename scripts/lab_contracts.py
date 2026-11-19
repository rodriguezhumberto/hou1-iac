"""Controller-only checks for a selected lab release and its deployment inputs."""

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tempfile

from ssh_target import private_file
from lab_runtime import runtime_contract

SHA = r'[a-f0-9]{40}'
GHCR_IMAGE = (r'ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*'
              r'(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[a-f0-9]{64}')
ROLES = Path(__file__).resolve().parents[1] / 'ansible/playbooks/roles'
CAPABILITY_VARIABLES = {
    'hou1': ('authentik_secret_source', 'authentik_identity_secret_source',
             'authentik_test_seed_source',
             'authentik_controller_age_key_file', 'rehearsal_secret_source',
             'rehearsal_host_age_key_source', 'application_image_registry_secret_source',
             'application_image_registry_age_key_file', 'hou1_tls_bundle_source',
             'hou1_tls_age_key_file', 'netbird_token_vault_file', 'netbird_token_age_key_file'),
    'hou4': ('netbird_token_vault_file', 'netbird_token_age_key_file'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_release(release):
    require(set(release) == {'receipt', 'source_bundle', 'bundle_ref', 'authorization_file'},
            'Declare the release receipt, standalone source bundle, ref and authorization file')
    for name in ('receipt', 'source_bundle'):
        private_file(release[name])
    require(re.fullmatch(r'refs/heads/[A-Za-z0-9_/-]+', release['bundle_ref'])
            and '..' not in release['bundle_ref'], 'Select an explicit bundle branch ref')
    path = Path(release['authorization_file'])
    require(not path.is_absolute() and '..' not in path.parts
            and re.fullmatch(r'[A-Za-z0-9_./-]+\.js', str(path)),
            'Select the authorization module within the release source')
    receipt = json.loads(private_file(release['receipt']).read_text())
    require(set(receipt) == {'schema', 'image', 'revision', 'platform', 'image_id'}
            and type(receipt['schema']) is int and receipt['schema'] == 1
            and receipt['platform'] == 'linux/amd64'
            and re.fullmatch(GHCR_IMAGE, receipt['image'])
            and re.fullmatch(SHA, receipt['revision'])
            and re.fullmatch(r'sha256:[a-f0-9]{64}', receipt['image_id']),
            'Select a complete immutable linux/amd64 image release receipt')
    return {key: receipt[key] for key in ('image', 'revision')}


def fingerprints(paths):
    return {str(private_file(p)): hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in sorted(set(paths))}


def capability_paths(config, selections):
    paths = [config['age_key_file'], *(config['release'][name] for name in ('receipt', 'source_bundle'))]
    for service, selected in selections.items():
        paths.extend(config['hosts'][service]['capabilities'])
        paths.extend(value for value in selected['contracts']['files'].values() if value)
    return paths


def run(argv, *, env=None, data=None, failure=None):
    # Keep command failures and decrypted output out of public diagnostics.
    result = subprocess.run(argv, env=env, input=data, capture_output=True, timeout=120)
    require(result.returncode == 0, failure or ('Local ' + Path(argv[0]).name + ' contract check failed'))
    return result.stdout


def source_roles(config):
    release = config['release']
    require(load_release(release) == config['application'], 'The release receipt changed after selection')
    with tempfile.TemporaryDirectory(prefix='hou-lab-release-') as directory:
        repo = str(Path(directory) / 'source.git')
        run(['git', '-c', 'init.templateDir=', 'init', '--bare', repo])
        run(['git', '-C', repo, '-c', 'transfer.fsckObjects=true', 'fetch', '--no-tags',
             '--', release['source_bundle'], release['bundle_ref']])
        revision = run(['git', '-C', repo, 'rev-parse', 'FETCH_HEAD^{commit}']).decode().strip()
        require(revision == config['application']['revision'], 'The standalone bundle ref differs from the image release')
        source = run(['git', '-C', repo, 'show', revision + ':' + release['authorization_file']]).decode()
        entry = run(['git', '-C', repo, 'ls-tree', revision, '--', 'runtime-contract.json']).decode().strip()
        if entry:
            require(entry.split()[:2] == ['100644', 'blob'],
                    'The application runtime contract must be a regular source file')
            runtime_contract(run(['git', '-C', repo, 'show', revision + ':runtime-contract.json']))
    # The current application publishes a static mapping, not a JSON contract.
    # Accept that exact literal form without importing or executing release code.
    matches = re.findall(r'\bconst\s+GROUP_ROLES\s*=\s*Object\.freeze\(\s*(\{[^{}]*\})\s*\)\s*;', source)
    require(len(matches) == 1, 'The selected authorization module must declare one static GROUP_ROLES mapping')
    try:
        tree = ast.parse(matches[0], mode='eval').body
        require(isinstance(tree, ast.Dict), 'The application role mapping must be a literal object')
        mapping = ast.literal_eval(tree)
    except (SyntaxError, ValueError):
        raise ValueError('The application role mapping must use supported string literals') from None
    require(isinstance(mapping, dict) and len(tree.keys) == len(mapping) == 3
            and all(isinstance(key, str) and key.strip() for key in mapping)
            and set(mapping.values()) == {'reader', 'editor', 'admin'},
            'The application must declare three distinct staff role groups')
    return {role: group for group, role in mapping.items()}


def validate_identity(config, selected, roles, env):
    files = selected['files']
    key = files['authentik_controller_age_key_file'] or config['age_key_file']
    names = json.loads(run(['sops', '--decrypt', '--output-type', 'json', files['authentik_identity_secret_source']],
        env={**env, 'SOPS_AGE_KEY_FILE': key}))['IDENTITY_NAMES']
    bootstrap = runpy.run_path(str(ROLES / 'authentik/files/bootstrap.py'))
    profile = bootstrap['resolve_profile'](selected['identity_profile'], names)
    require(profile['environment'] == 'test' and profile['netbird_subnet'] == '100.91.0.0/16'
            and profile['netbird_url'] == config['management_url']
            and profile['issuer_host'] == 'auth-test.' + config['namespace'],
            'The identity profile must select the lab issuer and VPN account')
    require(profile['role_groups'] == roles, 'Identity role groups differ from the selected application release')
    client = profile['staff']['client_id']
    require(selected['staff_client_id'] == client
            and selected['staff_issuer'] == 'https://' + profile['issuer_host'] + '/application/o/' + client + '/'
            and selected['netbird_client_id'] == profile['netbird']['client_id'],
            'Application and VPN OIDC inputs differ from the private identity profile')
    users = selected['staff_users']
    require(isinstance(users, list) and all(isinstance(user, dict) for user in users),
            'Declare staff users as explicit records')
    usernames = [user.get('username') for user in users]
    require(all(isinstance(name, str) and name.strip() for name in usernames)
            and len(set(usernames)) == len(usernames), 'Staff usernames must be nonempty and unique')
    for user in users:
        groups = user.get('groups', [])
        require(isinstance(groups, list) and all(isinstance(group, str) for group in groups)
                and set(groups) <= set(roles.values()), 'Staff users reference an undeclared role group')


def validate_backups(config, selections, env):
    one, two = (selections[service]['contracts'] for service in ('hou1', 'hou4'))
    require(type(one['backup_enabled']) is bool and type(two['backup_enabled']) is bool
            and one['backup_enabled'] == two['backup_enabled'],
            'Enable or disable the HOU1 backup source and HOU4 receiver together')
    proof = os.urandom(32)
    if one['backup_enabled']:
        require(one['backup_receiver_ip'] == config['hosts']['hou4']['ssh_host_ip']
                and two['backup_source_ip'] == config['hosts']['hou1']['ssh_host_ip']
                and re.fullmatch(r'age1[0-9a-z]{58}', one['backup_recipient']),
                'The backup source, receiver and offline recipient must select this lab')
        public_key = run(['ssh-keygen', '-lf', '/dev/stdin'],
                         data=(two['backup_source_host_key'] + '\n').encode()).decode().split()
        require(public_key[1] == config['hosts']['hou1']['ssh_fingerprint'],
                'The backup receiver pins a different source host key')
        run(['age', '--encrypt', '--recipient', one['backup_recipient']], env=env, data=proof)
    else:
        identity = one['files']['rehearsal_host_age_key_source']
        require(bool(identity), 'Declare the controller copy of the Authentik backup self-check identity')
        encrypted = run(['age', '--encrypt', '--recipient', one['authentik_backup_recipient']], env=env, data=proof)
        require(run(['age', '--decrypt', '--identity', identity], env=env, data=encrypted,
                    failure='The Authentik backup recipient cannot be decrypted by its self-check identity') == proof,
                'The Authentik backup recipient cannot be decrypted by its self-check identity')


def validate(config, selections, env):
    artifact = runpy.run_path(str(ROLES / 'application_image/files/artifact.py'))
    artifact['validate_reference'](config['application']['image'], config['application']['revision'])
    roles = source_roles(config)
    one = selections['hou1']['contracts']
    require(one['source_bundle'] == config['release']['source_bundle']
            and one['bundle_ref'] == config['release']['bundle_ref']
            and one['source_revision'] == config['application']['revision'],
            'Effective source-bundle inputs differ from the selected release')
    validate_identity(config, one, roles, env)
    validate_backups(config, selections, env)
