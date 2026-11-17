# Replacement-host recovery

The guarded test/production restore and publication playbooks share this role.
It preserves the selected recovery contract, peer guards and private SSH cutover.

Publication validates optional transports, publishes restored ingress, pins the
lost peer's exact managed groups, replaces only that disconnected identity,
reattaches the groups, restores DNS, refreshes selected transports, then verifies
private SSH before closing bootstrap access.

Optional inputs belong in private recovery inventory:

- `host_recovery_management_refresh_argv`: credential-free command for one
  dedicated controller transport; no command is inferred.
- `host_recovery_companion`: explicit independent HOU4 SSH alias, public IP and
  pinned known-hosts file. Only its existing `netbird` daemon is restarted.
- `host_recovery_renewal_handoff` (default `false`): require the existing
  `hou1_tls_peer_config` and pinned `host_recovery_renewal_peer_id`; replace only
  HOU1 in the exact renewal receiver group while preserving other receivers.

The role owns recovery staging and evidence under its selected recovery ID.
Optional transport refresh preserves identities and persistent data. Group binding
accepts completed/partial assignments. `host_recovery_resume` (default `false`)
requires the successful publication checkpoint and original private handoff plan;
it validates the local enrollment and pinned replacement receipt before repair.
Latched restore/publication errors still require a fresh replacement. See [production recovery](../../../../docs/production-host-recovery.md)
for inputs, failure handling and operational checks.
