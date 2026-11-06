# LAN rehearsal evidence (2026-11-06)

This is an isolated LAN-only rehearsal on the homelab VM `192.168.0.151`. It does not change the production NetBird node or production Authentik deployment. The test names are:

- `https://auth-test.spare-test.pearlandpaws.com`
- `https://staff-test.spare-test.pearlandpaws.com`
- `https://netbird-test.spare-test.pearlandpaws.com`

The names resolve through the controller/VM LAN hosts mapping. Public DNS-01 certificates were issued for the four test names with the registration address supplied for this test. No router forwarding is enabled.

## Immutable inputs

- Infrastructure branch: `issue-27-homelab-rehearsal`, commit `6be667f` (includes `af74c1c`).
- Staff application commit: `c762675c96ed5f3f220e96e83a854506240afb45`.
- Authentik: `ghcr.io/goauthentik/server:2025.10.4` digest `sha256:4b4f9ae106dbda902b836aa7a79d2f456b8302f090b862f7ad1bf268402730b2`.
- PostgreSQL: `postgres:16.6-alpine` digest `sha256:1d04b9ba1d4996401f2552b51beda8187f175c0645c091e4781134fc9c9a3eef`.
- NetBird server: `netbirdio/netbird-server:0.82.0` digest `sha256:7b5b53b2dc8bc25639f791cf72d47cf86911894ff3f8a97cb79bab9be958109c`.
- NetBird dashboard: `netbirdio/dashboard:v2.96.0` digest `sha256:340b13c00b84349572a2acc5605e907cb09250a9e6461c757097eabdb8e02f2c`.

## Checks passed

- VM placement gate: 4 vCPU, 7.8 GiB RAM, free disk/inodes and I/O checks passed.
- Authentik server, worker and PostgreSQL are healthy; PostgreSQL has no host port.
- Staff login passed for the isolated admin and reader users; protected read passed; reader mutation returned 403; callback replay was rejected; logout revoked the old session.
- NetBird external Authentik provider was created through the authenticated 0.82.0 API. The embedded NetBird issuer remains `https://netbird-test.spare-test.pearlandpaws.com/oauth2`.
- NetBird Authentik flow passed dashboard callback, PKCE token exchange, protected API permission handling, and authorization-code replay rejection. The federated test account is still pending NetBird owner approval, so management API access correctly returns 403 until that account is approved; this is an explicit rollout finding.
- Authentik outage caused the live login probe to fail closed; Authentik returned healthy after restart.
- An encrypted SOPS/age backup was checksum-verified off VM and restored into a fresh temporary PostgreSQL instance (`django_migrations` populated).
- Host firewall defaults to deny incoming; SSH is limited to `192.168.0.150`, web ingress to the LAN, PostgreSQL is denied, and a persistent `DOCKER-USER` rule restricts published Docker ports to `192.168.0.0/24`. NetBird STUN binds only to `192.168.0.151:3478`.
- Staff application test suite: 79 passed. Infrastructure targeted regression suite: 16 passed, including the rendered-stack/Nginx check run against the VM's pinned binaries. A prior full controller run had one environment-only Nginx parser failure caused by the disposable read-only wrapper; the corrected targeted run passed.

## Remaining gates

This is a LAN rehearsal, not production approval. A clean-host full restore with service re-provisioning, production rollback, peer/CLI connectivity, gRPC/WebSocket probes, monitoring/alert delivery, and the production rollout runbook still require their respective issue evidence. The local test cron is not considered a production backup schedule until its SOPS/age recipient and off-host destination are configured.

No NetBird upgrade is proposed: the 0.82.0 external-provider flow passed and the embedded issuer was retained.
