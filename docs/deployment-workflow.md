# Managed lab deployment workflow

`scripts/deploy-lab.py` coordinates the existing HOU1 and HOU4 playbooks. It
supports existing test hosts on `100.91.0.0/16`, with controller-managed HOU1 TLS.
It does not bootstrap hosts, enroll the controller, change public DNS, seed identities,
deploy Authentik/control-plane changes or run destructive acceptance drills.
Bootstrap and enrollment gaps are assigned in the [intervention audit](deployment-interventions.md).
The separate [fresh lab procedure](probes/lab-bootstrap.md) orders bootstrap
playbooks, peer enrollment and the managed handoff.

## Inputs

Copy [the version 2 example](../examples/lab-workflow.json) outside Git and replace its
paths, revisions, fingerprints and runtime projects. Keep the JSON, inventory,
extra-variable files and SSH config operator-owned, mode `0600`; create the run
directory as mode `0700`. Prepare tools with `make setup` in both repositories.

- Pin both infrastructure commits and select the application's CI release receipt
  and standalone source bundle. The receipt supplies the digest/revision;
  [input contracts](lab-inputs.md) check the bundle, identity and backup bindings.
  Execution
  rejects tracked checkout changes and untracked deployment code. Untracked notes
  such as `docs/mockups/` do not affect execution.
- Keep the actual namespace, image repository and runtime paths in private inputs;
  the example uses synthetic values. The workflow validates GHCR digest syntax
  and compares the complete image reference with the effective deployment inputs.
- Supply the existing private inventories and ordered extra-variable files.
  Inventories must select managed test hosts; default test groups remain empty.
  Keep `ansible_*` execution variables in inventory, not extra-variable files;
  global overrides also affect controller delegation.
  Include opted-in [lab DNS inputs](lab-dns.md) in this list so reconciliation
  maintains the resolver, Docker DNS and declared network allowances.
- Declare additional referenced files under each host's `capabilities`, including
  ciphertext loaded by custom inventory lookups. Known component file references
  and the deployment identity are included automatically.
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
  endpoints. This is separate from checking container health; declared runtime
  contracts also validate the resolved Compose readiness healthcheck and stop
  grace before accepting the deployment.

## Commands

```bash
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase plan
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase inputs
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase handoff
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase preflight
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase reconcile
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase verify
```

Use the selected tooling Python when `VENV` is elsewhere.

### Finalize a published restore

After publication and private VPN reconnection, use version 2 inputs:

```bash
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json \
  --phase finalize --recovery-identity /private/recovery/offline.agekey
```

The phase reconciles HOU1's source/storage layout and initializes `backups.yml`
under capture admission. It disables that playbook's synchronous initial capture;
a timer-triggered capture waits for admission to be released. The controller then
requests initial capture if none has succeeded and observes VPN recovery without
replaying start requests. A successful existing source point is reused.

After HOU4 reconnects, the phase holds capture admission again, reconciles HOU4,
collects the selected point, and verifies its ciphertext, offline decryption and
SQLite integrity against the selected application revision. Decrypted output is
removed on success or failure. The offline identity stays on the controller.
Capture observation is bounded to 50 minutes; collection to 33 minutes 20 seconds.
An older scheduled transfer finishes before collection is requested; both waits
share the collection budget.
Both hosts must pass final readiness checks.

To resume, add `--resume /private/lab/runs/previous/receipt.json`. Each attempt
uses a new directory and repeats reconciliation. Resume requires the same inputs,
revisions, transports and offline identity; a previously selected source point
must still match. It never silently substitutes a newer point. This phase does
not restore, publish or rebuild a host; login, reboot and alert-delivery acceptance
remain separate checks.

### Failure matrix

The non-destructive interruption matrix uses the same private workflow inputs
and maintained reconciliation paths:

```bash
.venv/bin/python scripts/lab-failure-matrix.py --config /private/lab/workflow.json \
  --matrix /private/lab/failure-matrix.json
.venv/bin/python scripts/lab-failure-matrix.py --config /private/lab/workflow.json \
  --matrix /private/lab/failure-matrix.json --apply
```

The first command validates the private matrix declarations and prints
the scenarios. `--apply` stops one declared service at a time, reconciles it,
checks recovery, and restores it in cleanup code. The dependency case runs only
the HOU4 monitoring playbook while the HOU1 endpoint is unavailable, then checks
both HOU4 local readiness and the failed remote probe. Reboot remains a separate
explicit drill because it interrupts the host. Run this command inside
`scripts/lab-network.py`; it refuses non-test workflow inputs and writes private
JSON evidence under the workflow run directory.

The optional [backup scenario](probes/backup-transfer.md) creates a fresh recovery
point, interrupts its live transfer and requires the collector's bounded retry
and controller-side offline verification. `--scenario backup` selects that case;
without selection flags the runner executes every declared scenario.

### Isolated controller transport

Before VPN enrollment, use [the bootstrap transport](../examples/lab-bootstrap-network.json)
with `transport: bootstrap`. It keeps the controller's network namespace and
supplies the same private DNS/hosts view. Every pinned name must use a lab LAN
address, including the management origin. No VPN container or peer state is
required. Ansible target guards and verified SSH host keys still apply.

```bash
python3 scripts/lab-network.py --config /private/lab/bootstrap-network.json -- \
  /private/tooling/bin/ansible-playbook -i /private/lab/bootstrap-inventory.yml \
  ansible/playbooks/rehearsal-prepare.yml
```

On a Linux controller, `scripts/lab-network.py` runs a command through an already
enrolled, dedicated test VPN container. Copy [the transport example](../examples/lab-network.json)
outside Git and declare its container, image digest, peer state, management URL,
overlay address and private resolver file. The resolver file accepts
`nameserver ADDRESS` and `options timeout:N attempts:N` lines. Keep both files
mode `0600` and peer state mode `0700`.

Optional `hosts` entries pin exact names within the selected test namespace to
private LAN or test-overlay addresses. They apply only to the wrapped command.
The private view replaces the legacy lab hosts block; declare all names in that
block. Unrelated aliases remain, and conflicting entries stop execution.

```bash
python3 scripts/lab-network.py --config /private/lab/network.json -- \
  .venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase preflight
```

The tool requires Docker access, an operator-owned `SSH_AUTH_SOCK`, noninteractive
sudo, and Linux `unshare`, `nsenter`, `mount` and `setpriv`. It verifies the running
container's image, isolation, state mount, connected management origin and overlay
address, then rechecks them before entry. A private mount namespace supplies DNS;
the command enters only the selected network namespace and runs as the operator
without capabilities. Controller routes, resolver and hosts file stay unchanged. It does not
create, enroll or restart peers, modify firewall rules, or use renewal peers.
The bootstrap transport needs the same namespace/sudo tools and SSH agent, but
does not inspect Docker or enter a VPN namespace.

An explicit private config with `transport: recovery` enters the same dedicated
VPN namespace while management is disconnected. Container/image/state/account
and overlay-address checks still apply, including revalidation before entry.
Use this for publication that reconnects the retained controller before private
SSH cutover. Normal VPN transport continues to require a connected peer.

For recovery, `scripts/reconnect-lab-controller.py --config /private/lab/network.json`
validates the same VPN configuration without changing it. `--apply` reconnects
only that controller using saved enrollment; it preserves the container and
network namespace. The complete operation has a ninety-second budget, stops on
identity/configuration changes and withholds command output. It does not verify
DNS, application readiness or companion access; use the normal workflow afterward.
Run it on the controller, outside a VPN-dependent SSH session. A recovery inventory
may select its absolute Python/script/config paths plus `--apply` through
`host_recovery_management_refresh_argv`. The older `reconnect-test-controller.py`
remains available for its historical fixed fixture.

`plan` is the default and makes no managed-host connections. It validates the
local configuration and lists the sequence; it does not establish connectivity.
Version 2 `inputs` checks both inventories and release/identity/backup contracts
without managed-host commands. These checks also precede host access in other
execution phases. Version 1 remains supported with its original checks and
explicit `application` selection; it has no `inputs` phase or capability tracking.
`handoff` additionally checks the running VPN-only SSH policy without deploying.
The [inventory tool](lab-inventory.md) uses it before selecting a generated snapshot.

`preflight` runs the existing target guards on the controller, binds effective
inputs to the declared release/SSH targets, then verifies each hostname, sudo
access and VPN address. It does not validate every component input; the existing
playbooks retain their component checks.

`reconcile` checks both targets before changing either, then runs:

1. HOU1 `hou1.yml`, followed by declared container/source/TLS checks.
2. HOU4 `hou4.yml`, followed by declared container and HTTPS checks.

### Capture coordination

`reconcile` holds HOU1's `/run/lock/hou1-backup-export.lock` across both hosts
and their final checks. Version 2 validates local inputs before admission and
checks live access afterward. Version 1 keeps its original access checks before
admission. Another controller using the same host lock also waits.

The dedicated pinned SSH connection holds the lock until EOF. The controller
sends a keepalive every ten seconds; the host releases after a minute of silence.
SSH liveness checks detect a broken connection within about 45 seconds.
Outstanding writer intent refuses deployment so existing backup recovery can
run first. Losing the guard cancels active local Ansible work and stops later
steps; existing playbook
recovery and reconciliation still handle interrupted remote operations.

Host lock admission waits at most fifteen minutes; the controller allows another
thirty seconds for connection overhead. Capture's systemd budget includes the
lock wait plus its original capture budget. Admission
failure changes no writers or previous recovery point. Inspect the service result
for a capture admission timeout; its previous successful export status is preserved.

Apply the established source's `backups.yml` once to upgrade its helper and service
budget. Routine HOU1 reconciliation then maintains the helper. The failure matrix
uses the same lock across its interruptions. Direct Ansible commands, read-only
workflow phases, TLS jobs and reboot observers do not acquire this guard; schedule
those operations separately from capture when they need uninterrupted VPN access.

For a declared isolated application bundle, HOU1 refreshes its fixed local Git
remote before selecting the checkout. Bootstrap uses the same
[source owner](../ansible/playbooks/roles/test_application_source/README.md).
The imported branch must match the selected release commit; an explicit older
release can also be selected for rollback.

Each playbook uses its own repository configuration and the selected toolchain,
collections and age identity. Host limits and frozen SSH settings apply to every
invocation. The security role's delegated SSH check uses the same workflow pin,
overriding older `security_netbird_ssh_options` in private inputs. Standalone
playbooks retain that variable's existing behavior. A failure stops later steps;
completed changes on either host remain.

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
an unfinished attempt remains running or failed. Fingerprints cover inventories,
their sibling `group_vars`/`host_vars`, and declared extra-variable files. Version 2
also fingerprints the release and referenced capabilities described in
[input contracts](lab-inputs.md). This is not a complete frozen bootstrap bundle. Input or SSH
drift during an attempt stops later playbook execution.

Logs may contain private operational details; keep the run directory outside
both repositories. The remote probe returns IDs and status fields, never Compose
environments, health-command output or HTTP bodies. Retain failed attempts when
recording later success. An existing-host pass does not establish a fresh install.
