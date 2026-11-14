"""Persist a bootstrap PAT with SOPS/age; retain the action name for callers."""
import json
import os
from pathlib import Path
import tempfile
import subprocess

import yaml
from ansible.plugins.action import ActionBase
from ansible.errors import AnsibleError


class ActionModule(ActionBase):
    TRANSFERS_FILES = False
    _VALID_ARGS = frozenset(('path', 'management_url', 'token', 'age_key_file', 'recovery_recipients'))

    def sops_token(self, path, endpoint, token, identity, recovery_recipients):
        identity = Path(identity).expanduser()
        if not identity.is_absolute() or not identity.is_file():
            raise AnsibleError('Supply the SOPS age identity file.')
        if (not isinstance(recovery_recipients, list) or not recovery_recipients
                or any(not isinstance(value, str) or not value.strip() for value in recovery_recipients)):
            raise AnsibleError('Supply the offline recovery recipients.')
        env = {**os.environ, 'SOPS_AGE_KEY_FILE': str(identity), 'SOPS_AGE_KEY': ''}
        if path.exists():
            encrypted = path.read_text()
            metadata = yaml.safe_load(encrypted)['sops']
            recipients = {entry['recipient'] for entry in metadata['age']}
            if not set(recovery_recipients) <= recipients:
                raise AnsibleError('Saved credential is missing a recovery recipient.')
            saved = json.loads(subprocess.run(
                ['sops', '--decrypt', '--input-type', 'yaml', '--output-type', 'json', '/dev/stdin'],
                input=encrypted, env=env, capture_output=True, text=True, check=True).stdout)
            existing = saved.get('netbird_api_token')
            if saved.get('netbird_management_url') != endpoint or not isinstance(existing, str) or not existing or any(c.isspace() for c in existing):
                raise AnsibleError('Saved SOPS credential does not match this endpoint.')
            if token is not None and token != existing:
                raise AnsibleError('Refusing to replace an existing SOPS token.')
            return dict(changed=False, exists=True, token=existing)
        if token is not None and (not isinstance(token, str) or not token or any(c.isspace() for c in token)):
            raise AnsibleError('Invalid bootstrap token.')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        recipient = subprocess.run(['age-keygen', '-y', str(identity)], capture_output=True, text=True, check=True).stdout.strip()
        if recipient in recovery_recipients:
            raise AnsibleError('Deployment and offline recovery identities must be separate.')
        recipients = ','.join(dict.fromkeys([recipient, *recovery_recipients]))
        # Exercise encryption and storage before requesting a one-time PAT.
        encrypted = subprocess.run(
            ['sops', '--encrypt', '--config', '/dev/null', '--age', recipients, '--input-type', 'json', '--output-type', 'yaml', '/dev/stdin'],
            input=json.dumps({'netbird_management_url': endpoint, 'netbird_api_token': token}),
            cwd=path.parent, env=env, capture_output=True, text=True, check=True).stdout
        fd, temporary = tempfile.mkstemp(prefix='.netbird-sops-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            if token is None:
                return dict(changed=False, exists=False)
            # Never overwrite a token saved by another run.
            os.link(temporary, path)
            return dict(changed=True, exists=True, token=token)
        finally:
            os.unlink(temporary)

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        result.update(changed=False, _ansible_no_log=True)
        try:
            if self._task.check_mode:
                raise AnsibleError('Token persistence requires a real run.')
            args = self._task.args
            path = Path(args['path']).expanduser()
            if not path.is_absolute() or path.is_symlink():
                raise AnsibleError('Use an absolute, non-symlink token path.')
            endpoint = args['management_url'].rstrip('/')
            result.update(self.sops_token(path, endpoint, args.get('token'), args.get('age_key_file', ''),
                                          args.get('recovery_recipients')))
            return result
        except Exception:
            # Never echo an exception that could include decrypted data.
            result.update(failed=True, msg=(
                'Cannot read or persist the SOPS controller token. Check the age identity, recovery recipients, '
                'destination permissions, and endpoint. For an initialized instance, restore '
                'the encrypted file or save a replacement owner PAT with SOPS/age. '
                'Never rerun owner creation to recover a token.'))
            return result
