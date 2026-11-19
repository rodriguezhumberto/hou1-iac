# Lab peer handoff

Replace the active HOU1 lab through the existing test recovery playbooks.
Keep HOU4 data, peer identity and production configuration intact.

## Endpoint decision

The selected plan transfers the existing HOU1 LAN address to the replacement
after fencing the old VM. This preserves the endpoint and existing companion
firewall rules, matching the supported same-address recovery workflow. Keep the
replacement's distinct-address companion firewall exception deferred.

The provisioning project owns address assignment, source-address filtering and
VM autostart. Prepare its exact per-VM changes before interruption; apply them
only after the old VM is fenced. Selecting this plan does not execute VM or
network changes.

## Prepare before interruption

1. Bind the selected endpoint, replacement NIC and verified SSH key in the
   provisioning project and private inventories. Confirm independent console or
   guest-agent recovery for both HOU1 and HOU4.
2. Validate the current dedicated controller with `reconnect-lab-controller.py`
   using its existing private transport config. Select its `--apply` argv for
   recovery as described in the [workflow](../deployment-workflow.md#isolated-controller-transport).
   Select independent HOU4 refresh through working pinned SSH or the provider
   console; its VPN connection cannot serve as its own recovery path.
3. Freeze the infrastructure revisions, image lock/source bundle, encrypted
   capabilities and a final verified off-host point. Record authorization drift
   and pin the original HOU1 peer's ID/name/address from that point.
   Use the [provider isolation contract](../../ansible/playbooks/roles/host_recovery/README.md)
   for this lab. Freeze the independently checked applied-policy record, bind its
   digest to the replacement LAN address/MAC, and retain that owner through resume.
4. Preserve the quarantine evidence, verified restore inputs and original HOU1
   boot disk for rollback. A quarantined copy cannot be promoted by changing an inventory
   flag. A new normal restore requires an empty replacement and unused recovery
   and evidence IDs; rebuilding it is a separate destructive step.

## Execute the selected transition

1. Fence the original HOU1 VM and prevent automatic restart. Only one restored
   control plane may own the service endpoint or VPN identity. Record the final
   point's age and any writes between capture and fencing.
2. Prepare the empty replacement with the selected LAN binding and a newly
   verified SSH key. Set `host_recovery_quarantine: false`, select the verified
   provider boundary and run `restore-test-host.yml` once.
3. Verify recovered credentials, readiness and access changes while listeners
   remain on loopback. Then select the reviewed access assertion and run
   `publish-test-recovery.yml`, using only the pinned disconnected lost peer.
4. Refresh the dedicated controller and retained companion, verify private SSH,
   and reconcile monitoring, backup and renewal through their existing entry
   points. Update the backup source SSH pin through its owning role.
5. Require login/denial checks, real peer transport, a fresh verified off-host
   point, alert delivery/recovery, reboot recovery and unchanged reconciliation.
   Preserve failed observations separately from later repairs.

## Rollback

Keep the original VM fenced during acceptance. Before restoring its endpoint,
stop and fence the replacement. Preserve replacement data and receipts; if it
has accepted writes, reconcile those changes before reverting to the older disk.
Restore the original private trust/endpoint bindings and backup pin, refresh
clients, then rerun readiness, login and backup checks. Publication resume follows
the retained handoff plan; it does not authorize running both control planes.
