# Managed lab deployment workflow

`scripts/deploy-lab.py` coordinates the existing HOU1 and HOU4 playbooks. It
supports existing test hosts on `100.91.0.0/16`, with controller-managed HOU1 TLS.
It does not bootstrap hosts, enroll the controller, change DNS, seed identities,
deploy Authentik/control-plane changes or run destructive acceptance drills.
Those gaps are assigned in the [intervention audit](deployment-interventions.md).

## Inputs

Copy [the example](../examples/lab-workflow.json) outside Git and replace its
paths, revisions, fingerprints and runtime projects. Keep the JSON, inventory,
extra-variable files and SSH config operator-owned, mode `0600`; create the run
directory as mode `0700`. Prepare tools with `make setup` in both repositories.

- Pin both infrastructure commits and the application digest/revision. Execution
  rejects tracked checkout changes and untracked deployment code. Untracked notes
  such as `docs/mockups/` do not affect execution.
- Keep the actual namespace, image repository and runtime paths in private inputs;
  the example uses synthetic values. The workflow validates GHCR digest syntax
  and compares the complete image reference with the effective deployment inputs.
- Supply the existing private inventories and ordered extra-variable files.
  Inventories must select managed test hosts; default test groups remain empty.
  Keep `ansible_*` execution variables in inventory, not extra-variable files;
  global overrides also affect controller delegation.
- Use verified ED25519 fingerprints, one explicit SSH `IdentityFile` per host,
  and the private SSH contract in [targeting](probes/rehearsal-targets.md).
  Direct VPN access is required; SSH proxy commands are not supported.
- Run inside the selected administrative VPN namespace if the controller uses
  one. The workflow does not create routes, modify the controller resolver or
  select an existing production peer.
- List each runtime Compose project, including explicit project/file options
  where needed. HOU1 must include `astro-app`; verification checks its actual
  image, source revision and source cleanliness. Optional Compose profiles are
  outside this initial workflow.
- Declare HTTPS checks with exact statuses. They run on HOU4, use normal TLS
  validation, and refuse redirects. Include both readiness and expected-denial
  endpoints. This is separate from checking container health.

## Commands

```bash
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase plan
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase preflight
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase reconcile
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase verify
```

Use the selected tooling Python when `VENV` is elsewhere.

### Isolated controller transport

On a Linux controller, `scripts/lab-network.py` runs a command through an already
enrolled, dedicated test VPN container. Copy [the transport example](../examples/lab-network.json)
outside Git and declare its container, image digest, peer state, management URL,
overlay address and private resolver file. The resolver file accepts
`nameserver ADDRESS` and `options timeout:N attempts:N` lines. Keep both files
mode `0600` and peer state mode `0700`.

```bash
python3 scripts/lab-network.py --config /private/lab/network.json -- \
  .venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase preflight
```

The tool requires Docker access, an operator-owned `SSH_AUTH_SOCK`, noninteractive
sudo, and Linux `unshare`, `nsenter`, `mount` and `setpriv`. It verifies the running
container's image, isolation, state mount, connected management origin and overlay
address, then rechecks them before entry. A private mount namespace supplies DNS;
the command enters only the selected network namespace and runs as the operator
without capabilities. Controller routes and resolver stay unchanged. It does not
create, enroll or restart peers, modify firewall rules, or use renewal peers.

`plan` is the default and makes no managed-host connections. It validates the
local configuration and lists the sequence; it does not establish connectivity.
`preflight` runs the existing target guards on the controller, binds effective
inputs to the declared release/SSH targets, then verifies each hostname, sudo
access and VPN address. It does not validate every component input; the existing
playbooks retain their component checks.

`reconcile` checks both targets before changing either, then runs:

1. HOU1 `hou1.yml`, followed by declared container/source/TLS checks.
2. HOU4 `hou4.yml`, followed by declared container and HTTPS checks.

Each playbook uses its own repository configuration and the selected toolchain,
collections and age identity. Host limits and frozen SSH settings apply to every
invocation. Any failure stops the sequence. This is ordered reconciliation, not
a transaction that rolls back the other host.

`verify` repeats preflight and readiness checks without running deployment
playbooks. Container checks cover the declared projects, not arbitrary host
containers. They do not prove login authorization, backup recovery or UDP paths.

## Retrying and evidence

Rerun the same phase after a failure. Every attempt gets a new private directory
and reruns prerequisites; saved success markers never skip a host. Existing
idempotent playbooks repair or retry installed state. Only one workflow can use
the selected run directory at a time.

Receipts record the phase, infrastructure/application refs, input fingerprints
and passed/failed steps. Overall success is written only after the phase finishes;
an unfinished attempt remains running or failed. Fingerprints cover inventories, their sibling
`group_vars`/`host_vars`, and declared extra-variable files. Referenced capabilities
outside those paths are not a complete frozen release bundle yet. Input or SSH
drift during an attempt stops later playbook execution.

Logs may contain private operational details; keep the run directory outside
both repositories. The remote probe returns IDs and status fields, never Compose
environments, health-command output or HTTP bodies. Retain failed attempts when
recording later success. An existing-host pass does not establish a fresh install.
