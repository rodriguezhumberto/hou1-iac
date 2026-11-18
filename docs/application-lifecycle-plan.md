# Application lifecycle work

Make application health and recovery portable between Compose and Kubernetes.
Keep orchestration-specific behavior in deployment configuration.

## Contract

- Preserve `/api/health.json` as a basic process/HTTP check. It must remain
  reachable when local storage is unavailable.
- Add `/api/ready.json`: return 200 when startup is complete and required local
  database/media storage is usable; otherwise return 503. Responses disclose no
  paths, credentials, identity settings or raw errors and are never cached.
- Identity, CAPTCHA and notification providers are feature dependencies. Their
  outages must not make the whole application fail readiness.
- Retry unavailable startup storage with bounded delays. Avoid duplicate schema
  initialization and background jobs after recovery.
- On termination, stop admitting work, report unready, drain existing requests
  within a fixed deadline, then close local resources.

## Work order

1. Extract startup/storage ownership from middleware into a small runtime module.
   Keep authentication, data paths, schema and request behavior compatible.
2. Implement liveness/readiness and storage recovery. Test failure, recovery,
   secret-free responses and independence from external providers.
3. Check the framework's shutdown behavior, add only missing lifecycle handling
   and test actual HTTP draining under termination.
4. Select readiness in source Compose configuration. Teach maintained deployment
   checks to verify it for releases declaring this contract; preserve older
   release and backup compatibility.
5. Run unit, built-application and release-image fixtures. Review integrated code
   and verify the lab using frozen, matching image/source inputs where available.

## Current implementation

Application PR #69 merged this contract as `463a28b`; its trusted release is
deployed to the isolated lab. The application's `docs/application-lifecycle.md`
maps runtime ownership and supported launch.
Deployment verification validates declared capabilities and checks readiness;
older releases retain their existing checks. See the
[validation record](evidence/application-lifecycle-2026-11-17.md).

For a declared runtime contract, the maintained deployment probe also checks
the resolved Compose readiness healthcheck and stop grace before probing each
running application container. A release without the declaration keeps the
legacy container checks.

## Deployment boundary

Test-host deployment is authorized for this work. Keep production, unrelated
working-tree edits and historical evidence intact. New application source still
needs a matching trusted CI image/receipt before managed-host deployment; local
images must not be relabeled as CI artifacts.

## Kubernetes mapping

Use the basic endpoint for liveness and readiness for traffic admission. A future
startup probe can give initialization a longer budget before liveness enforcement.
Termination handling belongs to the application; probe intervals, grace periods
and traffic routing belong to the deployment platform. Health checks do not
provide automatic repair or uninterrupted availability of a single instance.
