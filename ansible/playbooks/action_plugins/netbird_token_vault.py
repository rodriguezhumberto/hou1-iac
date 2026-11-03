"""Persist a bootstrap PAT on the controller using this run's Vault credential."""
import json
import os
from pathlib import Path
import tempfile

import yaml
from ansible.plugins.action import ActionBase
from ansible.errors import AnsibleError


class ActionModule(ActionBase):
    TRANSFERS_FILES = False
    _VALID_ARGS = frozenset(('path', 'management_url', 'token', 'vault_id'))

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        result.update(changed=False, _ansible_no_log=True)
        temporary = None
        try:
            if self._task.check_mode:
                raise AnsibleError('Token persistence requires a real run.')
            args = self._task.args
            path = Path(args['path']).expanduser()
            if not path.is_absolute() or path.is_symlink():
                raise AnsibleError('Use an absolute, non-symlink token Vault path.')
            endpoint = args['management_url'].rstrip('/')
            vault = self._loader._vault
            vault_id = args.get('vault_id', 'default')
            secret = next((value for label, value in vault.secrets or [] if label == vault_id), None)
            if secret is None:
                raise AnsibleError('Supply --ask-vault-pass or the configured --vault-id before bootstrap.')

            token = args.get('token')
            if path.exists():
                encrypted = path.read_bytes()
                if not encrypted.startswith(b'$ANSIBLE_VAULT;'):
                    raise AnsibleError('Token file must be encrypted with Ansible Vault.')
                saved = yaml.safe_load(vault.decrypt(encrypted))
                if not isinstance(saved, dict) or saved.get('netbird_management_url') != endpoint:
                    raise AnsibleError('Token Vault belongs to another management endpoint.')
                existing = saved.get('netbird_api_token')
                if not isinstance(existing, str) or not existing or any(c.isspace() for c in existing):
                    raise AnsibleError('Token Vault has no valid netbird_api_token.')
                if token is not None and token != existing:
                    raise AnsibleError('Refusing to overwrite a different saved API token.')
                result.update(exists=True, token=existing)
                return result

            if token is not None and (not isinstance(token, str) or not token or any(c.isspace() for c in token)):
                raise AnsibleError('Bootstrap returned no valid personal_access_token.')
            # Exercise encryption and the destination before requesting a one-time token.
            payload = json.dumps({'netbird_management_url': endpoint, 'netbird_api_token': token})
            encrypted = vault.encrypt(payload, secret=secret, vault_id=vault_id)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.netbird-vault-', dir=path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            if token is None:
                result.update(exists=False)
            else:
                # Publish a complete encrypted file atomically, without replacing a
                # token written by another run. mkstemp gives the file mode 0600.
                os.link(temporary, path)
                result.update(changed=True, exists=True, token=token)
            return result
        except Exception:
            # Never echo an exception that could include decrypted data.
            result.update(failed=True, msg=(
                'Cannot read or persist the controller token Vault. Check the Vault credential, '
                'destination permissions, and endpoint. For an initialized instance, restore '
                'the encrypted file or create a replacement owner PAT and save it with '
                'ansible-vault edit. Never rerun owner creation to recover a token.'))
            return result
        finally:
            if temporary is not None:
                os.unlink(temporary)
