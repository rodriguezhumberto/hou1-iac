# Development and architecture map

## Execution locations and supported operations

Operations span three execution environments:

### Controller
- Orchestrates Ansible playbooks (`ansible/playbooks/hou1.yml`, `ansible/playbooks/monitoring.yml`, `ansible/playbooks/backups.yml`).
- Decrypts SOPS/age credentials at task time via `community.sops.load_vars`.
- Verifies release selectors, image digests, and git revisions before deployment.
- Coordinates TLS issuance and renewal transports (`scripts/controller-tls.py`, `scripts/controller-renewal-peer.py`).
- Executes offline unit tests, lints, and recovery checks (`scripts/verify-recovery.py`).

### HOU1 host
- Runs system services, UFW firewall rules, and the NetBird VPN client.
- Runs Docker Compose containers: application image, Nginx ingress proxy, Authentik server/worker, PostgreSQL, and Redis.
- Exposes private metrics to HOU4 via the `monitoring_exporter` role over VPN.
- Creates staged encrypted backup snapshots via the `backup_source` role.

### HOU4 companion
- Operates the monitoring stack (Prometheus, Alertmanager, Grafana SSO).
- Scrapes HOU1 metrics privately across the NetBird VPN mesh.
- Collects nightly encrypted backup archives from HOU1 via restricted SSH ([backup protocol](hou4-backup-protocol.md)).
- Executes synthetic health and login probes (`scripts/staff-login-probe.py`).

## Configuration ownership

Configuration flows strictly from authoritative inputs to downstream outputs:

1. **Role defaults** (`ansible/playbooks/roles/*/defaults/main.yml`): Baseline variables, fallback directory paths, and default ports.
2. **Environment inventory** (`ansible/inventory/inventory.yml`, `ansible/inventory/group_vars/`): Topology definitions, host groups, IP addresses, and SSH parameters.
3. **Scoped SOPS source bundles** (`ansible/inventory/group_vars/*.sops.yml`, `ansible/secrets/*.enc.*`): Encrypted credentials and identity tokens scoped to environment keys in `.sops.yaml`.
4. **Private release selectors** (e.g. `release-inputs.yml`): Run-time parameters passed via `-e "@$INPUTS"` defining image digests and Git refs (`deploy_ref`).
5. **Generated runtime files**: Target files rendered by Ansible templates (Docker Compose files, Nginx configurations, container `.env` files).

> Generated `.env` files are operational outputs rendered from authoritative inputs; they must not be manually edited or synchronized across hosts.

## Development workflow and tooling

### Dependencies
Install Python prerequisites and Ansible collections:
```bash
pip install -r requirements-dev.txt
ansible-galaxy collection install -r ansible/requirements.yml
```

### Makefile targets
- `make help`: Display available targets and descriptions.
- `make check`: Run repository preflight checks.
- `make test`: Run offline unit tests (`python3 -m unittest discover -s tests`).
- `make syntax`: Validate playbook syntax (`ansible-playbook --syntax-check`).
- `make lint`: Run YAML and code linters.

### Optional integration checks
Offline unit tests automatically skip assertions requiring external binaries. To enable these optional checks locally, install the respective tools and set their environment variables:
- `PROMTOOL_BIN`: Path to `promtool` for alert rules.
- `AMTOOL_BIN`: Path to `amtool` for Alertmanager configuration checks.
- `NGINX_BIN`: Path to `nginx` for configuration syntax validation.
- `ALERTMANAGER_BIN`: Path to `prometheus-alertmanager`.
- `BLACKBOX_BIN`: Path to `prometheus-blackbox-exporter`.
