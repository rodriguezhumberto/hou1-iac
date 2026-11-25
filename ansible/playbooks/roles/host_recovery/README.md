# Replacement-host recovery

The guarded test/production restore and publication playbooks share this role.
It preserves the selected recovery contract, peer guards and private SSH cutover.

Publication validates optional transports, publishes restored ingress, pins the
lost peer's exact managed groups, replaces only that disconnected identity,
reattaches the groups, restores DNS, refreshes selected transports, then verifies
private SSH before closing bootstrap access.

Publication also requires `hou1_backup_receiver_ip` from the existing backup
inputs. It is validated before opening ingress, then used to verify the exact
retained metrics/backup policies before retiring the lost peer. A standalone
recovery inventory must carry that topology input explicitly.

`host_recovery_monitoring_required` defaults to `true`. A test point without any
managed exporter groups, metrics policy or staff-probe policy may explicitly set
it to `false`; recovery still requires the exact backup policy and pins this
omission in the handoff plan. Partial monitoring resources stop recovery. No new
monitoring grants are created to make a restore pass. Production keeps the
existing requirement, and legacy handoff scopes remain compatible.

Optional inputs belong in private recovery inventory:

- `host_recovery_management_refresh_argv`: credential-free command for one
  dedicated controller transport; no command is inferred.
- `host_recovery_companion`: explicit independent HOU4 SSH alias, public IP and
  pinned known-hosts file. Only its existing `netbird` daemon is restarted.
- `host_recovery_renewal_handoff` (default `false`): require the existing
  `hou1_tls_peer_config` and pinned `host_recovery_renewal_peer_id`; replace only
  HOU1 in the exact renewal receiver group while preserving other receivers.

The role owns recovery staging and evidence under its selected recovery ID.
Test restores accept one public or RFC1918 controller `/32`; production retains
its public-source requirement. `host_recovery_isolation_mode` defaults to `host`,
preserving the existing test isolation inputs and final rehearsal gates.

For a LAN lab with independently enforced isolation, explicit `provider` mode
requires `host_recovery_provider_isolation`: the target `address`, lowercase
`mac`, `policy_sha256` and boolean `verified`. Set `verified: true` only after the
provider policy is applied and independently checked for that address/MAC.
This is an operator assertion, not guest-side verification of provider rules.
Restore and publication bind the receipt to the actual LAN interface; resume
requires the same receipt and owner. Provider mode is test-only, rejects legacy
host-isolation inputs and leaves provider rules and legacy rehearsal probes
unmanaged. Reconcile monitoring through its normal entry point afterward.

With opted-in lab DNS, cold recovery creates only its seven restored containers
before reconciling their actual bridge allowances. Writers start afterward.
This runs after recovery's firewall reset and network creation, so preparation's
earlier DNS rules cannot leave recovered services without resolver access.

The validated cold Compose override uses the standard `app_compose` ownership
header and fingerprint. Normal reconciliation can then replace recovery rendering
with declared runtime inputs while retaining the operator-owned-file guard.

`host_recovery_quarantine: true` is test-only. It restores a separate empty VM
on Docker-internal networks, binds ingress/STUN to loopback, and resolves saved
service names to the restored proxy inside those networks. It refuses publication
before failure cleanup, leaving services and evidence intact;
no controller/companion refresh or peer replacement runs. The recorded source
configuration remains intact; cold recovery renders the combined ingress listener
even when its saved override disabled that listener after ingress separation.

Optional transport refresh preserves identities and persistent data. Group binding
accepts completed/partial assignments. `host_recovery_resume` (default `false`)
requires the successful publication checkpoint and original private handoff plan;
it validates the local enrollment and pinned replacement receipt before repair.
Latched restore/publication errors still require a fresh replacement. See [production recovery](../../../../docs/production-host-recovery.md)
for inputs, failure handling and operational checks.
