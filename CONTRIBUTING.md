# Contributing to HOU1 Infrastructure

## Contributor guidelines

- **Use existing patterns and public entry points:** Extend existing roles and playbooks (`ansible/playbooks/hou1.yml`, `ansible/playbooks/monitoring.yml`, `ansible/playbooks/backups.yml`) rather than introducing ad-hoc deployment paths.
- **Single file ownership:** Each generated configuration or runtime file must be managed by one task phase only.
- **Preserve safety and secrets:** Retain environment preflight guards, ordering, and controller-side SOPS/age encryption. Never commit plaintext secrets.
- **Record inputs:** Declare new variables in role defaults, environment inventory, scoped SOPS bundles, or release selectors.
- **Test changes:** Run relevant local checks and test changed behavior.
- **No live deployment in code changes:** Commits and branches do not deploy to live hosts.
- **Concise documentation:** Keep docs factual, plain, and brief.

## Environment setup

Use an isolated Python environment and install dependencies explicitly:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
ansible-galaxy collection install -r ansible/requirements.yml
```

## Validation commands

Use the Makefile targets for repository validation:

- `make help`: Display available targets and descriptions.
- `make check`: Run the local validation suite.
- `make test`: Run offline unit tests (`python3 -m unittest discover -s tests`).
- `make syntax`: Validate playbook syntax (`ansible-playbook --syntax-check`).
- `make lint`: Run the configured static checks.

### Optional integration checks

Some checks require Docker Compose, Nginx, SOPS/age or other external tools.
Read each test's skip or failure message; a skip does not establish that behavior.
Use `NGINX_BIN` and `COMPOSE_BIN` to select the local configuration validators.
CI also runs the branded-login browser check with its declared browser dependency.
Prometheus and Alertmanager binary checks belong to the companion HOU4 repository.

See [docs/development.md](docs/development.md) for architectural details and execution locations.
