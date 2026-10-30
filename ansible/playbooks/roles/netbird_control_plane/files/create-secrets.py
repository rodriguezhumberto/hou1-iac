"""Create persistent NetBird encryption secrets once, without overwriting them."""
import base64
import json
import os
import secrets
import sys

path = sys.argv[1]
values = {name: base64.b64encode(secrets.token_bytes(32)).decode('ascii')
          for name in ('relay_auth_secret', 'store_encryption_key', 'cookie_encryption_key')}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(values, stream)
    stream.write('\n')
