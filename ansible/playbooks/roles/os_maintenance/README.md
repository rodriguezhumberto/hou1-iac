# OS maintenance

Explicit package upgrades for an initialized host reached through NetBird.
Routine provisioning installs required packages and refreshes apt metadata;
this role performs the separately requested upgrade.

`os_maintenance_upgrade` defaults to `dist`; select `safe` to retain packages
requiring dependency changes. `os_maintenance_cache_valid_time` defaults to 3600
seconds. The role reports `/var/run/reboot-required` and never reboots the host.
Use `ansible/playbooks/os-maintenance.yml` with the normal private inventory.
