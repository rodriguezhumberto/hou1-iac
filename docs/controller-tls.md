# Controller-managed HOU1 TLS (infrastructure #20)

This path supports initial issuance and renewal for explicitly configured test or
production certificates. DNS credentials and ACME account state stay on the
trusted controller. HOU1 receives only certificate chains and private keys.
The existing rehearsal helpers retain their test-only guards.

If deployment access is temporary, provision a
[separate renewal VPN peer](controller-renewal-peer.md) before retiring it.
Its fixed namespace jobs retain the direct pinned SSH contract below.

No live production issuance, delivery or alert validation is claimed by offline
tests. Keep the production TLS gate open until the checks below pass.

## Private inputs

Use a controller directory owned by the renewal operator with mode `0700`.
Configuration, age identity and SSH private key files must be owned by that
operator with mode `0600`. Keep organization names and deployment inputs in
SOPS-managed sources, outside plaintext repository configuration.

The controller JSON configuration has these fields:

| Field | Required value |
| --- | --- |
| `environment` | `test` or `production`. |
| `lineages` | Mapping of lineage hostname to `domains` and `probe_hosts` lists. |
| `email` | Approved ACME registration email. |
| `recipients` | Owner age recipient plus the scoped renewal recipient. |
| `age_key_file` | Absolute controller path to the scoped renewal identity. |
| `dns_secret_source` | Absolute SOPS JSON path containing only `cloudflare_dns_api_token`. |
| `netbird_subnet`, `netbird_ip` | Declared VPN subnet and HOU1 address. |
| `ssh_config`, `ssh_host` | Protected SSH configuration path and its renewal host alias. |

Use a DNS token limited to DNS edits for the approved zone. Give the renewal
identity access to that DNS-only bundle, ACME state and certificate bundle;
do not grant it application, identity-provider, backup or administrative secrets.
Keep an owner-decryptable copy of `acme.enc.json` and `certificates.enc.json`
outside the controller as part of the release recovery materials.

The lineages must include every `ssl_domains[].name`, the selected identity
profile's issuer hostname and `netbird_domain`. Web SAN lists must match the
existing `-d` flags. Identity and VPN lineages each cover their own hostname.
A probe hostname must be a real served name covered by that lineage; it cannot
be a wildcard. Multiple SANs and any number of configured lineages up to ten
are supported. No suffix, organization name, container name or fixed lineage
count is embedded in the controller.

Supply matching protected Ansible inputs:
`hou1_controller_managed_tls: true`, `hou1_tls_identity_profile` (`test` or `prod`),
`hou1_tls_lineages`, `hou1_tls_bundle_source` and `hou1_tls_age_key_file`.
The last two identify the controller's encrypted `certificates.enc.json` and
its scoped decryption identity. The bundle is decrypted into Ansible memory
under `no_log`; no age identity or DNS credential is copied to HOU1.

## Initial deployment and scheduling

1. Issue on the controller using the approved production inputs:

   ```bash
   python3 scripts/controller-tls.py --config "$TLS_CONFIG" --issue
   ```

   Certbot runs in the pinned Cloudflare plugin image. It uses protected
   temporary files and persists SOPS-encrypted ACME state and deployable material.
   `--issue` does not connect to HOU1. `--dry-run` requires existing issued state
   and changes neither encrypted state nor the remote host.
2. Run the existing authorized bootstrap/deployment with the matching private
   Ansible inputs. After the app checkout exists, `webstack` imports the encrypted
   bundle before requiring its certificates. An entirely empty certificate set
   can be imported before Nginx or NetBird exists, over the bootstrap access path.
   It does not count as a verified renewal. On a running host, import validates
   Nginx, reloads only changed material, verifies served certificates and restores
   the prior files on failure. Successful import removes legacy host DNS files,
   the old renewal script and its cron entry. Authentik and NetBird use the same
   controller mode and do not request host-side DNS credentials.
3. Once private management works, configure the renewal account through an
   administrative Ansible run:

   ```bash
   ansible-playbook -i "$INVENTORY" ansible/playbooks/tls-renewal-access.yml \
     -e "@$INPUTS"
   ```

   Set `hou1_tls_inventory_group` to one private host, and supply
   `hou1_tls_renewal_public_key` and `hou1_tls_controller_ip`. Use a dedicated key.
   The controller peer must already have narrowly approved VPN reachability to
   HOU1's private SSH listener. This playbook does not broaden NetBird policies
   or public firewall access.

   `hou1-renewal` has no administrative groups. Its root-controlled key is
   restricted to that exact VPN source and the `deploy-v1` / `status-v1` gateway.
   Sudo permits only the fixed argument-free receiver and its `--metrics` mode;
   bootstrap, shells, forwarding, file transfers and arbitrary commands are denied.
4. Configure SSH with the pinned host key, HOU1 VPN address, `User hou1-renewal`,
   `StrictHostKeyChecking yes`, a populated `UserKnownHostsFile`, its dedicated
   `IdentityFile`, `IdentitiesOnly yes`, `BatchMode yes`, and
   `PasswordAuthentication no`. Proxy commands/jumps are refused. Verify delivery:

   ```bash
   python3 scripts/controller-tls.py --config "$TLS_CONFIG" --deploy
   python3 scripts/controller-tls.py --config "$TLS_CONFIG" --check-config
   ```

   The receiver validates exact SANs, trusted CA chains, expiry, key matching and environment before
   replacing any material. It validates Nginx before reload and checks CA trust,
   hostname and the expected leaf fingerprint over a fresh TLS connection for
   every probe hostname. No-op deployments still verify the served certificates.
   Only verified delivery advances the private success receipt.
5. Enable the controller user timers with `tls-renewal-schedule.yml`, supplying
   `hou1_tls_schedule_environment`, `hou1_tls_controller_config` and boolean
   `hou1_tls_schedule_enabled: true`. The selected encrypted bundle must have a
   matching verified deployment receipt from the last 26 hours. The controller
   user manager must be active with lingering enabled.

   Renewal runs twice daily at 03:15 and 15:15 UTC with up to five minutes of
   jitter; the timer persists missed runs. A separate minutely timer publishes
   status. Timer names include the explicit environment. Disable scheduling by
   running the same playbook with that environment and the enable flag `false`.
6. Publish status and enable the existing VPN collector integration:

   ```bash
   python3 scripts/controller-tls.py --config "$TLS_CONFIG" --publish-status
   ```

   Set `hou1_tls_metrics_enabled: true` on HOU1 and
   `hou4_hou1_tls_enabled: true` alongside HOU4's private `hou1-node` source.
   HOU1 measures the actual deployed certificate expiry. Status inputs reject
   another environment, invalid numbers, stale/future timestamps and timestamps
   older than the latest accepted publication. Failed runs preserve last success;
   failure of the systemd service is published even if it wrote no receipt.

## Acceptance and recovery

Record release refs and redacted evidence for ACME dry-run, verified delivery,
all served names, public/unrelated-peer denial, forbidden renewal-key commands,
and Discord firing and resolution. Prove that a failed delivery preserves the
previous certificate and does not advance success. Check that host DNS files,
the legacy cron and administrative controller grants are absent.

HOU4 alerts on a failed last run for two minutes, no successful delivery within
26 hours for five minutes, publication missing/stale beyond five minutes for two
minutes, and certificate expiry below fourteen days for ten minutes. Existing
request/callback thresholds are unchanged. A broken VPN can prevent publication;
HOU4's target availability and stale-publication alerts cover that failure.

The controller retains previous encrypted ACME and certificate bundles. The
receiver restores original file content or symlinks if validation after install,
reload or served verification fails. For operator rollback, stop the selected
timer, select the reviewed previous encrypted bundle, and import it through the
same administrative Ansible path. Validate the current certificate's remaining
lifetime first; expired or near-expired material is refused. Recheck every served
name and the renewal status before enabling the timer. Preserve the current
controller state until the replacement is verified. Production replacement-host
recovery remains a separate gate.
