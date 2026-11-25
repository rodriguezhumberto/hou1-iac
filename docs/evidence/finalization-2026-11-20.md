# Published lab finalization — 2026-11-20

The existing replacement HOU1 and retained HOU4 completed the new controller
`finalize` phase, followed by a full receipt-based resume. Both runs used HOU1
`4c78955` and HOU4 `3d0ae0f`. Neither VM was rebuilt; the original HOU1 stayed fenced.

## Results

- HOU1 source/storage reconciliation preceded backup initialization under capture
  admission. Capture observation ran after admission was released.
- Both runs reused the successful source point and verified its exact off-host
  ciphertext, offline decryption, SQLite integrity and selected application revision.
- Complete service-to-container-ID mappings stayed identical: 8 HOU1 and 4 HOU4
  containers. Both hosts passed final readiness checks. No repair commands were needed.
- Decrypted verification output was removed after each attempt; private receipts,
  logs and ciphertext remain outside Git.

Review then added a bounded wait for an older scheduled receiver transfer before
requesting collection of the selected point. The final collection path at HOU1
`421f2ca` passed a separate lab collection, offline verification and both-host
readiness check, again preserving the complete mappings and source point.

Local validation passed 89 tests, all playbook syntax checks and lint. Tests cover
initial capture with no export, deferred playbook capture, lock ordering, failure
boundaries, lost SSH responses, resume drift, point preservation, older transfers
and plaintext cleanup. Live runs reused an existing successful point; a new
empty-host rehearsal is still required to prove the full restore sequence without
manual correction.

## Reproduce

Use the [finalization command](../deployment-workflow.md#finalize-a-published-restore)
with frozen private version 2 inputs and the controller-only recovery identity.
Pass the previous private receipt through `--resume` for the second attempt.
