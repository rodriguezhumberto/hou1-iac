# LAN rehearsal evidence (2026-11-06)

This is an isolated LAN-only rehearsal on the homelab VM `192.168.0.151`. It does not change the production NetBird node or production Authentik deployment. The test names are:

- `https://auth-test.spare-test.pearlandpaws.com`
- `https://staff-test.spare-test.pearlandpaws.com`
- `https://netbird-test.spare-test.pearlandpaws.com`

The names resolve through the controller/VM LAN hosts mapping. Public DNS-01 certificates were issued for the four test names with the registration address supplied for this test. No router forwarding is enabled.

## Immutable inputs

- Infrastructure deployment fixes: `6be667f` (includes `af74c1c`), with the dashboard callback entry-page fix in `48f3515`, on branch `issue-27-homelab-rehearsal`. The VM also has the disposable LAN/secret/backup overrides described below; this is not proof of an unmodified clean-host deployment.
- Staff application commit: `c762675c96ed5f3f220e96e83a854506240afb45`.
- Authentik: `ghcr.io/goauthentik/server:2025.10.4` digest `sha256:4b4f9ae106dbda902b836aa7a79d2f456b8302f090b862f7ad1bf268402730b2`.
- PostgreSQL: `postgres:16.6-alpine` digest `sha256:1d04b9ba1d4996401f2552b51beda8187f175c0645c091e4781134fc9c9a3eef`.
- NetBird server: `netbirdio/netbird-server:0.82.0` digest `sha256:7b5b53b2dc8bc25639f791cf72d47cf86911894ff3f8a97cb79bab9be958109c`.
- NetBird dashboard: `netbirdio/dashboard:v2.96.0` digest `sha256:340b13c00b84349572a2acc5605e907cb09250a9e6461c757097eabdb8e02f2c`.
- Nginx: `nginx:1.28.0-alpine` digest `sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284`.
- TLS renewal: `certbot/dns-cloudflare:v5.1.0` digest `sha256:8181d9632ee6af74234ede88469f9f4d9b7faab4ed4cd7e77e66afca908ea992`.
- Private local staff image digest: `sha256:6645d5e8decef8ba9f500a2d42a1c369c7c87925541aa9f149603d5347377cf7`.

## Checks passed

- VM placement gate: 4 vCPU, 7.8 GiB RAM, free disk/inodes and I/O checks passed.
- Authentik server, worker and PostgreSQL are healthy; PostgreSQL has no host port.
- Staff login passed for the isolated admin and reader users; protected read passed; reader mutation returned 403; callback replay was rejected; logout revoked the old session.
- NetBird external Authentik provider was created through the authenticated 0.82.0 API. The embedded NetBird issuer remains `https://netbird-test.spare-test.pearlandpaws.com/oauth2`.
- NetBird Authentik flow passed dashboard callback, PKCE token exchange, protected API access (HTTP 200 after approval), and authorization-code replay rejection. The federated test account was approved as an ordinary user through the isolated owner API; the owner-approval boundary was also observed before approval.
- Authentik outage caused the live login probe to fail closed; Authentik returned healthy after restart.
- An age-encrypted backup (including SOPS ciphertext and runtime configuration) was checksum-verified off VM. Its logical SQL restored into a fresh temporary PostgreSQL instance (`django_migrations`: 640); no full application/clean-host restore is claimed. The later backup `authentik-20261106T093425Z.tar.gz.age` was also copied off VM and verified. The controller copy and recovery keys are in the mode-0700 `/var/tmp/hou1-homelab-rehearsal`, with private files mode 0600.
- Host firewall defaults to deny incoming; SSH is limited to `192.168.0.150`, web ingress to the LAN, PostgreSQL is denied, and a persistent `DOCKER-USER` rule restricts published Docker ports to `192.168.0.0/24`. NetBird STUN binds only to `192.168.0.151:3478`.
- Staff application test suite: 79 passed. Infrastructure suite: 75 passed, 1 skipped (76 total), using the VM's pinned Nginx and Docker Compose binaries through a disposable parser wrapper. The skip is the DNS resolver integration check because this controller lacks `dnsmasq`.

## Test operations and exceptions

The VM's `/opt/hou1-authentik/test`, `/opt/pearlandpaws-test`, and `/opt/hou1-netbird` are the three isolated Compose deployments. The Authentik bootstrap uses the nested test DNS suffix and isolated SOPS secrets. Test admin/reader accounts are generated locally; the federated NetBird user has the ordinary `user` role, separate from the local owner.

The test-only Authentik management paths accept the LAN for bootstrap. This does not validate the production NetBird-only management restriction. The Docker firewall service `/etc/systemd/system/hou1-test-firewall.service` restricts published ingress independently of UFW. Firewall configuration was inspected; an external-source packet probe remains outstanding.

The root cron now calls `/opt/hou1-authentik/test/backup-test-run` daily at 02:40 UTC with an explicit age recipient. The backup script's `/mnt/offhost-backups` directory is on this same VM; only the verified controller copy provides off-VM protection in this rehearsal. Automated offsite retention and alert delivery remain untested. Root cron also calls `/usr/local/sbin/renew-hou1-test-tls` twice daily, and its DNS-01 dry run passed.

The TLS certificate includes the additional isolated public-site name `test.spare-test.pearlandpaws.com`. Certificate registration used the email supplied for this rehearsal. Each LAN client needs the test names mapped to `192.168.0.151`; only the controller and VM hosts files were configured here.

From the controller, retrieve test passwords interactively with `ssh -t dev@192.168.0.151 sudo /usr/local/sbin/hou1-test-credentials`. This helper decrypts in memory and refuses redirected output. Do not paste its output into issue/PR evidence.

## Remaining gates

This is partial evidence for infrastructure #27 and staff #39, not production approval. A clean-host full restore with service re-provisioning and login, rollback/break-glass drills, peer/CLI connectivity, gRPC/WebSocket probes, JWKS rotation, monitoring/alert delivery, shared-password migration, explicit production egress isolation, and the production rollout runbook still require their respective issue evidence. The production environment was not contacted or modified during this rehearsal, but its unreachability from test was not demonstrated by an egress-deny test.

The existing staff Dockerfile copies `.env` into the local image. This rehearsal used test-only secrets and published no image; removing secret-bearing image layers before publishing a production image remains an unresolved finding. The runtime backup script also needed disposable fixes for logical-only PostgreSQL backup and explicit age identity selection; those fixes still need to be reconciled into the reusable backup/restore automation and retested before its acceptance can be claimed.

No NetBird upgrade is proposed: the 0.82.0 external-provider flow passed and the embedded issuer was retained.
