# Probes and drills

Commands run from the repository root. Executable programs remain in `scripts/`
and their owning Ansible roles; this folder holds their procedures.
An isolated HOU1/HOU4 lab is available; see its
[deployment evidence](../evidence/isolated-lab-2026-11-17.md). These procedures
still contain older fixture aliases; check their targets before use. See
[environment targeting](../development.md#environment-targeting).
Direct-SSH tools require [explicit destinations and host-key pins](rehearsal-targets.md).

| Procedure | Scope |
| --- | --- |
| [Backup transfer interruption](backup-transfer.md) | Guarded live transport failure, automatic retry and offline verification |
| [Synthetic staff login](staff-login-probe.md) | Optional environment-bound monitoring deployed through HOU1 and HOU4 |
| [NetBird compatibility spike](netbird-oidc-spike.md) | Isolated external-provider checks and manual protocol acceptance |
| [Live OIDC security suite](live-security-tests.md) | Mutating tests bound to the fixed isolated VPS fixture; never production |
| [Component recovery drill](reproducible-recovery-drill.md) | Restore into separate containers on an isolated test host |
| [Clean test-host recovery](clean-host-recovery.md) | Replace HOU1's test OS; retain HOU4 and its backups |

For production replacement use [production recovery](../production-host-recovery.md).
Record results in [evidence](../evidence/README.md), including failures and cleanup.
