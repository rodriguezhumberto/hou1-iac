# Backup transfer interruption

Use the existing isolated HOU1/HOU4 workflow and enabled unified backups. Upgrade
HOU4's `backup_receiver` through `hou4.yml --tags backup` first; its collection
unit must have the bounded runtime. Require an intact committed point on HOU4.

Copy [the matrix example](../../examples/lab-failure-matrix.json) outside Git,
mode `0600`. Preserve the reviewed service selectors. The optional `backup`
declaration names HOU4 and the controller's private offline recovery identity.
The key is fingerprinted, matched to HOU1's recipient and kept off both VMs.
Omit that declaration to retain the original three-scenario matrix.

Run inside the maintained [controller transport](../deployment-workflow.md#isolated-controller-transport):

```bash
.venv/bin/python scripts/lab-failure-matrix.py --config /private/lab/workflow.json \
  --matrix /private/lab/failure-matrix.json --scenario backup
.venv/bin/python scripts/lab-failure-matrix.py --config /private/lab/workflow.json \
  --matrix /private/lab/failure-matrix.json --scenario backup --apply
```

The scenario:

1. Validates both inputs and retained ciphertext, then requests a fresh local
   source capture outside the deployment guard. Capture briefly pauses writers.
2. Holds capture admission and waits for both private SSH paths after source
   capture, then starts collection once. A host-local observer kills only
   the collector's pinned SSH child after partial bytes arrive. PID identity is
   checked and signalled through a pidfd; administrative SSH remains untouched.
3. Checks the prior point's bytes and metadata at the interruption boundary.
   The same collector must retry without another service start, commit the exact
   source point and remove staging.
4. Copies that encrypted point to private controller storage, verifies age and
   SQLite integrity against the selected release, removes plaintext and checks
   both hosts' readiness.

Ciphertext must exceed three MiB to observe a partial stream. A missed window
fails the drill. This interrupts transport; killing the collector itself remains
a separate case. The matrix receipt records selected scenarios and private results.
