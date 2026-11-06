# Isolated VPS rehearsal (2026-11-06)

Evidence for infrastructure #27 and staff #39. This deployment uses the new VPS
`139.177.151.12` (Debian 12, 4 vCPU, 16,005 MiB RAM) as a test host. Production
and the prior homelab deployment were not changed. These issues remain open;
this is not production approval.

## Deployment inputs and access

- Infrastructure: 8db0d25 on `issue-27-homelab-rehearsal`.
- Staff: `af9a46b3f3b7a96543481c82979fe4068bc01f4c` on `issue-39-homelab-rehearsal`.
- Authentik `2025.10.4`, PostgreSQL `16.6-alpine`, NetBird server `0.82.0`,
  dashboard `v2.96.0`, Nginx `1.28.0-alpine`, Certbot DNS plugin `v5.1.0`.
- Staff local image: `sha256:8dc72772a33a6ba570d9e5ecd5184001faded60bf254a4fd789cf324600c0c85`.
- Test account network: `100.91.0.0/16`; server peer `100.91.0.10`.
- Root SSH bootstrap used the user-verified ED25519 fingerprint
  `SHA256:yBUIvbYTyqbzhEcXOabgbLcwLp7UAq+nQlFWY0ASHOk`.

The isolated DNS-only names all point publicly to this VPS:

| Service | HTTPS name | Access |
| --- | --- | --- |
| Authentik | `auth-test.vps-test.pearlandpaws.com` | Public sign-in/OIDC; management requires test VPN |
| Staff | `staff-test.vps-test.pearlandpaws.com` | Test VPN plus staff authentication |
| NetBird | `netbird-test.vps-test.pearlandpaws.com` | Public test VPN control plane |
| Website | `test.vps-test.pearlandpaws.com` | Public |
| Secondary site | `landing-test.vps-test.pearlandpaws.com` | Public |

Private DNS publishes the staff and website names as `100.91.0.10` to the test
VPN's administrator/staff groups. UDP and TCP queries to the resolver on 1053
passed from a separate enrolled peer. The test account contains an empty
`prod-servers` sentinel group required by the shared policy planner; no real
production peer or production account is connected to it.

## Results

- Infrastructure suite: 81 passed, 1 skipped (82 total). The skip is the
  controller-only dnsmasq fixture; live VPN DNS queries passed over UDP/TCP.
  Staff suite: 79 passed, zero failures.

- Full `rehearse-vps.yml` run through the test VPN: 231 tasks OK, 11 changed,
  31 skipped, zero failures. Subsequent test DNS policy repair was applied and
  checked over both transports. The bootstrap, NetBird enrollment, access
  hardening, and capacity/firewall runs also completed successfully.
- Capacity: RAM, CPU, disk and free-inode gates passed. The inode check uses the
  free count. The existing I/O check proves that `iostat` can run, not sustained
  storage headroom. Image extraction and initial migrations were slow; disk
  latency/load suitability remains a production gate.
- PostgreSQL has no published host port. Authentik server/worker/database and
  the staff application are healthy with the configured Authentik limits.
- Outside VPN: staff login/API, Authentik admin/API, and NetBird setup return
  403. Spoofing a VPN address in `X-Forwarded-For` still returns 403. The public
  website returns 200. External TCP 22, 5432, 9000, 9090 and 1053 are denied.
- Fresh test VPN SSH succeeds as `dev` with sudo. SSH listens only on
  `100.91.0.10:22`; public/root/password SSH is disabled. The bootstrap recovery
  timer was disarmed only after a fresh private connection passed.
- Both isolated staff users completed authorization-code login. Protected
  reads passed, reader writes returned 403, callback replay was rejected, and
  logout invalidated the prior session cookie.
- Authentik external-provider login through NetBird completed dashboard PKCE
  exchange and rejected code replay. The new federated user was denied pending
  owner approval, then approved as an ordinary user and received API HTTP 200.
  NetBird's issuer remains its own `/oauth2`; no upgrade or direct-issuer swap
  was used.
- Scanning a 551,461,888-byte Docker image export found none of the exact fresh
  test secrets in layers/config/history. `.env` is excluded from image inputs;
  runtime secrets use a root-only environment file. No image was published.
- Authentik encrypted backup `authentik-20261106T114519Z.tar.gz.age` and the
  NetBird encrypted backup `netbird-20261106T120029.tar.gz.age` were copied to
  the controller, checksum-verified and decrypted in memory for validation.
  The Authentik archive uses a logical SQL dump and excludes live PGDATA.
- Authentik recovery: removed containers; retained the original data and
  runtime configuration in root-only quarantine directories; recreated fresh
  directories/configuration/database/containers from the controller copy of
  the encrypted archive. Exact issuer and signing keys matched. Both staff
  login/role/replay/logout probes passed afterward. Duration: 214 seconds.
  Fresh sign-in failed while Authentik was down, while VPN SSH remained usable.
- NetBird recovery: removed the external connector through its API to rehearse
  embedded-only login; stopped Authentik; completed independent local-owner
  PKCE login, saved-PAT API access, and fresh VPN SSH. Restored encrypted
  NetBird state without re-enrolling peers. Peer IDs/IPs and the original
  external connector ID matched. Authentik recovered healthy. Duration: 73
  seconds. The original test data was retained for recovery.

## Reproduction and private artifacts

The mode-0700 controller directory `/var/tmp/hou1-vps-test` contains separate
bootstrap and private-management inventories, SOPS ciphertext, age/SSH keys,
the immutable staff Git bundle, redacted check logs, and encrypted off-VPS
backup copies. Private keys and secret values are intentionally absent here.
The inventory embeds the repository's public defaults and isolated host
settings; it does not load the production Vault. Preserve this directory in an
approved encrypted recovery location before retiring the controller.

```bash
ANSIBLE_FILTER_PLUGINS="$PWD/ansible/playbooks/filter_plugins" \
ANSIBLE_ACTION_PLUGINS="$PWD/ansible/playbooks/action_plugins" \
ANSIBLE_HOST_KEY_CHECKING=True \
ansible-playbook -i /var/tmp/hou1-vps-test/management.yml \
  ansible/playbooks/rehearse-vps.yml

ssh -F /var/tmp/hou1-vps-test/ssh_config hou1-vps-test 'sudo -n true'
```

`rehearse-vps.yml` requires a single isolated host, a full staff commit SHA, a
separate test CIDR/domain and SOPS secret/key paths. It prepares the existing
common/Docker dependencies, enforces capacity before deployment, installs the
stack, enrolls the server, configures private DNS, and creates the external
Authentik connector. Access hardening follows enrollment and an independently
verified VPN administrator path; its fresh-connection probe accepts explicitly
pinned SSH gateway options. The private test peer is a separate Docker network
namespace on the homelab VM; it does not alter that host's VPN enrollment.

The restore and rollback probe scripts and exact diagnostic logs are retained
in that private directory. The restore is a fresh application-state recovery
on the same VPS with Docker/age already installed, not a provider-console or
replacement-host rebuild test. `/mnt/offhost-backups` is on the VPS itself;
only the controller copies demonstrate off-VPS protection. Scheduled transfer,
offsite retention, and failure alert delivery are not yet validated.

## Remaining gates

- Repeat the complete mirrored security matrix against this VPS, especially
  live JWKS rotation/overlap, missing groups, invalid-token variants, PKCE
  failure, revocation policy, and shared-password/session migration. Existing
  application tests cover these paths; the live checks above cover a subset.
- Validate browser rendering and real user/CLI VPN enrollment and group
  assignment, rather than only scripted OIDC and setup-key peer enrollment.
- Reconcile issue #24's production cutover/rollback automation with the proven
  external-provider approach. Its current issuer-swapping scripts were not
  used or validated by this rehearsal. Keep the embedded issuer enabled.
- Demonstrate test-to-production egress denial, provider-console emergency
  access, automated offsite backups, monitoring/alert delivery, and storage
  capacity under representative load. Test resources/credentials are separate;
  production unreachability has not been demonstrated. The Cloudflare DNS API
  credential was reused from the earlier authorized test secret set, rather
  than minted specifically for this VPS. Isolate/replace that credential before
  claiming complete production-secret separation.
- Complete issues #28/#38 runbooks with the tested procedures, resolve or obtain
  explicit acceptance of findings, then review production approval separately.
  Reusing this host for production requires fresh production resources and
  secrets; the present test data is not production state.
