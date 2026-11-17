# Compose activation

Installs `/usr/local/libexec/hou1-compose-apply`. Callers supply the existing
project directory, optional project/file/service selection and Nginx validation.

Ordinary Compose reconciliation repairs missing or stopped services. Changed
service definitions, networks and read-only file inputs recreate their existing
consumers before waiting for health. This lets repaired inputs replace an
unhealthy old container. Writable data and directory contents
are excluded; ingress and certificate owners validate and reload those changes.
Nginx templates require recreation because its entrypoint renders them at startup.

Private state in each runtime directory advances after successful activation and
validation. Interrupted or failed activation retries on the next run. Existing
projects without state adopt their static inputs once. No volumes are removed.
