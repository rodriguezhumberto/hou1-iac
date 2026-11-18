# Independent ingress migration

Separate shared Nginx from the application Compose project so application
activation and shutdown cannot stop the VPN control-plane proxy. Keep existing
certificate, log and backend ingress paths stable.

## Current preparation

TLS, identity/VPN ingress operations and monitoring share the validated
`shared_ingress_compose_directory`, `shared_ingress_compose_project` and
`shared_ingress_compose_file` inputs. The [owning role](../ansible/playbooks/roles/shared_ingress/README.md#compose-selection)
keeps compatible defaults for older inventories and TLS receiver configurations.

The application release provides dynamic backend discovery and readiness without
making Nginx wait for application startup. The infrastructure migration is
feature gated: `shared_ingress_independent: false` keeps the combined project;
`true` renders a separate Nginx project, backend network and recovery manifest.

## Prerequisites

- Give TLS renewal, Authentik, NetBird and monitoring an explicit ingress
  directory/project/file selector. Defaults retain the current application project.
- Publish an immutable application release whose Nginx template resolves its
  backend through Docker DNS at runtime. Remove Nginx's application-health startup
  dependency. Test absent, late and replaced backends without reloading ingress.
- Preserve cold recovery. The split manifest records both Compose selectors,
  the backend network and exact stop/start rollback commands. The installed
  recovery validator rejects edits or incomplete selectors before activation.

The selector is a preparation step; selecting another directory alone does not
migrate containers, routing, boot recovery or backups.

## Implementation and acceptance order

1. Freeze the application release receipt, both infrastructure revisions and
   private inputs. Verify input contracts and a fresh encrypted recovery point.
2. Give shared ingress its own Compose project and Docker-only boot unit. Own its
   backend networks explicitly and allow startup before application enrollment or
   readiness. Preserve the environment's existing network isolation.
3. Validate the split Compose model and recovery manifest offline. During cutover,
   drain the legacy listener with Nginx's graceful `SIGQUIT` and 30-second stop
   grace, then start independent Nginx. Compose's healthcheck/`--wait` gate must
   pass before the cutover succeeds; restore the legacy listener automatically if
   activation fails. This does not change the application readiness endpoints.
4. Record the old selectors and arm a VM-local rollback timer before production
   use. Run the listener transfer locally on HOU1 so losing controller SSH cannot
   interrupt it.
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
