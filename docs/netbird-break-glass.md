# NetBird and Authentik break-glass recovery

The break-glass command is installed at
`/opt/hou1-netbird/break-glass.sh`. It is run by an approved operator from the
host console or an existing NetBird SSH session:

```bash
export BREAK_GLASS_OPERATOR=alice
export BREAK_GLASS_REASON='Authentik outage during migration'
export NETBIRD_API_PAT='provided from the encrypted controller vault'
export NETBIRD_HOST=prod_hou1
/opt/hou1-netbird/break-glass.sh
```

The command records only operator, reason, timestamps, pass/fail state, and
rollback evidence. It never writes the PAT to the evidence file. It first tests
the NetBird API with the PAT, then tests NetBird SSH independently, and finally
restores the embedded NetBird IdP from the verified cutover snapshot when the
rollback script is present. API PAT access authorizes API operations only; it
does not create a dashboard login or bypass Authentik group authorization.

If NetBird SSH is unavailable, use the VPS provider's host/serial console as
the second recovery path, authenticate as root, and run the same rollback
script. Keep the node's SQLite state, encryption keys, peers, policies,
addresses, and PAT untouched. Do not re-enroll peers or generate new server
keys.

Approved operators are recorded in `/var/log/hou1-netbird/operators` with a
15-minute escalation target. The operator records the outage start, API PAT
result, SSH/console result, embedded restore result, end time, and follow-up
owner in the issue-linked recovery evidence. Rotate the PAT and close the
incident after recovery.
