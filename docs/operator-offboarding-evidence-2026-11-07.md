# Isolated offboarding evidence — 2026-11-07

Scope: hou1-iac #47 and pearland-paws #51. This rehearsal used the existing
isolated VPS, private pinned SSH/sudo, test domains, clients and SOPS/age files.
No production promotion, real-user offboarding, firewall changes, NetBird
upgrade, or signing-key changes were performed.

The deployed staff implementation is commit
`2f133f32aae5656a4dea1b2649b9af166ff16aa9`, image
`sha256:949975ce328b1482867cb522f8db349a32cfb030c27a7bdcc6fcffcc23ad4990`.
It was built on the controller and transferred over private management access;
the runtime egress restrictions stayed in place. Only the test app was
recreated, with `STAFF_ENVIRONMENT=test`. A recovery image was retained after
its application bundle was compared to the running pre-change app. Authentik
remained 2025.10.4 and NetBird remained 0.82.0.

## Live assertions

| Check | Observed result |
| --- | --- |
| Dry run | Reports the immutable target, test environment and server-derived SSH/sudo actor; no access mutations. |
| Existing cookies | Two independently authenticated sessions returned HTTP 200 before blocking and HTTP 401 afterward. |
| In-flight callback | A fresh code exchanged successfully, evidenced by a new IdP token record, but persistent staff blocking prevented session creation; protected API returned 401. IdP was still active for this test. |
| Full offboarding | All seven coordinator steps passed; the target was inactive with no Authentik groups, blocked in staff and NetBird, and absent from encrypted admission state. |
| Retained NetBird token | Existing bearer token returned HTTP 403 after blocking. |
| Owned peer | A disposable SSO peer reached the VPN-only staff API before offboarding (401 without an app cookie). Exactly one owned peer was removed; later TCP access failed. |
| Control identities | Unrelated reader cookie stayed HTTP 200, reader writes were denied, and the real management peer plus VPS management peer remained present and connected. |
| Audit fault | Making the audit file fail its required permission check caused nonzero `unsafe_audit_file` before access changes. The admission ciphertext was unchanged and the second target remained active with its groups. Audit permissions were restored. |
| App outage | Stopping only the test app produced a nonzero partial result. Admission removal, IdP disable/revocation, and NetBird denial checks succeeded independently; staff blocking and final verification failed explicitly. |
| Outage recovery | Starting the app and retrying the same operation completed, revoked the retained cookie (401), and preserved the control session (200). |
| Retry | Repeating the first completed operation returned success with zero new admission removals and zero peer removals, without re-enabling access. |
| Reconciliation | The existing encrypted admission reconciler passed its live policy checks. Decrypted remaining desired state exactly matched the pre-fixture document, and both disabled targets still failed fresh IdP login. |
| CLI guards | Nonroot worker/container execution, forged actor input, wrong environment/issuer, email-shaped subject, and missing database were refused. |
| Durable audit | Both operations have root-owned 0700 directories, 0600 regular files, server-derived operator identity, linked app audit records, and durable partial/complete outcomes. No human emails or credentials occur in the operator audit. |

The IdP's disable operation removed its live grants/sessions; the explicit
revocation step verified no owned records remained. Ownership-based deletion,
pagination, unknown-owner rejection and readback are also tested independently
with fixtures. No real Google account was disabled: the live targets were
synthetic internal test identities, while Google-source eligibility and
immutable subject mapping have unit coverage.

## Validation and handoff

- Infrastructure regression suite passed using the CI Nginx container wrapper;
  the optional dnsmasq integration case was skipped because dnsmasq is absent.
- All 19 focused offboarding tests passed, including newly protected identities
  on cached-plan retries, independent outages, audit failure, and real SOPS/age
  encryption/readback with recipient preservation.
- All four existing Ansible playbook syntax checks passed.
- Staff: 85 tests, build, and all 9 integration cases passed.
- Transient browser/token artifacts and the disposable peer container were
  removed. Two inactive synthetic identities and their persistent subject blocks
  remain for audit linkage; their rehearsal passwords were discarded.
- Monitoring was restored: all 33 rules healthy, no firing alerts or pending
  deliveries. Private management access remained available.

Private raw reports and encrypted fixtures stay outside Git. The test app uses
an explicit rehearsal Compose override; deploying from the merged release is a
separate step. Merge the staff implementation and the existing encrypted
admission prerequisite (PR #46) before enabling this coordinator through the
normal rollout. Rebase and rerun repository checks after prerequisite merges.
Signing-key overlap remains a separate gate.
