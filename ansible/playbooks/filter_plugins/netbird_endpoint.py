"""Compare management endpoints using NetBird's default-port semantics."""
from urllib.parse import urlsplit

from ansible.errors import AnsibleFilterError


def netbird_endpoint(value):
    try:
        url = urlsplit(value)
        default_port = {'https': 443, 'http': 80}[url.scheme]
        port = url.port if url.port is not None else default_port
        if (not url.hostname or url.username is not None or url.password is not None or
                url.path not in ('', '/') or url.query or url.fragment or not 1 <= port <= 65535):
            raise ValueError
        return url.scheme, url.hostname.lower(), port
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise AnsibleFilterError('Invalid NetBird management endpoint URL.') from error


class FilterModule:
    def filters(self):
        return {'netbird_endpoint': netbird_endpoint}
