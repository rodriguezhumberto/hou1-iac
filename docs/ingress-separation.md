# Independent ingress migration

Separate shared Nginx from the application Compose project so application
activation and shutdown cannot stop the VPN control-plane proxy. Keep existing
certificate, log and backend ingress paths stable.

## Prerequisites

- Give TLS renewal, Authentik, NetBird and monitoring an explicit ingress
  directory/project/file selector. Defaults retain the current application project.
- Publish an immutable application release whose Nginx template resolves its
  backend through Docker DNS at runtime. Remove Nginx's application-health startup
  dependency. Test absent, late and replaced backends without reloading ingress.
- Preserve cold recovery. Current recovery expects Nginx, the application and
  Uptime Kuma in one saved project, with the existing network and port contracts.
  A split runtime must export a compatible combined recovery configuration before
  it can replace that layout. Keep the recovery validator strict.

The selector is a preparation step; selecting another directory alone does not
migrate containers, routing, boot recovery or backups.

## Implementation and acceptance order

1. Freeze the application release receipt, both infrastructure revisions and
   private inputs. Verify input contracts and a fresh encrypted recovery point.
2. Give shared ingress its own Compose project and Docker-only boot unit. Own its
   backend networks explicitly and allow startup before application enrollment or
   readiness. Preserve the environment's existing network isolation.
3. Prepare the new configuration and validate it without claiming the active
   listener ports or fixed proxy address. Test compatible backup export and cold
   recovery locally before enabling migration.
4. Record the old files and selectors, and arm a VM-local rollback timer. Run the
   listener transfer locally on HOU1 so losing controller SSH cannot interrupt it.
   Rollback must restore the previous container, configuration and selectors.
5. Verify fresh VPN SSH, TLS renewal, identity/control-plane endpoints, application
   routing and monitoring. Only then disarm rollback. Keep independent Proxmox
   console or guest-agent access available throughout.
6. Repeat unchanged inputs; unrelated container IDs must remain stable. Stop and
   replace the application backend, including a changed address, and verify
   automatic proxy recovery while ingress and administrative access stay up.
7. Rehearse interruption and rollback, then deploy frozen inputs to clean lab
   hosts. Record fresh-host results separately from adoption of existing hosts.

Shared ingress remains a single service: stopping it still interrupts the VPN
control-plane endpoint. Keep ingress-stop drills separate from application
failure checks and retain their VM-local recovery timer.
