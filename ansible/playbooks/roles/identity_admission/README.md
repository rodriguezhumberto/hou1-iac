# Base-user admission

`identity_admission_secret_source` selects an environment-bound SOPS manifest for
the existing admission command. Empty disables this role. Profiles, receipts and
optional user units are controller-owned; plaintext credentials never reach HOU1.

The manifest declares approved Google accounts and explicit `staff_users` roles.
Application `role` defaults do not imply network administration. Optional
`vpn_group` (`staff` or `administrators`) and `netbird_role` (`user` or `admin`)
must explicitly authorize elevated VPN/dashboard access. `provision_users: true`
prepares missing Authentik identities without passwords or invitations.

NetBird creates external users on first login. Enable
`identity_admission_schedule_enabled` to reconcile their declared permissions on
the next one-minute timer run, without operator intervention. The command uses
`identity_admission_transport_argv`, the shared controller
`identity_admission_coordination_lock` and HOU1's capture lock. A busy workflow is
skipped; failures retry at the next timer event. Existing unrelated memberships,
the local NetBird owner and other environments are preserved.
Unchanged policies, account settings and assignments are verified without writes.
Scheduling requires a Linux controller with user-manager lingering enabled.
Disabling scheduling or removing its private input stops this environment's timer
and outstanding job. The managed workflow supplies its existing capture admission;
standalone deployment and scheduled runs acquire the host lock themselves.
