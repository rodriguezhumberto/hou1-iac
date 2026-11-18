# Webstack

Deploys the verified application image and existing Nginx ingress through
`hou1.yml` and `bootstrap-control-plane.yml`.

## Task phases

`main.yml` statically imports these phases so role tags and ordering carry through:

1. `application.yml`: verify the artifact, refresh a declared isolated source
   bundle through `test_application_source`, update the separate source checkout,
   validate website routing, install managed assets and render base Compose.
2. `storage.yml`: retain the database mount, prepare CMS media and install backup
   and restore commands, then optionally seed missing media.
3. `runtime.yml`: preserve a validated existing `.env` for image-only updates or
   render `app.env.j2` as mode `0600`, with secret logging suppressed.
4. `tls.yml`: import controller certificates or retain host DNS-01 issuance and
   renewal according to the existing TLS mode guards.
5. `deploy.yml`: prepare shared ingress, validate Compose, archive retired
   Directus data, apply changed service inputs, validate Nginx and record the
   artifact before cleanup. Failed activation retains inputs for retry.

`hou1-webstack.service` retries activation at boot once the declared VPN address
exists. Docker starts ingress independently so NetBird can reconnect first. The
unit has no stop action; stopping it does not remove shared ingress or VPN access.

When `shared_ingress_independent` is enabled, `shared_ingress` owns a separate
Compose project and `hou1-ingress.service`. The application project starts only
the backend and data services; the ingress project owns listener ports and its
network attachments.

## Inputs and ownership

Inputs come from `ansible/inventory/group_vars/all/vars.yml`, environment
inventory/SOPS bundles and the selected application release. Required inputs
include `app_directory`, `app_repo_pat`, immutable `application_image_reference`
and `application_image_revision` matching `deploy_ref`, registry credential
sources, website/staff domains and routing, VPN addresses, hCaptcha/VAPID/staff
secrets, CMS environment/storage settings, and certificate/TLS inputs.
Optional `staff_oidc_*` inputs add OIDC settings to the runtime file.

`runtime.yml` alone owns application `.env` generation. This role also manages
the separate `app_source_directory` checkout, runtime source-asset manifest,
regular ingress assets and base Compose image selection, CMS directories,
backup/restore commands and legacy backup cron, Certbot resources, the artifact
receipt and project-scoped Directus retirement archive. Existing nested task
files retain their public entry points. The `app_compose` role alone renders
the media/ingress override from declared inputs; `shared_ingress` prepares
external networks and mounts before application startup. Paths and recovery
formats are unchanged. See [source ownership](../../../../docs/application-images.md#source-and-runtime-ownership)
for migration and local-change guards.

## Prerequisites

Run the existing environment guards first. The controller needs the declared
Ansible collections and scoped registry/TLS secret access. The host needs Docker
Compose, access to the selected source revision, and the selected certificate source.
Preserve-runtime mode requires a regular, non-symlink `.env`. Public bootstrap
starts only Nginx and its dependencies until NetBird enrollment completes.
