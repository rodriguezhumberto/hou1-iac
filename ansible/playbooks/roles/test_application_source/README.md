# Test application source

Owns `/opt/hou1-test-source/staff.bundle` and its local bare Git remote. Bootstrap
and ordinary webstack reconciliation use the same role when a source bundle is
declared. It is restricted to the isolated test inventory and overlay.

Inputs are the existing `rehearsal_staff_bundle`, `rehearsal_staff_bundle_ref` and
`rehearsal_staff_ref`; `app_repo_pat` must select the fixed local remote. The lab
workflow validates the standalone bundle against the CI release receipt first.
The role checks the imported commit before selecting its deployment ref. Explicit
rollback may select an older commit; ordinary Git checkout safety still applies.
