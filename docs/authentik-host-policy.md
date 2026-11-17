# Authentik host capacity and isolation gate

Authentik stays on the existing HOU1 node only after the preflight passes. Run
the gate over the private NetBird administration path before deploying either
the test or production Compose project:

```bash
cd ansible
ansible-playbook playbooks/authentik-preflight.yml \
  -e target_hosts=hou1_prod
```

The gate refuses placement below 2 vCPU, 4096 MiB RAM, 20 GiB free space, or
200,000 free inodes on `/var/lib`. It requires `iostat` and records two samples
of device utilization. Install `sysstat` and rerun when the probe is unavailable.
The gate applies UFW changes; it is not read-only.

The firewall defaults to deny incoming and allows only TCP 80/443 publicly and
TCP 22 from the NetBird subnet on `wt0`. PostgreSQL TCP 5432 is explicitly
denied on host interfaces. Authentik test and production use the distinct
`hou1-authentik-test` and `hou1-authentik-prod` Docker networks; neither may
share `hou1-netbird-ingress`. The preflight inspects running Docker port
bindings and refuses a `0.0.0.0:5432` or `:::5432` publication.

Compose keeps PostgreSQL on its private network and uses separate data roots.
Rerun the gate after a host resize or Compose change. Operators must also review
measured pressure against the [production thresholds](production-oidc-runbook.md#health-monitoring-and-renewal).
The preflight checks fixed capacity minima and probe availability; it does not
automatically enforce utilization thresholds.
