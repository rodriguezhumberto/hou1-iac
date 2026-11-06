# Authentik host capacity and isolation gate

Authentik stays on the existing HOU1 node only after the preflight passes. Run
the gate over the private NetBird administration path before deploying either
the test or production Compose project:

```bash
cd ansible
ansible-playbook playbooks/authentik-preflight.yml \
  -e target_hosts=hou1_prod --ask-vault-pass
```

The gate refuses placement below 2 vCPU, 4096 MiB RAM, 20 GiB free space, or
200,000 free inodes on `/var/lib`. It requires `iostat` and records a two
sample device-utilization probe so I/O headroom is measured before deployment.
Install `sysstat` and rerun the gate when the probe is unavailable.

The firewall defaults to deny incoming and allows only TCP 80/443 publicly and
TCP 22 from the NetBird subnet on `wt0`. PostgreSQL TCP 5432 is explicitly
denied on host interfaces. Authentik test and production use the distinct
`hou1-authentik-test` and `hou1-authentik-prod` Docker networks; neither may
share `hou1-netbird-ingress`. The preflight inspects running Docker port
bindings and refuses a `0.0.0.0:5432` or `:::5432` publication.

The later Compose deployment must bind PostgreSQL only to its private service
network and use separate roots under `/var/lib/hou1-authentik/{test,prod}`.
The same capacity and network gate is required again after a host resize or a
change to either Compose project. Monitoring thresholds are 80% memory, 75%
filesystem, 70% inode, and 70% device utilization; crossing a threshold blocks
the next deployment until capacity is restored.
