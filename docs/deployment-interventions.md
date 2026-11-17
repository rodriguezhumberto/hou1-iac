# Deployment intervention audit

Scope: the [2026-11-17 lab run](evidence/isolated-lab-2026-11-17.md), repository
code and private helper source. No credentials were decrypted for this audit.
The run proved repaired deployment and reboot recovery; it did not prove a fresh
install from the final revisions without corrective commands.

| Intervention | Current owner/status | Required follow-up |
| --- | --- | --- |
| Missing ACME challenge directory | Fixed in webstack, `35b6cad` | Repeat fresh-host acceptance |
| TLS import blocked bootstrap retry or service repair | Fixed in controller TLS/webstack, `35b6cad` | Retain strict rotation checks; repeat fresh install |
| Uptime Kuma bound an absent VPN address at boot | Fixed in activation/webstack, `f553c61`; reboot passed | Include delayed-address regression in acceptance |
| Inter-VM TCP resets | Proxmox Terraform, corrected by local provisioning agent | Declare/check network prerequisites before bootstrap |
| Bootstrap name resolution | Opt-in `lab_dns` roles in both repositories; adoption and repeat passed | Verify fresh-host bootstrap; preserve provider DNS and unrelated host settings |
| Container DNS blocked after UFW enabled | HOU1 `docker` owns daemon DNS; `lab_dns_containers` derives narrow UFW allowances from declared networks | Include replacement-network and interrupted-start checks in fresh-host acceptance |
| Controller administrative VPN and resolver namespace | `scripts/lab-network.py` owns command transport and private resolver/hosts views; enrollment remains private | Maintain peer creation/enrollment separately from renewal jobs |
| Inventory assembly and bootstrap/managed selection | `scripts/prepare-lab.py` owns generated profiles and verifies fresh managed SSH before selecting them | Retain reviewed overrides; test initial cutover on fresh hosts |
| Lab secrets and age identities | Private preparation/import helpers; scoped encrypted bundles | Maintain retry-safe creation and recipient validation; never recreate identities on rerun |
| Recovered DNS/registry capabilities and ACME contact | Private encrypted inputs; version 2 fingerprints known and declared capability files | Validate provider/registry authorization before bootstrap |
| Application digest and matching source bundle | Version 2 derives selection from the CI receipt and verifies a standalone bundle/ref before host access; runtime artifact checks remain | Maintain release-input generation from this selection |
| Application role claims differed from lab IdP groups | Version 2 compares selected source groups, encrypted identity names, OIDC inputs and user grants | Maintain identity provisioning and optional test seeding separately |
| Empty `prod-servers` group required in lab account | Private input override for current policy planner | Make required policy groups explicit; the name does not authorize production peers |
| Synthetic passwords applied after identity provisioning | Private host-local Authentik adapter | Maintain an optional test-identity seed path; keep credentials out of arguments |
| Authentik backup self-check needed host decryption | Version 2 proves the legacy recipient/self-check key pair; unified mode checks endpoint/key bindings; owner-encrypted recovery copy retained | Verify installed identities, offline recovery and actual backup collection |
| Google callback not verified | External OAuth registration; login/branding disabled | Verify callback before enabling; preserve the approved allowlist |
| Nginx-stop drill removed administrative VPN access | Console recovery; failed drill preserved | Require independent recovery access and a VM-local restart timer for ingress drills |
| Runtime snapshots, login checks and reboot receipts | Workflow owns container/source/TLS/HTTPS checks; login and reboot wrappers remain private | Maintain the remaining acceptance tools with declared targets and distinct failure records |
| Playbook invocation and logs | `scripts/deploy-lab.py`; existing-host reconciliation and verification passed | Extend only after bootstrap prerequisites have owners |

## Ownership decisions

- Keep Proxmox rules in the provisioning project. HOU1/HOU4 declare and check the
  required paths: HOU4 to HOU1 TCP 443 and bidirectional UDP 3478/51820/51821.
  Successful HTTPS does not establish UDP transport or enrollment permissions.
- Keep encrypted capabilities scoped by consumer. Consolidate their references
  and validation, not their decryption rights.
- Replace lab-specific helper behavior with roles or maintained controller tools;
  copying the private scripts unchanged would retain hardcoded paths and identities.
- Preserve explicit SSH cutover verification and console access. A deployment
  checkpoint cannot establish that private access still works.
- Keep existing playbooks as resource owners. The workflow coordinates them and
  records results; it does not become a second configuration renderer.

## Implementation order

1. Managed-host target/access preflight, ordered reconciliation and runtime checks.
2. Declarative lab DNS, container DNS firewall rules and controller transport.
3. Input/release assembly, identity/backup contract checks and bootstrap handoffs.
4. Independent shared ingress, with explicit migration and rollback coverage.
5. Fresh-host deployment from frozen inputs, then interrupted/dependency-failure
   acceptance. Run destructive drills separately from routine reconciliation.

The [workflow](deployment-workflow.md) and [lab DNS roles](lab-dns.md) implement
the first two slices for existing hosts and an enrolled controller peer. Resolver
adoption, repeat reconciliation and standalone verification passed without manual
repair. Bootstrap handoffs, peer enrollment, identity seeding, off-host backup and
recovery acceptance remain separate work.
Version 2 adds [controller-only input contracts](lab-inputs.md), including checks
that stop before host access. [Inventory generation](lab-inventory.md) references
repository defaults and explicit private overrides. Its handoff checks the live
VPN-only SSH policy before selecting generated managed profiles. Initial VM
bootstrap and peer enrollment remain separate from this managed-host workflow.

The [ingress migration sequence](ingress-separation.md) preserves application
routing, TLS consumers and the existing cold-recovery contract before cutover.

Initial workflow validation: 23 workflow/transport tests passed, including real
Ansible/OpenSSH fixtures, plus syntax/lint and documentation links. Managed-host
preflight, reconciliation and standalone verification passed on both lab hosts;
the initial delegation failure and corrected retry are in the
[lab evidence](evidence/isolated-lab-2026-11-17.md#managed-workflow-acceptance).
DNS adoption and repeat results are recorded in the
[same evidence](evidence/isolated-lab-2026-11-17.md#maintained-dns-adoption-and-repeat).
