# Reversible NetBird 0.82.0 Authentik cutover

The cutover is blocked unless the exact compatibility spike has passed:

```bash
cd ansible
ansible-playbook playbooks/netbird-authentik-cutover.yml \
  -e netbird_authentik_compatibility_passed=true \
  -e netbird_authentik_issuer=https://auth.example.com/application/o/netbird-prod/ \
  --ask-vault-pass
```

Before changing the issuer, the task stops the combined server and snapshots
`config.yaml`, `dashboard.env`, SQLite data, `secrets.json`, encryption keys,
peers, policies, addresses, and the existing PAT state into a root-only
cutover directory. It then swaps only the issuer/client settings and starts
the same pinned `netbirdio/netbird-server:0.82.0` and dashboard images.

The health gate checks the management API, Authentik discovery/JWKS, and running
server/dashboard containers. A failed check automatically restores the
embedded-IdP files and records a failure marker. Operators can run
`/opt/hou1-netbird/authentik-rollback.sh` with a snapshot path to restore the
embedded issuer without re-enrolling peers. The snapshot is retained for audit
and recovery; no SQLite or encryption key is regenerated.
