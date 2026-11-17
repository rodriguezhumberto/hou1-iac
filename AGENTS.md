# Agent guidelines

Guidelines for automated agents modifying this repository:

- **Follow existing patterns and public entry points:** Use established playbooks (`ansible/playbooks/hou1.yml`, `ansible/playbooks/monitoring.yml`, `ansible/playbooks/backups.yml`), role layouts, and naming conventions.
- **Single file ownership:** Each generated runtime file must be rendered and owned by exactly one role or task phase.
- **Preserve guards, ordering, and secrets:** Keep preflight validation guards, task execution order, rollback protections, and controller-side SOPS/age secret handling intact. Never log or leak secrets into command arguments.
- **Declare configuration inputs:** Document every new variable with its authoritative source (role default, environment inventory, scoped SOPS bundle, or private release selector).
- **Test relevant behavior:** Add or update offline tests in `tests/` covering modified logic without relying on live infrastructure.
- **No implicit live deployment:** Code and playbook changes stay local to branches and checkouts; changes never imply automatic deployment to live hosts.
- **Concise, factual documentation:** Document consequential design decisions and rationale without conversational fluff, administrative workflow rules, or stock qualifiers.
- **No invented workflows:** Do not introduce mandatory approval steps, delegation constraints, agent-model restrictions, or synthetic procedures.
