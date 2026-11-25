# Clean restore gate — 2026-11-19

Scope: controller-side recovery of the verified isolated test point into a
disposable, network-isolated PostgreSQL container and private staging. HOU1,
HOU4 and Proxmox were not modified. This is a restore gate; it does not claim a
fresh Debian host restore or publication.

## Results

| Check | Result |
| --- | --- |
| Point authentication | Passed for manifest `20261119T052214Z-5b41da6e8272`; age decryption, archive safety and source revision matched. |
| Staff database | SQLite integrity passed; 15 tables were present. |
| NetBird state | Five recovered SQLite databases passed integrity checks. |
| Media | Recovery tree was readable; this point contains no media files. |
| PostgreSQL | Imported 4,583,330 bytes into a fresh database with zero public tables beforehand; 187 tables were present afterward. |
| Identity data | User, group, application and OAuth provider tables were present. |
| Session cleanup | Existing identity session/token tables were cleared before any restored writers were started. |
| Isolation | PostgreSQL used `--network none`, no ports were published, and the container was removed afterward. |
| Plaintext cleanup | Private extracted recovery data was removed after verification. |

The private receipt is under
`/home/dev/.ssh/hou-lab/clean-restore/20261119T/restore-report.json`.
The offline identity remained on the controller and was never installed in a
container or VM.

## Boundary

The host recovery playbook still requires a separate fresh Debian 12 VM, an
independent console checkpoint, registry/image locks and explicit publication
review. Those gates were not bypassed by this disposable restore. The next
acceptance step is a dedicated replacement VM; HOU1 and HOU4 remain intact.
