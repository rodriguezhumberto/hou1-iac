# Webstack

Deploys the verified application image and existing Nginx ingress through
`hou1.yml` and `bootstrap-control-plane.yml`.

## Task phases

`main.yml` statically imports these phases so role tags and ordering carry through:

1. `application.yml`: verify the artifact, select its Git revision, validate
   website routing and configure the image in the existing Compose file.
2. `storage.yml`: retain the database mount, prepare CMS media and install backup
   and restore commands, then optionally seed missing media.
3. `runtime.yml`: preserve a validated existing `.env` for image-only updates or
   render `app.env.j2` as mode `0600`, with secret logging suppressed.
4. `tls.yml`: import controller certificates or retain host DNS-01 issuance and
   renewal according to the existing TLS mode guards.
5. `deploy.yml`: validate Compose, archive retired Directus data, start the
   application, record its artifact and recreate/validate Nginx before cleanup.

## Inputs and ownership

Inputs come from `ansible/inventory/group_vars/all/vars.yml`, environment
inventory/SOPS bundles and the selected application release. Required inputs
include `app_directory`, `app_repo_pat`, immutable `application_image_reference`
and `application_image_revision` matching `deploy_ref`, registry credential
sources, website/staff domains and routing, VPN addresses, hCaptcha/VAPID/staff
secrets, CMS environment/storage settings, and certificate/TLS inputs.
Optional `staff_oidc_*` inputs add OIDC settings to the runtime file.

`runtime.yml` alone owns application `.env` generation. This role also manages
the application checkout and Compose image/media adjustments, CMS directories,
backup/restore commands and legacy backup cron, Certbot resources, the artifact
receipt and project-scoped Directus retirement archive. Existing nested task
files retain their Compose transformations; ingress integration remains shared
with the existing NetBird role. Paths and recovery formats are unchanged.

## Prerequisites

Run the existing environment guards first. The controller needs the declared
Ansible collections and scoped registry/TLS secret access. The host needs Docker
Compose, a compatible application checkout, and the selected certificate source.
Preserve-runtime mode requires a regular, non-symlink `.env`. Public bootstrap
starts only Nginx and its dependencies until NetBird enrollment completes.
