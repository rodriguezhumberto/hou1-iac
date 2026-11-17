# Component recovery drill — 2026-11-10

Historical execution notes retained from the [component drill guide](../probes/reproducible-recovery-drill.md).
Results apply to the revisions and fixtures recorded below.

The private baseline harness passed on 2026-11-09: PostgreSQL 187 tables, six
identity-material assertions and 29 application/session/media/restart/rollback
checks. After cleanup, all 23 primary health checks and eight monitoring targets
passed; primary container IDs and start times were unchanged. That evidence
motivated this versioned workflow. It is not a live pass of this refactored role.

Local regression tests use generated certificates, synthetic databases and
Ansible tasks. They cover shared certificate selection, preserved app runtime,
database readiness, writer-stopped session cleanup, first-error refusal and
controller cleanup after remote failure. The live result below covers this
entry point only. Later clean-OS, human-login and cutover results are linked from
the [production runbook](../production-oidc-runbook.md); historical remaining gates
below must not be read as current rollout status.

The first Ansible run stopped at the NetBird discovery issuer assertion: the
saved endpoint retained `:443`, while discovery omits the default HTTPS port.
Preparation now validates saved URLs before canonicalizing service origins;
the saved staff issuer and exact issuer checks are preserved. Eleven focused
regressions and the recovery playbook syntax check pass. Cleanup preserved all
primary container IDs/start times; subsequent health checks passed 23/23 with
eight monitoring targets up and no active alerts. No host wipe was needed. See the
[failed-attempt evidence](hou1-recovery-ansible-2026-11-10.json).

The operator-authorized corrected attempt passed at infrastructure revision
`95ca59eeb78d365001c407aa8736ffe4512b9c27`: Ansible completed 26 tasks with no
failures, importing 187 PostgreSQL tables and passing six identity assertions
and all 29 application/session/media/restart/rollback checks. Cleanup removed
the drill containers, network and plaintext; primary container IDs/start times
were unchanged. Post-test checks passed 23/23 with eight monitoring targets up
and no active alerts. The temporary recovery identity was retired after the
checks; the operator's encrypted archive remains intact. See the
[successful-attempt evidence](hou1-recovery-ansible-2026-11-10-02.json).
