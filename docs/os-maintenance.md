# OS maintenance

Routine reconciliation installs required packages and refreshes apt metadata.
Run installed-package upgrades separately from the repository root:

```bash
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"
ansible-playbook -i /private/inventory.yml ansible/playbooks/os-maintenance.yml \
  -e target_hosts=hou1_prod -e @/private/host-inputs.yml
```

Use the normal pinned SSH configuration and environment inputs. The playbook
requires an initialized host reached at its declared NetBird address. Package
upgrades can restart services, so run them in the intended maintenance window.

`os_maintenance_upgrade` accepts `safe` and `dist` (the default).
Apt metadata is cached for 3600 seconds by default.
The role reports whether `/var/run/reboot-required` exists. Schedule any reboot
separately and verify VPN, services and monitoring afterward.
