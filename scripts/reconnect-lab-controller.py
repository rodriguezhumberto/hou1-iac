#!/usr/bin/env python3
"""Validate or reconnect the dedicated lab controller without replacing its container."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SPEC = importlib.util.spec_from_file_location('lab_network', Path(__file__).with_name('lab-network.py'))
network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network)


def reconnect(config_path, *, apply=False):
    network.require(sys.platform == 'linux' and os.getuid() > 0,
                    'Run from the non-root Linux controller account')
    config = network.load_config(config_path, os.getuid())
    network.require(config.get('transport', 'vpn') == 'vpn', 'Reconnect requires the dedicated VPN transport')
    deadline = time.monotonic() + 90

    def remaining(limit):
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('Controller validation/reconnect exceeded ninety seconds')
        return min(limit, seconds)

    peer = network.inspect_container(config, timeout=remaining(15))
    connected = network.peer_connected(config, timeout=remaining(20))
    if not apply:
        return {'environment': 'test', 'validated': True, 'connected': connected, 'applied': False}

    def unchanged():
        network.require(network.load_config(config_path, os.getuid()) == config
                        and network.inspect_container(config, timeout=remaining(15)) == peer,
                        'The declared controller transport changed during reconnect')

    # Keep the container/PID and stored enrollment: active namespace users and
    # recovered peer grants must remain bound to the same dedicated controller.
    for action in ('down', 'up'):
        unchanged()
        subprocess.run(['docker', 'exec', peer['id'], 'netbird', action],
                       capture_output=True, check=True, timeout=remaining(30))
    while True:
        unchanged()
        if network.peer_connected(config, timeout=remaining(20)):
            return {'environment': 'test', 'validated': True, 'connected': True,
                    'applied': True, 'container_preserved': True}
        time.sleep(remaining(1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Private lab-network VPN configuration')
    parser.add_argument('--apply', action='store_true', help='Reconnect only the verified dedicated test controller')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(reconnect(args.config, apply=args.apply)))
        return 0
    except network.TransportError as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Controller reconnect failed; inspect the selected private transport and saved enrollment.',
              file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
