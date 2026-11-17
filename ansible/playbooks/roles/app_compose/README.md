# Application Compose integration

Owns `{{ app_directory }}/docker-compose.override.yml`. Both the CMS and NetBird
entry points call this role, so role order does not affect the result.

Inputs are the shared CMS media path, NetBird ingress paths/network/address,
`authentik_enable_ingress_network`, `authentik_active_network` and
`app_compose_extensions` (default `{}`). Extensions add custom Compose settings;
they cannot replace managed mounts or network fields.

Before migrating a legacy managed override, declare its custom settings in
`app_compose_extensions`. Unknown additions and application/operator-owned files
are refused before writing. Generated content carries a fingerprint so later
input removals converge while manual additions still require migration.

Runtime paths, ordinary Compose commands and backup formats stay compatible.
