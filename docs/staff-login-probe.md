# Environment-bound staff login monitoring

This follow-up to infrastructure #28 supplies an optional synthetic login probe.
It is disabled by default. Offline verification does not clear the live production
monitoring gate or prove the upstream Google token exchange.

## Identity and encrypted inputs

Create a separate internal Authentik account for each environment, with only the
existing staff reader group. Give it no Authentik administration, NetBird service
access, VPN administration, or human Google credentials. The selected local
authentication flow must permit this synthetic account without weakening human
login policy. Keep its username and independently generated password in a separate
SOPS JSON bundle:

```json
{
  "environment": "production",
  "kind": "synthetic-reader",
  "username": "<dedicated reader>",
  "password": "<random password of at least 24 characters>"
}
```

Encrypt to the owner's age recipient; grant the deployment controller temporary
access only when needed. HOU4 decrypts on the controller under `no_log`, installs
only this synthetic credential in a private runtime mount, and discards the
decrypted Ansible variable. No age identity or management API token goes into
the probe container. Rotate the account password separately when retiring access.

## Explicit deployment contract

First reconcile `ansible/playbooks/monitoring.yml` using the inventory/profile
inputs in [environment-monitoring.md](environment-monitoring.md), adding boolean
`hou1_staff_probe_enabled: true`. This adds one marked, unidirectional TCP/443
NetBird policy between the existing single-peer HOU4/HOU1 metrics groups. It
does not grant staff or administrative membership. Keep the existing VPN-only
ingress restrictions; verify HOU4's selected peer can reach the private staff
origin while public and unrelated peers remain denied.

In the companion HOU4 playbook set:

| Input | Value |
| --- | --- |
| `hou4_staff_probe_enabled` | Boolean `true`. |
| `hou4_staff_probe_program_source` | Absolute controller path to `scripts/staff-login-probe.py` at the selected HOU1 `INFRA_REF`. |
| `hou4_staff_probe_secret_source` | Absolute controller path to the separate SOPS bundle. |
| `hou4_staff_probe_age_key_file` | Absolute path to the scoped controller age identity. |
| `hou4_staff_probe_config` | The explicit contract below; keep real service names in encrypted release inputs. |

```json
{
  "environment": "production",
  "auth_origin": "https://identity.example.invalid",
  "staff_origin": "https://staff.example.invalid",
  "issuer": "https://identity.example.invalid/application/o/reader/",
  "client_id": "reader-client",
  "flow": "default-authentication-flow",
  "session_cookie": "<actual application cookie name>",
  "netbird_subnet": "100.90.0.0/16",
  "server_ip": "100.90.0.10"
}
```

The environment, subnet and server IP must match HOU4's existing sole `hou1-node`
scrape target. Use the actual deployed client, issuer, flow and cookie name. The
controller validates the maintained script/config before host installation.
No image is built on HOU4: its non-root, read-only container uses a pinned Python
interpreter, with only a separate numeric output directory writable. Node exporter
mounts that output read-only and cannot read the credential mount.

## Checks and failure behavior

The probe runs sequentially once per minute. Every HTTPS socket connects to the
declared HOU1 VPN IP at port 443, preserving Host, SNI and default CA validation.
It ignores environment proxies and rejects destinations outside the exact staff
and identity origins. Credential POST redirects cannot cross origins.

It checks discovery issuer, Authorization Code with S256 PKCE, client/callback,
state and nonce presence, callback state agreement, secure staff cookie, protected
read, reader permission and logout. The permission check sends same-origin
`PATCH /api/staff/cms/pets` with `{}` and **no record ID**, expecting 403. If the
account is accidentally privileged, the current application rejects the missing
ID with 400 before writing; that response fails the probe. Keep this application
contract intact when changing the CMS API. After logout, the old cookie must get
401 directly; redirects fail the check. Failed runs attempt logout cleanup.

Only numeric metrics and generic pass/fail messages are published. No response
bodies, codes, credentials, subjects or exception details enter monitoring output.
The producer label is `probe_environment` so a Prometheus target's `environment`
label cannot conceal a stale metric from another environment.

HOU4 alerts after three consecutive failed checks. Missing metrics, a wrong
producer environment, a last run older than 180 seconds, or a timestamp more than
30 seconds in the future trigger the stale rule after a two-minute hold. The
existing callback latency thresholds are unchanged. Two dashboard panels show
the latest result and probe age. Deployment waits for a successful run newer than
the post-reconciliation verification boundary, rather than accepting old success.

## Live acceptance and rollback

Record selected HOU1/HOU4 refs, enabled-policy readback, successful fresh login,
protected read/reader denial/logout, private scrape, and public/unrelated-peer
denial. Prove Discord **firing and resolved** delivery using an isolated test
identity/client. Stop only the optional `staff-probe` container to exercise the
stale rule, then restart and verify recovery. Do not stop the monitoring stack
or use a human password to inject a failure. Record a human Google login and VPN
connection separately; this local synthetic login does not exercise Google.

For rollback set both enable flags to false and rerun the respective playbooks.
HOU4 removes the optional container, runtime credential and alert rules, and HOU1
disables only its marked HTTPS probe policy. Numeric output and dashboard panels
may retain the last observation as historical evidence. Disable/delete or rotate the synthetic account
separately, and retire the temporary controller grant. Preserve metrics, backup,
VPN enrollment and administrative policies.
