# Contributing to HOU1 Infrastructure

Use the existing roles and public playbooks. Preserve environment guards, task
ordering, recovery formats and controller-side SOPS/age secret handling. Declare
new configuration inputs at their source and keep generated files owned by one
component. See [AGENTS.md](AGENTS.md) for repository rules.

## Environment setup

Use Python 3.12+ and install the pinned tools and Ansible collections explicitly:

```bash
make setup
```

Setup uses `.venv` and `.ansible/collections`. Override `VENV` and
`COLLECTIONS_PATH` to use other locations; checks use the same settings.

## Validation commands

Use the Makefile targets for repository validation:

- `make help`: Display available targets and descriptions.
- `make check`: Run the local validation suite.
- `make test`: Run local tests against fixtures and disposable containers.
- `make syntax`: Validate playbook syntax (`ansible-playbook --syntax-check`).
- `make lint`: Run the configured static checks.

### Optional integration checks

Check commands do not install dependencies or contact managed nodes. Tests may
use Docker and its image registry. Some checks require Nginx, SOPS/age or other
external tools.
Read each test's skip or failure message; a skip does not establish that behavior.
Use `NGINX_BIN` and `COMPOSE_BIN` to select the local configuration validators.
CI also runs the branded-login browser check with its declared browser dependency.
Prometheus and Alertmanager binary checks belong to the companion HOU4 repository.

See [docs/development.md](docs/development.md) for architectural details and execution locations.
