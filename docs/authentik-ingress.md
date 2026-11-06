# Authentik DNS, TLS, and Nginx ingress

The Authentik role publishes `auth-test.<primary_domain>` for the test
project and `auth.<primary_domain>` for production. Cloudflare DNS must point
each hostname at the existing HOU1 ingress address before the play is run. A
DNS-01 certificate is stored in the existing Certbot tree and is renewed by
the existing renewal job.

The virtual host is mounted into the existing Nginx container through the
infrastructure-owned include directory. It forwards `Host`, `X-Real-IP`,
`X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host`, keeps HTTP/1.1
upgrade headers for Authentik WebSockets, and routes discovery, JWKS,
authorization, callback, token, and logout paths to the matching environment's
private Compose service. The issuer is the exact HTTPS hostname configured in
the OIDC contract.

`/if/admin/` and `/api/v3/` are limited to the NetBird subnet. OIDC endpoints
remain reachable over HTTPS so the staff and NetBird clients can authenticate;
PostgreSQL is never exposed through this ingress. The role runs `nginx -t`
before every reload and leaves the prior configuration in place when
validation fails.
