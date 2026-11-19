# Backup source

Owns HOU1's restricted encrypted export, source configuration, capture timer and
interrupted-writer recovery. Use the public `ansible/playbooks/backups.yml` with
an established VPN inventory. Required inputs and recovery boundaries are in the
[backup protocol](../../../../docs/hou4-backup-protocol.md).

- `files/export.py` captures locally and atomically publishes `ready.export`.
- `files/operation_lock.py` owns the shared capture/deployment lock protocol.
  It is installed before the exporter that imports it. The managed controller
  sends this same source over pinned SSH to hold admission across both hosts.
- `files/serve.py` reads completed ciphertext without stopping services.
- `tasks/source-layout.yml` maintains the exporter and lock helper during normal
  application deployment; it does not request a capture.
- `tasks/recovery.yml` installs systemd units and seeds an initial recovery point
  only when no earlier capture succeeded.

The controller sends ten-second keepalives; a minute of silence releases an
abandoned hold without waiting for the operating system's TCP timeout.
Capture waits at most fifteen minutes for the host lock. Recovery stays
nonblocking; deployment refuses outstanding writer intent. Keep the lock inode,
installed paths, export format and offline-key boundary stable.
