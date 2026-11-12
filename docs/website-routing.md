# Explicit public website routing

Follow-up to infrastructure #27 and staff #39: the selected deployment can omit
the secondary static website and redirect public aliases to one canonical HTTPS
hostname. Configure these choices in encrypted release inputs; no real domain
name is required in shared code or a PR description.

```yaml
webstack_secondary_site_enabled: false
webstack_public_canonical_redirect: true
primary_domain: site.example.invalid
public_site_origin: https://site.example.invalid
public_site_hostnames:
  - site.example.invalid
  - www.site.example.invalid
staff_domain: staff.example.invalid
```

Choose a compatible immutable application revision containing
`nginx/secondary.conf.template` and canonical redirect support. Publish and select
its matching CI image receipt through the normal digest workflow. Ansible refuses
the new options on older application source. Changing routing requires a normal
runtime render; do not use `webstack_preserve_runtime_environment: true` for this
configuration change.

When disabled, the deployment Compose file omits only the optional secondary-site
template and static-content mounts. Its runtime hostnames and certificate directory
are empty; no `secondary_domain`, secondary certificate or DNS entry is required.
Also omit the secondary lineage from the encrypted `ssl_domains` and controller
TLS inputs. Existing stored certificates are not deleted by this switch.

When enabled, canonical redirects cover only the explicitly declared public
website hosts. HTTP and HTTPS aliases return 301 directly to `public_site_origin`,
preserving the path and query. The canonical host serves the application without
a loop. HTTPS aliases still need a valid certificate, so include every public
alias in the public certificate SANs and `ssl_domains[].flags`. Staff keeps its
own hostname/certificate and VPN-only access; the alias redirect does not apply
to staff, identity or VPN endpoints. Unknown hosts remain refused.

Identity and VPN ingress blocks declare their fixed monitoring service labels
explicitly. Their request metrics do not depend on hostname prefixes.

The defaults preserve existing deployments: secondary hosting enabled and
canonical redirects disabled. To enable secondary hosting again, supply its
explicit hostname/certificate inputs and rerun normal provisioning at the chosen
source/image refs. Roll back the public redirect with the boolean switch and a
normal runtime render. Never assign private service hostnames to the public list.

Record native HTTP/HTTPS redirect, canonical host, absent secondary host, public
staff denial and unknown-host checks before release. Verify DNS and certificate
coverage against the deployed configuration.

## Content cutover

1. Review the initial database/media copy without editing the replacement.
   Stop if the destination has new records or edits before final sync.
2. During an approved window, stop source writers and capture the final pair.
   Preserve destination authentication/offboarding tables; never rerun an importer
   requiring an empty destination over populated storage.
3. Compare business-table/media hashes. A no-op final sync is valid only when the
   frozen source and destination both match the reviewed snapshot. Otherwise
   reconcile differences and verify the updated copy.
4. Collect a fresh recovery point after any actual data change. Verify the frozen
   destination again, then apply only the reviewed DNS change, preserving proxy/TTL.
5. Check content/media/routes/redirects and public/private boundaries. Observe for
   30 minutes and record owner acceptance, DNS values and temporary-access cleanup.

Keep the source stopped and retain its encrypted copies. Reconcile replacement
writes before rolling traffic back; never reopen both writers blindly. The
[November 12 evidence](evidence/website-data-migration-2026-11-12.json) records the
completed cutover and preserved verification/cleanup timestamps.
