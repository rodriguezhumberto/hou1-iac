# Shared ingress resources

Owns the external ingress networks, `nginx-include.conf` and the ingress
configuration directory under `netbird_control_plane_directory`. Webstack,
NetBird and Authentik prepare these resources independently of backend startup.

Inputs are shared NetBird network/subnet/range variables, the ingress root and
the optional active Authentik network. Authentik supplies its isolated network
through `shared_ingress_additional_authentik_networks` when preparing storage.
Networks are retained when attachments are disabled; this avoids deleting
resources that another Compose project still uses.

Authentik publication requires `authentik_enable_ingress_network: true` and its
environment network to match `authentik_active_network`. Its ingress phase
reconciles Nginx's attachment before publishing the vhost. Certificates remain
explicit prerequisites; this role does not issue them or start backends.

## Compose selection

Role defaults declare `shared_ingress_compose_directory` (default
`app_directory`), `shared_ingress_compose_project` and
`shared_ingress_compose_file`; inventory may override them. Empty project/file
values retain Compose discovery.
`tasks/selector.yml` validates an absolute directory, optional project name and
optional YAML basename, then builds fixed Compose arguments. Authentik, NetBird,
TLS reloads and Nginx metrics use this selection. Older TLS receiver files without
`ingress_compose` retain their application-project selection.

This prepares consumer interfaces; the default does not move Nginx or generate a
separate project. Application routing still belongs to the selected source.

`shared_ingress_independent: true` enables the migration slice. It renders a
separate project under `shared_ingress_directory`, attaches it to the external
backend network, installs a strict recovery validator and writes `recovery.json`.
Webstack cuts over the listener only after the application project is ready; a
failed independent activation starts the legacy listener again. The default
remains combined.
