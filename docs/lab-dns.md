# Isolated lab DNS

`lab_dns` owns bootstrap name resolution independently of NetBird. It is disabled
by default (`lab_dns_state: unmanaged`) and rejects production inventory, production
VPN addresses and records outside the declared test namespace. It does not change
Technitium, public DNS or controller DNS.

## Inputs and entry points

Copy [the example](../examples/lab-dns.yml) into a private host input file. Declare
the namespace, this VM's LAN listener, exact private records and independent DNS
upstreams. HOU1 also needs the names of the Docker networks used by its selected
Compose projects, including external and internal networks. Never capture subnets;
the firewall owner discovers their current allocations. HOU4 uses host networking
and does not configure Docker DNS or bridge allowances.

Provision a running `systemd-resolved` with `/etc/resolv.conf` linked to its stub
or uplink resolver file. The role checks and preserves that link and other provider
drop-ins. It installs `dnsmasq-base`, `dnsutils` and UFW, verifies private UDP/TCP
answers and public recursion, then applies its resolver drop-in. The old lab hosts
block and its single-setting cloud-init override are removed after DNS verification.

Before fresh lab bootstrap, run from each host's repository:

```bash
ansible-playbook -i /private/lab/inventory.yml ansible/playbooks/lab-dns.yml \
  -e target_hosts=lab_host -e @/private/lab/host-inputs.yml -e @/private/lab/dns.yml
```

Use the same pinned SSH and isolated controller transport as the
[managed workflow](deployment-workflow.md). The standalone HOU1 entry point also
installs/configures Docker. Include the DNS input file in the workflow's `inputs`
list; normal full reconciliation maintains the same resources.

## Ownership and startup

| Owner | Files and behavior |
| --- | --- |
| `lab_dns` | `/etc/hou-lab-dns.conf`, `hou-lab-dns.service`, `90-hou-lab-dns.conf`; no direct edits to `resolv.conf` |
| HOU1 `docker` | Only the optional `dns` field in `daemon.json`; prior independent DNS and restart progress in `/var/lib/hou1-docker/dns.json` |
| HOU1 `lab_dns_containers` | Private `/etc/hou-lab-dns-firewall.json`, installed reconciler and UFW rules marked `hou-lab-dns managed` |

Compose remains the network/container owner. With an installed lab policy,
application/NetBird activation and Authentik preparation first run
`compose create --no-recreate`, reconcile DNS allowances, then activate services.
The firewall matches each declared external bridge interface, current IPv4 subnet,
the resolver's exact LAN address and TCP/UDP port 53. Internal-only networks receive
no allowance. Replacement and removal retire old rules; unrelated rules remain.
The old private helper's rules marked `hou-lab container DNS` are migrated.

Docker restarts only when its effective DNS setting changes or a prior restart
is pending. Adopting an identical setting preserves running containers. Keep
independent console recovery available when intentionally changing Docker DNS.

## Removal

Set `lab_dns_state: absent` explicitly and run `lab-dns.yml`. HOU1 restores the prior
independent Docker DNS setting (or removes the adopted lab setting), completes its
restart and removes owned firewall rules before stopping the resolver. Host DNS
returns to the remaining provider configuration; a failed public-resolution check
restores the lab drop-in and leaves the resolver running. Packages, unrelated host
entries, provider files and firewall rules remain installed.
