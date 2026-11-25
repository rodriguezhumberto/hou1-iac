# SSH targets for rehearsal tools

The isolated lab's accepted gates are summarized in
[current status](../cleanup-plan.md). Select its private
targets and verified SSH keys explicitly. Old aliases do not establish identity.

`scripts/ssh_target.py` checks the resolved SSH address and ED25519 fingerprint
locally before a connection. Test servers must use `100.91.0.0/16`; auxiliary
hosts running probe containers may use their own explicit LAN address. Each
destination needs a non-root user, `StrictHostKeyChecking yes`, `IdentitiesOnly
yes` and one known-hosts file. Keep JSON, SSH config and known-hosts files owned
by the operator with mode `0600`. Verify fingerprints through the VM console;
`ssh-keyscan` alone does not verify identity.

Add these fields to private monitor/TLS configs. The addresses below are
examples; replace them and both fingerprints with verified values:

```json
{
  "environment": "test",
  "ssh": ["ssh", "-F", "/private/lab/ssh_config", "hou1-lab"],
  "ssh_host_ip": "100.91.0.10",
  "ssh_fingerprint": "SHA256:<verified HOU1 ED25519 fingerprint>",
  "peer": {
    "environment": "test",
    "ssh": ["ssh", "-F", "/private/lab/ssh_config", "probe-host"],
    "ssh_host_ip": "192.0.2.50",
    "ssh_fingerprint": "SHA256:<verified probe-host ED25519 fingerprint>"
  },
  "staff_probe_command": [
    "ssh", "-F", "/private/lab/ssh_config", "probe-host",
    "docker exec -i hou1-vps-admin-test python3 /tmp/live-staff-check.py"
  ]
}
```

This shows transport fields, not a complete monitor configuration. Preserve its
existing probe, secret-source and alert inputs. TLS-only configs need the
top-level transport fields; monitors and their alert/login drills also need
`peer` and a `staff_probe_command` using that exact peer SSH prefix.

- `prepare-rehearsal-host-secrets.py --controller-dir DIR` reads `DIR/targets.json`
  and requires its server address to match `DIR/management.yml`.
- `live_oidc/controller.py` reads `targets.json` beside its security workdir
  (normally `/var/tmp/hou1-vps-test/targets.json`). Its callers inherit checked
  server and peer transports. `HOU1_SECURITY_WORKDIR` changes the workdir.
- `reconnect-test-controller.py --config /private/lab/targets.json` uses `peer`.
  Optional `--ssh-host` must equal that peer's alias; it no longer selects an
  arbitrary destination.
- `install-rehearsal-monitor.py` checks both destinations and copies
  `ssh_target.py` with its installed monitor/TLS programs. Reinstall together
  when upgrading those programs.

The returned SSH commands fix the checked address, user, port and known-hosts
file, restrict host-key negotiation to ED25519 and disable connection reuse and
alternative host-key sources. Nonstandard ports and host-key aliases are
supported. Missing inputs or changed destinations stop before remote work.

The admission command also requires `ssh_host_ip`, `ssh_fingerprint` and the
four-item `ssh` list in its encrypted config. It selects `test` or `prod` from
the independently exported identity profile and requires the corresponding VPN
subnet. Existing profile, naming and token-origin checks still apply.

Before a new [live OIDC drill](live-security-tests.md), update fixture domains,
containers and paths for the selected lab.
