# Working in this repository

- Follow [CONTRIBUTING.md](CONTRIBUTING.md) and use existing public entry points.
- Preserve environment guards, task ordering, rollback protections and secret handling.
- Give each new generated file one owner; document configuration inputs and sources.
- Keep application names, actual deployment domains and application-specific repository names in private inputs; use neutral placeholders in new tracked code, examples and documentation.
- Preserve public variables, installed paths and recovery formats during refactoring.
- Run relevant local checks. Add tests for changed behavior, including removals.
- Code changes do not authorize live deployment.
- Keep docs concise and record consequential design choices with their reasons.
- Do not add approval requirements or delegation rules without a task requirement.
