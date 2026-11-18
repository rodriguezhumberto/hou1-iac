# Lab inventory generation and handoff

`scripts/prepare-lab.py` owns generated bootstrap/managed inventories. Existing
Ansible security roles still own SSH cutover and fallback. This tool does not
bootstrap VMs, enroll peers, issue certificates or reset access.

## Sources

Copy [the declaration](../examples/lab-inventory-sources.json) outside Git and
select a version 2 [workflow](deployment-workflow.md). Each host's private
`variables.yml` separates `all` overrides from `environment` inputs:

```yaml
all:
  ansible_user: dev
  ansible_python_interpreter: /usr/bin/python3
environment:
  hou1_controller_managed_tls: true
```

Supply the rest of that host's explicit settings and capability paths there.
Do not repeat release fields, `ansible_host` or `node_bootstrap`: the tool derives
release selection and builds both transport profiles. Conflicting test identity
or topology inputs fail. Bootstrap addresses must be RFC1918; managed addresses
use the workflow's test overlay and verified host keys.

Repository `group_vars/all/vars.yml` remains a referenced source. Reviewed `all`
overrides are a later group-variable file; environment values retain Ansible's
normal precedence. Scoped test variable files are explicit references, including
SOPS ciphertext. No credentials are generated or moved between scopes.

## Render and inspect

```bash
.venv/bin/python scripts/prepare-lab.py render \
  --config /private/lab/inventory-sources.json --output /private/lab/snapshots/review-1
.venv/bin/python scripts/deploy-lab.py \
  --config /private/lab/snapshots/review-1/workflow.json --phase inputs
```

Render publishes a new snapshot atomically and refuses an existing output path.
It leaves the active manifest unchanged. Files are private; `render.json` records
source/output hashes. Keep snapshots unchanged and render another when inputs or
repository shared variables change. Review effective Ansible values before
replacing an existing inventory; do not blindly delete copied overrides.

## Select managed access

After the normal bootstrap playbooks finish, run through the isolated controller
transport:

```bash
python3 scripts/lab-network.py --config /private/lab/network.json -- \
  .venv/bin/python scripts/prepare-lab.py handoff \
  --snapshot /private/lab/snapshots/review-1/workflow.json --active /private/lab/workflow.json
```

Handoff runs current input contracts and fresh pinned VPN connections to both
hosts. It requires the expected hostname/address, sudo, key-only non-root SSH,
the VPN-only live listener, inactive SSH socket activation and inactive bootstrap
fallback timers. Old success receipts cannot bypass these checks.

Only then does it atomically select the generated managed workflow. A failed
check preserves the active manifest; a successful run keeps `previous-workflow.json`
and an activation receipt. Workflows and handoffs share the run-directory lock.
The standalone `deploy-lab.py --phase handoff` performs these read-only checks
without selecting another manifest. Bootstrap fallback and independent console
recovery remain prerequisites for the earlier SSH cutover.
