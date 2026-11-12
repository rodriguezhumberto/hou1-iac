# NetBird and Authentik emergency access

Keep independent provider host/serial console or rescue access and the local
emergency account verified. SSH password restrictions do not disable console
authentication. A PAT does not create a dashboard login, grant staff roles or
repair transport; it permits API operations only.

## Choose the working access path

If VPN SSH works, use an approved administrative key to inspect the incident and
repair the affected service. If it does not, use provider access; never depend
on the broken control plane to reconnect its own recovery path. Preserve SQLite,
keys, peers, policies and current data before making changes.

The installed `/opt/hou1-netbird/break-glass.sh` is a **legacy helper**, not a
universal console repair command. It requires `BREAK_GLASS_OPERATOR`,
`BREAK_GLASS_REASON`, `NETBIRD_HOST` and a securely loaded `NETBIRD_API_PAT`.
It tests the API and outbound SSH first; if either fails, it stops before rollback.
Its rollback call selects the newest snapshot without an explicit path.

Do not use that helper to bypass a VPN outage or assume it restores the current
external-provider contract. For an approved configuration rollback, inspect the
exact snapshot and invoke `authentik-rollback.sh <reviewed-snapshot-path>` directly
through the working administrative path. It restores config/dashboard files only;
it does not restore the database or remove external-provider registrations. See
[legacy cutover limitations](netbird-authentik-cutover.md). For host loss, follow
[production replacement recovery](production-host-recovery.md).

## Audit and close

Record operator, reason, UTC start/end, snapshot, API/SSH/console results and
follow-up owner without credentials. Approved operators are recorded under
`/var/log/hou1-netbird/operators`, with a 15-minute escalation target. Preserve
first errors, verify identity/peer access after repair, and rotate used emergency
credentials/PATs. Do not regenerate server identities or re-enroll all devices
as an ordinary identity-service rollback.
