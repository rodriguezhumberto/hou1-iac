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
- Four admission regression tests pass. The complete repository suite runs 100
  tests: 99 pass and one DNS resolver integration check is skipped because the
  controller has no `dnsmasq` binary. Ansible syntax and Python compilation pass.
- Account settings readback matches the prior settings with only
  `extra.user_approval_required` changed to false. The existing subnet, peer
  settings, regular-user restrictions and local recovery login remain intact.

The private admission input and human email addresses remain outside Git. The
NetBird PAT remains SOPS/age encrypted on the controller; Authentik's existing
bootstrap credential is used inside its running test container. No production
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

Private evidence includes the full test log, admission input, and
`netbird-first-login-report.json` with successful first-login and cleanup
assertions. Interactive Google login after reconciliation still needs the
operator's browser session.
