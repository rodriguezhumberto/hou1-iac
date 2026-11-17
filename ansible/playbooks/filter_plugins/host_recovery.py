"""Pin optional recovery handoffs without creating peers, groups or policies."""
import importlib.util
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

from ansible.errors import AnsibleFilterError


def renewal_plan(groups, peers, policies, config, environment, server_ip, lost_id, source_id, allow_unbound=False):
    # Reuse the provisioning contract so recovery cannot introduce broader grants.
    spec = importlib.util.spec_from_file_location(
        'recovery_renewal_contract', Path(__file__).resolve().parents[3] / 'scripts/controller-renewal-peer.py')
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    try:
        if config['environment'] != environment or server_ip not in config['targets']:
            raise ValueError('Renewal receivers must include this recovery environment and server')
        addresses = config['targets']
        network = IPv4Network(config['subnet'])
        if (not isinstance(addresses, list) or len(set(addresses)) != len(addresses)
                or not network.subnet_of(IPv4Network('100.64.0.0/10'))
                or any(IPv4Address(address) not in network
                       or IPv4Address(address) in (network.network_address, network.broadcast_address)
                       for address in addresses)):
            raise ValueError('Renewal targets must be unique addresses in the recovery overlay')
        targets = contract.target_peers(config, peers)
        if lost_id not in targets:
            raise ValueError('Renewal receivers do not contain the pinned lost server')
        selected = []
        for name, members in [('tls-renewal-controller-' + environment, {source_id}),
                              ('tls-renewal-receivers-' + environment, targets)]:
            matches = [group for group in groups if group['name'] == name]
            allowed = [members]
            if allow_unbound and name.startswith('tls-renewal-receivers-'):
                allowed.append(members - {lost_id})
            if (len(matches) != 1 or contract.ids(matches[0].get('peers') or []) not in allowed
                    or matches[0].get('resources')):
                raise ValueError('Renewal handoff requires exact existing group membership')
            selected.append(matches[0])
        source, destination = selected
        expected = contract.policy_body(config, source['id'], destination['id'])
        owned = [policy for policy in policies if policy['name'] == expected['name']]
        if len(owned) != 1:
            raise ValueError('Renewal handoff requires one existing policy')
        contract.require_owned_policy(owned[0], expected)
        source_peers = [peer for peer in peers if peer['id'] == source_id]
        if len(source_peers) != 1:
            raise ValueError('Renewal source peer must be unique')
        contract.require_persistent_peer(source_peers[0])
        contract.require_isolated_source(source_peers[0], groups, policies, expected)
        return dict(id=destination['id'], name=destination['name'],
                    peers=sorted(targets - {lost_id}), policy=dict(id=owned[0]['id'], body=owned[0]),
                    source=dict(id=source['id'], name=source['name'], peers=[source_id]))
    except (KeyError, TypeError, ValueError) as error:
        raise AnsibleFilterError(str(error)) from error


def companion_endpoint(config, inventory, current_host, companion_ip):
    try:
        alias = config['inventory_host']
        address = IPv4Address(config['public_ip'])
        known_hosts = Path(config['known_hosts_file'])
        if (alias in (current_host, 'localhost', '127.0.0.1') or alias not in inventory
                or not address.is_global or str(address) != config['public_ip']
                or str(address) == companion_ip or not known_hosts.is_absolute()
                or '..' in known_hosts.parts or any(c.isspace() for c in str(known_hosts))
                or inventory[alias].get('ansible_host') != str(address)):
            raise ValueError
        return True
    except (KeyError, TypeError, ValueError):
        raise AnsibleFilterError('Declare a separate HOU4 inventory alias, public IPv4 and pinned known-hosts file') from None


class FilterModule:
    def filters(self):
        return {'host_recovery_renewal_plan': renewal_plan,
                'host_recovery_companion_endpoint': companion_endpoint}
