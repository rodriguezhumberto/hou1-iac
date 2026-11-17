#!/usr/bin/python3
"""Own only Docker's optional lab DNS setting and retry interrupted restarts."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CONFIG = Path('/etc/docker/daemon.json')
RECEIPT = Path('/var/lib/hou1-docker/dns.json')


def write_json(path, value, mode=0o600):
    if path.exists() and json.loads(path.read_text()) == value:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            os.fchmod(output.fileno(), mode)
            json.dump(value, output, indent=2, sort_keys=True)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update(state, servers, config_path=CONFIG, receipt_path=RECEIPT, validate=True):
    if state not in ('present', 'absent') or not isinstance(servers, list) or not servers:
        raise ValueError('Invalid lab Docker DNS inputs')
    for address in servers:
        parsed = ipaddress.IPv4Address(address)
        if not any(parsed in ipaddress.ip_network(cidr) for cidr in
                   ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
            raise ValueError('Lab Docker DNS must use the declared LAN resolver')
    current = json.loads(config_path.read_text()) if config_path.exists() else {}
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if state == 'absent' and receipt is None:
        return {'changed': False, 'restart_pending': False}
    observed = {'present': 'dns' in current, 'value': current.get('dns')}
    if receipt is None:
        # Adopt the private helper's identical setting without restoring it when
        # lab DNS is removed. Preserve an independent operator DNS setting.
        previous = observed if current.get('dns') != servers else {'present': False, 'value': None}
    else:
        previous = receipt['previous']
        if observed not in (receipt['managed'], receipt['pending_from'], previous):
            raise ValueError('Docker DNS changed outside its owner; preserve it before retrying')
    desired = dict(current)
    if state == 'present':
        desired['dns'] = servers
    elif previous['present']:
        desired['dns'] = previous['value']
    else:
        desired.pop('dns', None)
    changed = current != desired
    if changed and validate:
        descriptor, temporary = tempfile.mkstemp(dir=config_path.parent)
        try:
            with os.fdopen(descriptor, 'w') as output:
                json.dump(desired, output)
            subprocess.run(['dockerd', '--validate', '--config-file', temporary], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        finally:
            Path(temporary).unlink(missing_ok=True)
    pending = changed or bool(receipt and receipt['restart_pending'])
    managed = {'present': 'dns' in desired, 'value': desired.get('dns')}
    write_json(receipt_path, {'version': 1, 'state': state, 'previous': previous,
               'managed': managed, 'pending_from': observed, 'restart_pending': pending})
    if changed:
        write_json(config_path, desired, mode=0o644)
    return {'changed': changed, 'restart_pending': pending}


def acknowledge(receipt_path=RECEIPT):
    if not receipt_path.exists():
        return
    receipt = json.loads(receipt_path.read_text())
    if receipt['state'] == 'absent':
        receipt_path.unlink()
    else:
        receipt.update(restart_pending=False, pending_from=receipt['managed'])
        write_json(receipt_path, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ack', action='store_true')
    args = parser.parse_args()
    if args.ack:
        acknowledge()
    else:
        inputs = json.load(sys.stdin)
        print(json.dumps(update(inputs['state'], inputs['servers'])))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Docker DNS reconciliation failed; private diagnostics withheld') from None
