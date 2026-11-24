# Nginx source and deployment record — 2026-11-24

This records release metadata reviewed on November 24. It is not a fresh
production inspection or a new deployment acceptance report.

- The v0.2.2 application maintenance release contains the digest-pinned Nginx
  1.30.7 security update. Its source commit is
  `379e5e32d082de07d7d8f85821736487a9b26a70`.
- That release's notes record a production **Nginx image update on November 22**,
  preserving application `9facf29`, its container and the other containers. The
  full tagged maintenance application source was not deployed by that operation.
- The forward-port into application `main` subsequently merged at
  `2d913796a083eaa776e6d9138dea17c3db3301c4` and was source-versioned as v0.3.0.
  This minor release also contains application features beyond the maintenance
  baseline; it is not a security-only replacement for production.
- The merged tree matched tested head `b234f61335aa810dce7e8327aa5cd776956b1576`.
  Application and image CI passed on that head. Fresh disposable Nginx tests
  passed routing, TLS, staff/unknown-host denial and backend startup/replacement
  without ingress restart or reload.

The common image pin is
`nginx:1.30.7-alpine@sha256:caf4e1e032a20f9c3c5e28fc7f25ffd768ad8677b4975c54193075997b3a97e3`.
HOU1's drill/validation fixtures and the local HOU4 inventory use the same pin.
Those repository changes do not establish which image a running host uses.

Tag publication performed no new application-image publication or production
deployment. Existing v0.2.2, retained images and recovery-point bindings were
preserved. Select matching source, image receipt and infrastructure before a
later deployment; see [release selection](../releases.md).

Protected release metadata, local test logs and tag verification are retained
in the controller's private `application-nginx-release-20261124` state directory.
