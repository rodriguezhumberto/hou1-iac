# Documentation review — 2026-11-12

Scope: repository Markdown, cross-checked against Ansible roles, templates and
accepted production evidence. No live node changes or new production tests were
performed. Dated evidence reports retain their original results and limitations.

## Corrected documentation

| Conflict | Correction |
| --- | --- |
| NetBird guide claimed established hosts had no console recovery; a test required that sentence. | Document independent provider console/rescue access separately from VPN-only, key-only SSH. Correct the existing assertion. |
| Release guide required `cms-rollout` and a source-only deployment/obsolete baseline rollback. | Use tested `main`, manual image publication, explicit digest/revision deployment and compatible-data rollback. |
| CMS/Authentik guides described 30-day component cron jobs as the active backup policy. | Unified backup disables those schedules; active HOU4 retention is seven daily and four weekly points. Label old formats/helpers as legacy. |
| Authentik backup guide claimed live PostgreSQL persistent state was copied. | Both implementations use logical SQL; live PGDATA is excluded. |
| Architecture described future gates, fixed organization names and OIDC endpoints as children of each issuer. | Describe current boundaries, encrypted identity names, configurable hosts and discovery-provided protocol URLs. |
| Architecture blurred Authentik's role in NetBird. | NetBird 0.82.0 retains its embedded issuer and registers Authentik as an external provider. |
| Request metrics guide used the old 2-second callback alert and a fixed test log path. | Match HOU4's 5-second p95 / 10-minute window / minimum 10 callbacks / 5-minute hold; derive the log from `app_directory`. |
| Monitoring/secrets/renewal guides still described completed production work as pending. | Link accepted production evidence and retain explicit execution limits. The old disabled timer is historical, not current renewal state. |
| Test recovery required a Node build lock even for CI-backed points. | Use `astro-app` for CI points and `node` only for legacy source-built points. |
| Break-glass guide implied its helper worked directly from a disconnected console. | State its prerequisite API/outbound-SSH checks and snapshot-selection limits. |
| Production runbook repeated migration chronology and marked already-merged work as prerequisites. | Link evidence, keep release/recovery stop points, and place the reusable content-freeze checklist in website routing. |
| Rehearsal journals mixed historical open gates with current operations. | Add context banners and a docs index; preserve original records. |

## Implementation discrepancies still present

- **Legacy direct-issuer cutover:**
  [the template](../ansible/playbooks/roles/netbird_control_plane/templates/config-authentik.yaml.j2)
  writes Authentik into `server.auth.issuer`, contrary to the
  [pinned compatibility contract](netbird-authentik-compatibility.md). The helper's
  compatibility flag/readiness probes do not establish that contract. It remains
  executable, but is removed from recommended procedures. A code change should
  retire it or replace it with guarded external-provider reconciliation.
- **Legacy emergency helper:**
  [break-glass.sh](../ansible/playbooks/roles/netbird_control_plane/templates/break-glass.sh.j2)
  stops when API/SSH is unavailable and invokes rollback without an explicit
  snapshot. [Rollback](../ansible/playbooks/roles/netbird_control_plane/templates/authentik-rollback.sh.j2)
  restores two configuration files only; it does not remove provider registrations.
  A code change is needed before presenting this as a general recovery command.
- **Permissive repository inventory:**
  [the checked-in SSH options](../ansible/inventory/inventory.yml) disable host-key
  checking. Current production instructions require a separate strict private
  inventory; this review did not change defaults or inspect live SSH settings.
- **Incomplete secret consolidation:** Authentik still decrypts on-host and legacy
  Vault inputs remain. [#45](https://github.com/rodriguezhumberto/hou1-iac/issues/45)
  tracks that work; this cleanup does not claim all decryption is controller-only.

## Outstanding validation

| Test | Follow-up |
| --- | --- |
| Restore the renewal controller on a separate clean host | [#77](https://github.com/rodriguezhumberto/hou1-iac/issues/77). Owner-local decryption of the recovery bundle passed. |
| Run production replacement recovery from start to finish without corrections | [#78](https://github.com/rodriguezhumberto/hou1-iac/issues/78). Include a service restore from the retained post-import point. |
| Interrupt a live HOU4 transfer and verify retry/commit behavior | Receiver process-kill/retry checks passed locally; schedule the live test separately. |
| Recover capture after a provider-level power cut | Writer SIGTERM/SIGKILL and normal reboot recovery passed; schedule the provider test separately. |
| Roll back between two published CI digests | The original migration used a pre-CI rollback image. Validate the chosen release pair and database compatibility. |
| Repeat Discord firing/resolution after replacement | Delivery was confirmed before replacement; collection and probe health passed afterward. Repeat delivery checks after receiver or routing changes. |

An offline OCI mirror is unimplemented. Independent HOU4 outage monitoring is
deliberately deferred. The owner uses a network-specific Private Relay pause for
the Authentik overview; document a tested coexistence configuration if that changes.
