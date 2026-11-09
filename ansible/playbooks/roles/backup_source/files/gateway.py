#!/usr/bin/python3
"""The transport key has exactly one command and no arguments or shell access."""
import os
import sys


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND') != 'export-v1' or len(sys.argv) != 1:
        raise PermissionError('Only export-v1 is permitted')
    os.execv('/usr/bin/sudo', ['sudo', '-n', '/usr/local/libexec/hou1-backup-export'])


if __name__ == '__main__':
    try: main()
    except Exception:
        print('Backup command denied.', file=sys.stderr)
        sys.exit(1)
