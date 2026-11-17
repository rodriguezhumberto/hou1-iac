# Contributing to HOU1 Infrastructure

## Contributor guidelines

- **Use existing patterns and public entry points:** Extend existing roles and playbooks (`ansible/playbooks/hou1.yml`, `ansible/playbooks/monitoring.yml`, `ansible/playbooks/backups.yml`) rather than introducing ad-hoc deployment paths.
- **Single file ownership:** Each generated configuration or runtime file must be managed by one task phase only.
- **Preserve safety and secrets:** Retain environment preflight guards, ordering, and controller-side SOPS/age encryption. Never commit plaintext secrets.
- **Record inputs:** Declare new variables in role defaults, environment inventory, scoped SOPS bundles, or release selectors.
- **Test changes:** Run or add offline unit tests covering changed behaviors before committing.
- **No live deployment in code changes:** Commits and branches do not deploy to live hosts.
- **Concise documentation:** Keep docs factual, plain, and brief.

## Environment setup

Install required Python dependencies and Ansible collections:

```bash
pip install -r requirements-dev.txt
ansible-galaxy collection install -r ansible/requirements.yml
```

## Validation commands

Use the Makefile targets for repository validation:

- `make help`: Display available targets and descriptions.
- `make check`: Run repository preflight checks.
- `make test`: Run offline unit tests (`python3 -m unittest discover -s tests`).
- `make syntax`: Validate playbook syntax (`ansible-playbook --syntax-check`).
- `make lint`: Run code and YAML linters.

### Optional integration checks

Offline unit tests automatically skip assertions requiring external binaries. To enable these optional checks locally, install the respective tools and set their environment variables:

- `PROMTOOL_BIN`: Path to `promtool` for Prometheus alert rule testing.
- `AMTOOL_BIN`: Path to `amtool` for Alertmanager config checks.
- `NGINX_BIN`: Path to `nginx` for configuration syntax validation.
- `ALERTMANAGER_BIN`: Path to `prometheus-alertmanager` runtime checks.
- `BLACKBOX_BIN`: Path to `prometheus-blackbox-exporter` checks.

See [docs/development.md](docs/development.md) for architectural details and execution locations.
