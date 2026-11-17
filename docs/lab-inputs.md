# Lab input contracts

Version 2 of the [managed workflow](deployment-workflow.md) checks the release,
identity and backup inputs before managed-host commands. Version 1 retains its
original checks for compatibility. Use version 2 for new runs.
The controller needs Git, SOPS, age and OpenSSH, plus the tools from `make setup`.

## Sources and ownership

| Input | Owner |
| --- | --- |
| Component defaults | Each repository's role defaults; do not copy them into new inventories |
| Host groups, topology and deployment choices | Private inventory and its group/host variables |
| Explicit deployment overrides | Ordered private extra-variable files |
| Application image and revision | CI `image-release.json`; the workflow derives its selection from this receipt |
| Source bundle, branch ref and authorization module | Workflow `release`; effective source inputs must match |
| Identity names and client/group identifiers | Existing scoped SOPS bundle; never move names into tracked examples |
| Credentials and age identities | Existing scoped private files; the workflow does not create or rotate them |

Preserve Ansible precedence. Existing copied inventory values remain explicit
overrides until reviewed; deleting them automatically could change deployment.
The old private assembler is not a maintained entry point and must not overwrite
reviewed inputs. Inventory generation and bootstrap-to-VPN handoffs remain separate
work.

## Checks

The workflow checks both inventories locally, then:

- Fetches the declared bundle branch into an empty temporary Git repository.
  Incremental bundles and revisions differing from the image receipt fail.
- Reads the selected source's static `GROUP_ROLES` object without executing
  application code. The supported form is `const GROUP_ROLES = Object.freeze({...});`
  with three quoted group names mapped to `reader`, `editor` and `admin`.
  A changed representation needs an explicit adapter or a published JSON contract.
- Decrypts the identity naming bundle in memory, reuses the existing
  identity-profile validator, and compares role groups, clients, issuer and user grants.
- For legacy Authentik backups, encrypts a disposable value and decrypts it with
  `rehearsal_host_age_key_source`. This verifies the declared self-check key, not
  its installed copy or offline recovery. Unified backups instead require matching
  source/receiver enablement, lab endpoints, the pinned source key and a valid recipient.

Release files, the deployment identity and known referenced capability files are
fingerprinted and rechecked before subsequent commands, alongside both repository
refs and inventories. Each host's `capabilities`
list declares additional files used by opaque lookups, such as HOU4's encrypted
Grafana input. Keep all these files operator-owned and mode `0600`.
Arbitrary custom lookups are not automatically discovered.
Registry authorization, DNS-provider access, Google callbacks and actual backup
recovery still require their separate checks.

Run the checks without contacting either VM:

```bash
.venv/bin/python scripts/deploy-lab.py --config /private/lab/workflow.json --phase inputs
```

Version 2 also runs them during `preflight`, `reconcile` and `verify`. Decrypted
names stay in memory; private receipts contain hashes and step results.
