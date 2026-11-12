# Scoped controller VPN transport

Follow-up to infrastructure #20. A controller that retires its temporary
administrative VPN peer still needs a persistent transport for certificate
delivery and numeric renewal status. This optional entry point enrolls a separate
NetBird 0.82.0 peer in a pinned container and isolated network namespace. It
does not change the host's existing VPN interface or routes.

The peer blocks inbound connections, DNS changes, and client/server routes.
One policy permits only outbound TCP/22 to the exact declared receiver peers.
The source group contains only this controller, and the destination group contains
only the declared environment servers. Existing broader memberships, implicit All
policies, reverse grants, unexpected container settings and conflicting owned
policies stop provisioning. No management token or age identity is installed in
the container. The one-off setup grant and its temporary file are removed even
when enrollment fails. Interrupted provisioning may leave an isolated container;
inspect its state before retrying. Receiver accounts still enforce their own
source-address-bound key and fixed command gateways.

Keep these inputs in the private deployment inventory:

```yaml
netbird_management_url: https://vpn.example.invalid
netbird_server_group: environment-servers
netbird_token_vault_file: /private/netbird-token.enc.yaml
netbird_token_age_key_file: /private/temporary-deployment.agekey
hou1_tls_peer_config:
  environment: production
  management_url: https://vpn.example.invalid
  subnet: 100.90.0.0/16
  server_group: environment-servers
  targets: [100.90.0.10, 100.90.0.20]
  runtime: /private/controller-renewal-peer
  container: controller-renewal-peer
hou1_tls_peer_jobs:
  hou1-production-deploy:
    program: /private/hou1-tls/controller-tls.py
    config: /private/hou1-tls/controller.json
    mode: --deploy
  hou1-production-status:
    program: /private/hou1-tls/controller-tls.py
    config: /private/hou1-tls/controller.json
    mode: --publish-status
  hou1-production-check-config:
    program: /private/hou1-tls/controller-tls.py
    config: /private/hou1-tls/controller.json
    mode: --check-config
```

Install the maintained TLS programs and their companion modules in the private
job directories using Ansible `copy`, mode `0700`. Configurations must belong to
the non-root renewal operator and must not be writable by other users. Supply
HOU4 job entries for its maintained `controller-grafana-tls.py` and
`publish-renewal-metrics.py` programs as needed; no arbitrary program or mode is
accepted by the launcher.

```bash
ansible-playbook ansible/playbooks/tls-renewal-peer.yml -e "@$INPUTS"
```

Run this as the non-root controller operator with Docker access and Ansible
privilege escalation for installing the root-owned launcher, configuration and
exact-job sudoers entries. Provisioning reads the API token only for that run;
recurring jobs do not require it. Preserve the peer's private runtime directory
on this controller; keep any exported recovery copy encrypted. Do not copy it
to HOU1 or HOU4. Recreating a lost identity requires a new setup grant and
explicit reconciliation of the old peer/group and receiver source restrictions.

The enrollment receipt records the stable VPN address. Use it for the dedicated
renewal receiver grants on both nodes, with their existing pinned host keys.
Keep administrative membership on the separate temporary deployment peer.
The receiver server group comes from the private inventory rather than an
environment-name mapping. The renewal source may belong only to its dedicated
group and NetBird's implicit All group; ephemeral identities and login or
inactivity expiration are refused.

The root-owned `/usr/local/libexec/controller-renewal-exec` accepts only a fixed
job identifier from its root-private configuration. It checks the pinned running
container and joins its network namespace. Before running the configured TLS
program it switches to the non-root operator, clears all capabilities, and sets
`no_new_privs`. The program retains the existing direct, pinned SSH contract;
no SSH proxy exception is introduced. The operator cannot supply a command or
extra arguments through the launcher.

After proving delivery, status publication and the existing receipt check, set
`hou1_tls_executor_job_prefix: hou1-production` when enabling HOU1's existing
user timers. Their outer process permits the fixed sudo entry; the launched TLS
process itself runs with `no_new_privs`. The direct transport keeps its existing
unit settings when this option is empty. HOU4 scheduling must likewise select
its fixed launcher jobs before retiring administrative transport; creating a
peer alone does not configure HOU4's timers or clear either renewal gate.

Docker and the controller's persistent user manager run these jobs independently
of an active conversation. Keep this controller online. Preserve an encrypted
owner recovery copy of the peer identity, scoped job identities and keys,
encrypted DNS/ACME/certificate state, pinned SSH profiles and executor inputs.
Do not start two controllers with the same saved peer identity. Reinstall the
root launcher through Ansible and validate paths and host keys before resuming
timers on a replacement controller. Test the recovery bundle on a separate
controller as described in [outstanding validation](documentation-review.md#outstanding-validation).

Record live namespace/host-route isolation, target-only access, denied arbitrary
commands, restart persistence, served-certificate validation and alert recovery
after transport or receiver changes. [Scoped peer validation](evidence/scoped-renewal-peer-2026-11-11.json)
and [production handoff](evidence/production-host-recovery-2026-11-12.json) record
accepted live checks. Record controller recovery runs under
[#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77).
