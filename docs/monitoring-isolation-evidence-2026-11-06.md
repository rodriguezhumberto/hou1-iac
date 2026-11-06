# Test monitoring and isolation evidence — 2026-11-06

Paired rollout issues: infrastructure #27 and staff #39. This report now includes the fresh replacement VPS rehearsal. Production is unchanged and promotion is not approved.

Implementation candidate: `bd16f47b218aac4d1f06ffb2b108bee3cb877fa7`.
This includes the cached-geolocation startup correction and the controller code
`34ba80f769f0ec697c17755b2ee5093570f32a03`.
This includes the controller alert-state follow-up to the initial gate code
`41b1a26f185a4f57c864054a11cdf73272d702a3`.
The earlier working-tree gate deployment completed with 15 OK, 3 changed and no
failures. Redeployment from this immutable implementation ref, followed by the
final isolation and healthy-login checks, is pending restored private management.
The controller monitor now runs the committed source from its persistent private
directory. Base infrastructure remains
`8db0d2563ce314498a4a4840538fe5a1c720de81`; staff remains
`af9a46b3f3b7a96543481c82979fe4068bc01f4c`, image
`sha256:8dc72772a33a6ba570d9e5ecd5184001faded60bf254a4fd789cf324600c0c85`.
NetBird remains 0.82.0 with the embedded issuer and external Authentik provider.

The [mirrored JSON receipt](evidence/monitoring-isolation-2026-11-06.json)
contains assertions, timestamps and hashes of private logs. Raw destinations,
credentials, tokens and session cookies are excluded.

## Measured gates

- **Egress isolation:** all 11 IPv4 and 8 IPv6 host destinations were denied,
  with rejection-counter evidence. An unapproved benchmark destination was also
  denied. All seven workload namespaces denied production/default egress probes.
  Authentik worker and PostgreSQL have no external default route; the other five
  show nft rejection counters. Container IPv6 has no route; host IPv6 denials
  were measured independently. The policy denies known production addresses and
  the production overlay before established/local/update allowances. A separate
  postrouting guard preserves denial when NetBird prepends forwarding exceptions.
- **Secret boundary:** the test host received a different age identity and
  application-only SOPS ciphertext. Its key cannot decrypt the controller's
  DNS-bearing bundle or the earlier encrypted Authentik backup. The filtered
  ciphertext decrypts with the new key and excludes the DNS API token. Host
  probes passed checks for the expected recipient and absence of the two DNS
  credential files. Full exact-value host-path audit and off-VPS verification of
  the new backup remain pending management recovery; the earlier restore proof
  must not be treated as a restore under this new identity.
- **TLS:** all four isolated test certificate lineages passed controller-side
  staging renewal. The initial 20-second DNS wait failed for overlapping SANs;
  120 seconds passed all four. A renewal invocation retained valid, not-yet-due
  issued certificates, transferred only issued state, validated Nginx and
  reloaded it. Staging state and DNS credentials were never installed on the VPS.
- **Monitoring:** off-VPS checks cover services/readiness, public HTTPS, VPN,
  actual admin/reader login, reader denial, replay/logout, TLS expiry, capacity,
  backup freshness, isolation and DNS coverage. Ordinary alerts need two failed
  samples, staff login three, isolation/secret/DNS coverage one. The private
  receiver recorded firing and resolved transitions for Authentik, PostgreSQL,
  staff and Nginx outages. These used consecutive real probe samples, not elapsed
  minute detection guarantees. Unit checks also proved retry after failed delivery.
- **Ongoing controller:** receiver and monitor run outside the checkout under
  `/home/dev/.local/state/hou1-vps-test-monitor`; user lingering is enabled.
  The monitor timer is enabled and actual systemd runs report the current outage
  with no pending alert events. The receiver binds only `127.0.0.1:18765` and sends
  no email/chat notifications. The TLS timer is installed but not enabled yet.

## Failed NetBird recovery and next operation

The 20:31 UTC NetBird drill delivered its firing event after two failed samples.
A local 150-second Docker-start recovery job had been armed before shutdown.
Public API still returned 502 and the VPN/SSH path did not recover within the
runner's additional 240-second management deadline. No resolved event or recovery
pass is recorded. The later startup diagnosis and prepared fix are recorded below;
no upgrade requirement is inferred.

The operator was asked to run these commands in the provider console on
`139.177.151.12`, where recovery does not depend on the failed VPN:

```bash
docker start hou1-netbird-test-netbird-server-1
docker exec pearlandpaws-test-nginx-1 nginx -t
docker exec pearlandpaws-test-nginx-1 nginx -s reload
```

At the 20:44 UTC follow-up, the operator confirmed these commands had been run,
but public API remained 502 and private SSH remained unavailable. Container-status,
Nginx validation and an in-container upstream HTTP check were requested. The
operator supplied screenshots: the NetBird server is `Restarting (1)`, the
dashboard is up, Nginx configuration validation succeeds, and the upstream
request reports `bad address hou1-netbird-server:80`. This confirms a server
startup crash loop; an absent Docker DNS entry is consistent with that state.
The initial fatal filter matched `FATA`, but the actual marker was `FATL`.
A subsequent unfiltered startup excerpt identified the cause at 21:01 UTC:
geolocation initialization makes a HEAD request to `pkgs.netbird.io` and exits
when default-deny workload egress rejects it. Docker reports `exit=1 oom=false`.
This is a startup dependency finding, not evidence of insufficient RAM. No
successful recovery or upgrade requirement is inferred. The dashboard still
returns 200 independently of the failed API.

The 0.82.0 source confirms `server.disableGeoliteUpdate: true` selects existing
cache files without the startup HEAD request when both databases exist. The
encrypted test NetBird backup contains `GeoLite2-City_20261013.mmdb` (50,883,239
bytes) and `geonames_20261013.db` (6,946,816 bytes). The console edit was tested
against that backup's configuration: only the update flag changes, repeating the
edit is idempotent, and mode 0600 is retained. Geolocation itself stays enabled.
Both NetBird templates now set this flag only for the controller-managed isolated
test. The NetBird role checks for both nonempty cache files before rendering;
the gate role checks them before updating the flag or installing isolation.
The production default and firewall destinations are unchanged. Cache refresh
under isolation is outside this proof; these cached databases are dated October 13.

The operator was asked to apply the prepared fix in the provider console:

```bash
sed -i '/^  disableGeoliteUpdate:/d; /^server:$/a\  disableGeoliteUpdate: true' /opt/hou1-netbird/config.yaml
docker restart hou1-netbird-test-netbird-server-1
```

The operator confirmed the edit/restart commands were run, but API remained
502 and private access did not return. Current flag/cache/startup diagnostics
could not be obtained because entering further console commands is difficult.
The live repair is not counted as passed. API/VPN recovery, immutable redeployment
and a repeated NetBird firing/resolved drill remain pending. The focused seven-test gate suite
passes, including rejection of absent/partial/empty caches and production/test
configuration rendering. No successful live recovery is claimed yet.

During follow-up, a controller regression was corrected: omitted host samples
now retain existing firing state while the independent unreachable-host rule
reports the management outage. The regression check proves no false resolution
and a resolved receipt after a later healthy sample. The committed controller
program was installed and its digest verified against the checkout; this change
does not repair or claim to repair the VPN.

After recovery, diagnose and correct the cause, stop the monitor timer during
faults, repeat the NetBird drill, and require firing plus resolved receipts and
successful staff login. Run the distinct test-client-ID fault drill to prove
login alerts while Authentik/app/Nginx stay healthy. Fetch and validate the new
age-encrypted backup off-VPS, complete the exact-value host audit, reapply the
immutable gate source, rerun isolation, then verify healthy scheduled monitoring
and controller TLS renewal before enabling the TLS timer. Remove temporary
root RPC helpers/recovery jobs after drills.

## Repository verification and remaining acceptance

Infrastructure after the cache fix: **88 passed, one skipped, zero failures** using the existing real
Nginx/Compose wrappers. Staff: **79 passed, zero failures**. Both new/updated
playbooks passed syntax checks; Python parsing/undefined-name checks, YAML
parsing, diff whitespace and exact-value candidate secret scan passed.

Known production DNS coverage is checked each monitor cycle; address changes
require refreshing the explicit policy. This is address-based isolation and
covers the recorded target set, not every possible production alias. The shared
DNS capability is retained on the trusted controller only. The private receiver
proves off-VPS receipt, not delivery to an independent operator service or a
heartbeat surviving controller loss. Production alert destination, aggregate
error/login-rate monitoring, representative storage/load, provider-console
recovery, scheduled offsite retention, and final #28/#38 runbooks remain gates.
The pending console operation is not counted as a completed console drill.

The earlier live security matrix is recorded separately in infrastructure
[PR #43](https://github.com/rodriguezhumberto/hou1-iac/pull/43) and staff
[PR #49](https://github.com/rodriguezhumberto/pearland-paws/pull/49).
Its JWKS overlap and automatic IdP-to-app revocation limitations still apply.
Browser rendering and alignment of #24 production cutover automation with the
external-provider result still require review.
No prerequisite PR was merged or dependent branch rebased.

## Fresh-host reset preflight

The operator proposed a fresh install; no reset has been performed or authorized
by this report. The earlier off-VPS Authentik and NetBird encrypted archives were
re-decrypted and their required contents/hashes rechecked. Both old controller
and separate host recovery identities remain private off-VPS. The newer host-only
backup has not been retrieved; this is not a snapshot of all later test changes.
The existing bootstrap public key was verified against its controller private key.

A rebuild must prepare filtered host secrets, controller-issued test certificates
and the geolocation cache before isolation. Keep a restricted bootstrap recovery
path until NetBird has actually restarted under isolation and VPN plus staff
application authentication have passed. Then close public bootstrap SSH and
repeat the remaining fault drills. Existing private management input is not a
blank-host inventory: a rebuilt host needs the explicit public bootstrap settings,
verified replacement SSH host key, and seeded certificate/cache prerequisites.
The current full-stack entry point does not yet prove that revised rebuild order.
This preflight is preparation, not a passing rebuild or console-recovery drill.

## Fresh replacement VPS rehearsal

On 2026-11-06 the wiped host was rebuilt from immutable infrastructure `bd16f47b218aac4d1f06ffb2b108bee3cb877fa7` and staff `af9a46b3f3b7a96543481c82979fe4068bc01f4c`. The confirmed ED25519 fingerprint was `SHA256:5f+kLbOe5uHyM0m44PqlAjf022MKDEKqNmKx6rz5VLI`. The host has 16 GiB RAM, 73 GiB free disk and 1% inode use.

The fresh stack passed capacity, Authentik bootstrap, real admin/reader staff journeys, public staff 403 checks, and private admin peer enrollment. NetBird 0.82.0 started with both cached geolocation databases under `disableGeoliteUpdate: true`; geolocation remains enabled. A cold server restart restored the public API and private SSH in one second. The isolation gate then denied recorded production/default destinations from the host and all seven workload namespaces. Public SSH was closed while private NetBird SSH remained usable.

The private receiver recorded the staff client fault drill firing after three failed probes and resolving after restoration. A controller-only renewal passed all four test certificate lineages, the fresh encrypted Authentik backup decrypted off-VPS under the host recovery recipient, and the final image scan found no exact test secret values. Infrastructure tests passed 89 with one skip; staff tests passed 79.

This completes the disposable test-host monitoring and isolation rehearsal. Production approval remains withheld pending the separate production gates listed in the acceptance issues.
