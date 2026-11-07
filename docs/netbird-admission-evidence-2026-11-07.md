# Isolated NetBird allowlist admission rehearsal — 2026-11-07

Issue: infrastructure #27. This records a test rollout finding and its resolution;
it is not production approval. NetBird remains on 0.82.0.

The existing account required manual approval after successful Authentik login.
The Google source had an exact email allowlist, but the NetBird application had
no separate application admission policy. An approved Google user therefore
reached NetBird and entered its pending-approval queue.

The controller reconciliation now applies these boundaries in order:

1. Both Google source flows require their verified-email allowlist policy in
   ALL mode. Only explicit email addresses are accepted. Boolean
   `email_verified` is preferred, with legacy `verified_email` compatibility.
2. The `netbird-test` application requires its own allowlist policy in ALL mode.
   It permits the listed Google-source users and explicitly named internal test
   fixtures. It grants no staff group, NetBird administrative role, or peer policy.
3. The live Authentik policy engine must pass positive and negative admission
   checks before NetBird's `extra.user_approval_required` setting is disabled.
4. Exact allowlisted users already pending on the test Authentik connector are
   activated once, retaining the regular `user` role. Unknown pending users are
   left untouched.
5. Explicit `staff_users` assignments supply the application role and the
   NetBird `staff` auto-group independently. An operator-selected staff-app
   Administrator retains NetBird's regular user role and receives no SSH/admin
   network group. Login admission alone does not grant application privileges.

Recorded checks:

- Live Authentik application policy: all five existing identities checked;
  the approved Google user and the named monitor fixture pass, other users fail.
- Live Google source policy: approved/verified OIDC and legacy claims pass;
  unapproved email, unverified email, and a string `"true"` fail.
- Approved monitor fixture completes Authentik → embedded NetBird OIDC login,
  dashboard callback, PKCE exchange and authenticated API access (200).
  Authorization-code replay is rejected.
- A first-time internal fixture, explicitly added to the test allowlist for the
  drill, completes login with no pending approval and retains the regular user
  role. Its temporary admission and NetBird account are removed afterward.
- An unlisted fixture receives Authentik's `Permission denied` page at the
  authorization endpoint, with no NetBird callback or tokens.
- Public user-creation API without credentials: 401. Public setup endpoint: 403.
- Reconciliation rerun: succeeds with zero pending users requiring activation;
  the application policy and binding are reused.
- Nine admission/access regression tests pass, including real SOPS/age round-trip,
  plaintext/wrong-key/tampered-input rejection, and external identity selection.
  The complete repository suite runs 105 tests: 104 pass and one DNS resolver
  integration check is skipped because the controller has no `dnsmasq` binary.
  Ansible syntax, Python compilation, and whitespace checks pass.
- The selected human account's Authentik membership is
  `pearland-paws-staff-admin`, with no Authentik superuser group. Testing the
  actual groups property mapping for that identity returns exactly
  `{'groups': ['pearland-paws-staff-admin']}`.
- Its NetBird user has the `staff` auto-group. Its existing connected device
  receives `staff`, with no `administrators` group. The user remains a regular
  NetBird `user`. Existing HTTPS and test DNS policies apply; SSH remains limited
  to the administrators network group.
- Both existing staff test users complete the real VPN-only staff login and
  protected read; the reader cannot write, callback replay fails, and logout
  revokes the prior session. The staff login outside the VPN remains 403.
  The staff network group receives the private DNS configuration.
- Account settings readback matches the prior settings with only
  `extra.user_approval_required` changed to false. The existing subnet, peer
  settings, regular-user restrictions and local recovery login remain intact.

The entire private admission input is now SOPS/age encrypted with the existing
controller identity, including the allowlisted emails and explicit staff-role
assignments. Decryption matches the original object in memory, and the emails
are absent from the ciphertext. The encrypted file has mode `0600` and remains
outside Git. Reconciliation from ciphertext passes all five application-policy
and five Google-policy checks, with one staff assignment configured, none waiting
for first login, and zero pending users requiring activation. Only after those
checks passed was the original plaintext source removed. The script decrypts in
memory without producing a plaintext file and has no plaintext fallback.

The NetBird PAT remains SOPS/age encrypted on the controller; Authentik's existing
bootstrap credential is used inside its running test container. Authentik retains
identity data and policy expressions in its runtime database. No production
configuration, credential, user, or service was modified.

See [the rehearsal procedure](rehearsal-monitoring-isolation.md) for the command,
configuration fields, queue rollback, and the distinction between removing an
allowlist entry and revoking existing sessions/peers.

Repository validation:

```bash
NGINX_BIN=/tmp/hou1-nginx-validator PYTHONPATH=tests \
  python3 -m unittest discover -s tests -v
ansible-playbook -i <private-test-inventory> \
  ansible/playbooks/rehearsal-gates.yml --syntax-check
```

Private evidence includes the full test log, encrypted admission input,
`encrypted-admission-report.json`, and
`netbird-first-login-report.json` with successful first-login and cleanup
assertions. Interactive Google login after reconciliation still needs the
operator's browser session.
