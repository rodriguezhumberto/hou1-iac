#!/usr/bin/python3
"""Only two fixed stdin receivers; no shell, arguments, forwarding or file access."""
import os
import sys


def main():
    commands={'deploy-v1':[], 'status-v1':['--metrics']}
    selected=commands.get(os.environ.get('SSH_ORIGINAL_COMMAND'))
    if selected is None or len(sys.argv)!=1:raise PermissionError('TLS command denied')
    os.execv('/usr/bin/sudo',['sudo','-n','/usr/local/libexec/hou1-tls-receive',*selected])


if __name__=='__main__':
    try:main()
    except Exception:
        print('TLS command denied.',file=sys.stderr)
        raise SystemExit(1)
