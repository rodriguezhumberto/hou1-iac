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
