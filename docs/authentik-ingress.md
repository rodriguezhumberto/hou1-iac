# Authentik DNS, TLS and ingress

The selected identity profile supplies `authentik_domain`; hostname prefixes are
not fixed. Public DNS reaches HOU1 ingress so a device can authenticate before
joining the VPN. [Controller TLS](controller-tls.md) supplies certificates in
controller-managed mode; DNS credentials stay off the node.

Nginx forwards the HTTPS host/protocol and client-address headers and supports
Authentik WebSockets. It routes discovery, JWKS, authorization, token, callback
and logout traffic to the environment's private Compose service. Use discovery
metadata for protocol endpoints rather than constructing them from hostnames.

Public sign-in includes `/api/v3/flows/executor/`. The more specific executor
location is deliberately public; `/if/admin/` and the remaining `/api/v3/` API
are VPN-only. PostgreSQL has no public listener. Nginx configuration validation
must pass before reload.

The applications overview needs the private API. VPN DNS resolves this hostname
to HOU1's VPN address while public DNS remains available for first login. If the
overview loops to login, check the actual system resolver/route before changing
access controls. The owner verified a network-specific macOS Private Relay pause
as a workaround; globally disabling Private Relay is not a proven requirement
for Grafana or every service.
