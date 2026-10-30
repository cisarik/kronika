# Kronika Backup and Recovery Runbook

## Status

This is the repository-native operator runbook for the minimum Kronika catalog
backup and recovery foundation.

It is not a real NUC backup record, media backup plan, secret backup plan,
service-replacement procedure, or deployment acceptance record.

Authoritative decision: [ADR-0033](adr/0033-catalog-backup-and-recovery-foundation.md).
Catalog snapshots include migration `0034` tables when the source is at that
head: documents, common records, and approved media projections. Backup and
restore targets keep private POSIX permissions. This note is implementation
evidence, not a deployment or restore acceptance of host data.
Automated scheduling, retention, and restore-readiness:
[ADR-0052](adr/0052-automated-catalog-backup-retention-and-restore-verification.md).
Mounted-filesystem off-device copy and restore verification:
[ADR-0056](adr/0056-off-device-catalog-backup-copy-and-restore-verification.md).
Operator-workstation pull-based catalog snapshot and recovery:
[ADR-0057](adr/0057-operator-workstation-pull-based-catalog-snapshot.md).

The accepted sanitized NUC host baseline is recorded in
[NUC_HOST_BASELINE.md](NUC_HOST_BASELINE.md). That baseline confirms the current
media-storage preparation state but does not replace catalog backup, media
second-copy planning, secret recovery, restore drills, or deployment
acceptance.

## Automated Daily Pipeline

Threat: unverified or unscheduled catalog backups leave no proven recovery point
after destructive administrator operations or failed migrations.

Benefit: one systemd timer runs a full create → verify → disposable restore →
semantic readback → retention pipeline every day.

Limitation: this protects the catalog database only. It does not back up
`/srv/media`, published originals, cover bytes, caches, secrets, AI
configuration, or `runtime-settings.json`. Successful catalog restore does not restore lost original media.
Same-host verified recovery points remain on the live catalog host filesystem;
mounted-filesystem off-device copy is a separate ADR-0056 capability and does
not by itself prove a distinct physical/off-host failure domain.

Default roots:

```text
/var/lib/kronika/catalog-backups
/var/lib/kronika/catalog-restore-verify
/var/lib/kronika/catalog-backup-ops
```

Schedule:

```text
kronika-catalog-backup.timer
OnCalendar=*-*-* 03:17:00 UTC
Persistent=yes
RandomizedDelaySec=900
```

There is exactly one local backup timer. Restore-readiness is proven by the
daily pipeline. Status becomes stale when no complete scheduled success has
occurred for more than 48 hours. Scheduled attempt ordering uses a durable
monotonic `attempt_seq` in operator state so a later failure remains `failed`
even when wall-clock timestamps collide in the same UTC second. Manual
`verify-restore` never supersedes scheduled attempt order.

The [NUC hardening contract](NUC_SECURITY_HARDENING.md) adds repository-source
restrictions to the local-backup service: private devices, protected hostname,
clock and kernel logs, restricted namespaces/realtime, native syscall ABI and
the selected syscall deny groups. `RestrictAddressFamilies=AF_UNIX`,
`IPAddressDeny=any`, and `InaccessiblePaths=/srv/media` preserve catalog-only
scope without IP networking. Host installation and effective isolation proof
belong to row 08, followed by actual timer-triggered backup success and
restore-readiness `ready`; a manual checkpoint alone is insufficient.
The off-device sources, retention policy and release helper are unchanged.
The host-boundary helper's root-only recovery state is separate from catalog
bundles and is never included in them.

Shared exclusive non-blocking `fcntl.flock` on
`/var/lib/kronika/catalog-backup-ops/catalog-backup.lock` protects:

- `create`
- `verify`
- `restore`
- `run-scheduled`
- `run-offdevice`
- `export-latest`
- `verify-restore`
- `expire`

Competing protected commands return sanitized `BACKUP_OPS_BUSY` with zero
mutation. Read-only `status`, `list`, and `retain-plan` do not take the lock.
Production manual pre-deployment `create` therefore shares the lock with the
scheduled service when both use the configured operator-state root.

Successful restore publication removes the private temporary sibling. A fully
successful scheduled or manual restore-verification leaves the disposable
restore-verification root without that operation's database or temporary
sibling; unrelated files in the restore root are never removed.
`pending_cleanup=false` only when every disposable artifact created by that
operation was removed.

Retention:

- `KRONIKA_CATALOG_BACKUP_KEEP_AUTO=30` by default;
- minimum accepted value is `3`; invalid values fail closed;
- only verified ledger-recorded `auto-` bundles expire;
- all non-`auto-` bundles remain pinned;
- failed pipelines perform no deletion;
- `expire` defaults to dry-run; `--apply` is required to delete;
- local retention never scans or deletes off-device destination bundles.

Operator commands:

```text
kronika-backup run-scheduled
kronika-backup run-offdevice
kronika-backup export-latest
kronika-backup status
kronika-backup list
kronika-backup retain-plan
kronika-backup expire --dry-run
kronika-backup expire --apply
kronika-backup verify-restore --bundle <bundle>
```

## Off-Device Mounted Copy

Threat: a catalog recovery point stored only on the NUC can be lost with that
host filesystem.

Benefit: `kronika-backup run-offdevice` copies the latest verified scheduled
catalog recovery point onto a distinct mounted destination, verifies exact
bytes, publishes without replace, and restore-verifies from the published
destination bundle into the disposable local restore root.

Limitation: this repository capability proves the mounted-filesystem copy and
verification contract only. It does not back up media, covers, secrets, AI
configuration, or host state. It does not by itself prove that a chosen
destination survives complete NUC loss until separately authorized host
acceptance establishes the physical/network failure domain. V1 has no off-device
retention or final-bundle deletion command.

Fixed scheduled destination:

```text
/mnt/framenest-catalog-offdevice
```

Required destination marker written by the current copier:

```text
.kronika-catalog-offdevice.json
```

Readers still accept the historical marker
`.framenest-catalog-offdevice.json`.

Optional non-secret pin:

```text
KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID=<32 lowercase hex>
```

When unset, off-device copy remains disabled. Ordinary `status` output exposes a
sanitized `off_device` block and must not print the destination root, marker ID,
device numbers, or mount identity.

Optional timer:

```text
kronika-catalog-offdevice.timer
OnCalendar=*-*-* 04:17:00 UTC
Persistent=yes
RandomizedDelaySec=900
```

## Operator-Workstation Pull (Preferred Current Off-Host Layer)

Threat: a catalog recovery point that exists only on the NUC, or only on a
mounted destination the NUC can write, can still be lost with that host or
mount failure domain.

Benefit: the operator workstation initiates an OpenSSH pull of exactly the
ledgered successful scheduled recovery point, verifies framed bytes locally,
restore-verifies into a disposable local database, and publishes a private
snapshot with no-replace atomicity. The NUC never learns the workstation
destination root and gains no workstation credential or snapshot write/delete
authority. No NUC outbound backup connection is introduced.

Limitation: this is a repository capability after implementation. It is not
real-host proven until later E3 workstation/NUC provisioning and the first
accepted pull/verify. It remains catalog-only. It does not back up media,
cover bytes beyond existing bundle scope, secrets, AI configuration, or full
host state. V1 has no workstation retention, no automatic scheduling, and no
Level-4 production replacement.

Recovery layers remain distinct:

1. same-host scheduled verified backup;
2. optional mounted off-device copy (ADR-0056);
3. preferred current operator-workstation pull (ADR-0057).

Do not redefine `off_device` status to include workstation snapshots.

### Export and sudo contract

Remote export command:

```text
kronika-backup export-latest
```

Repository launcher source:

```text
deploy/ubuntu/kronika-catalog-export-v1
```

Intended later host install path:

```text
/usr/local/libexec/kronika-catalog-export-v1
```

Intended later sudoers semantic contract (operator username is host-specific;
use the placeholder, never hardcode a real username into product code):

```text
<KRONIKA_OPERATOR> ALL=(kronika) NOPASSWD:NOSETENV: /usr/local/libexec/kronika-catalog-export-v1 ""
```

The explicit final `""` is required so zero arguments are matched. Run-as
target is `kronika`, not `root`. No wildcard `kronika-backup *` rule. No
caller environment override. Host provisioning must validate with `visudo`
before activation. This repository task does not install the launcher or
sudoers on a real NUC.

### Workstation recovery CLI

```text
kronika-recovery init-store --store-root <abs> --mount-root <abs>
kronika-recovery pull --store-root <abs> --mount-root <abs> --expected-store-id <32hex> --ssh-target <alias-or-user@host>
kronika-recovery list --store-root <abs> --mount-root <abs> --expected-store-id <32hex>
kronika-recovery verify --store-root <abs> --mount-root <abs> --expected-store-id <32hex> --bundle-id <id>
```

Generic code accepts store/mount/SSH parameters. Concrete workstation values
remain local operator configuration for a later Fish wrapper outside the
repository (`~/.local/bin/kronika-nuc-backup.fish` is intentionally not
installed by this repository task).

### Recovery levels

- Level 1 (implemented): offline workstation `list` and `verify`.
- Level 2 (documented): reverify local snapshot, transfer the nested original
  bundle into bounded operator-owned NUC staging, no-replace move/install into
  private service-owned recovery staging, correct owner/mode, reverify
  identity. No new automatic remote-import protocol.
- Level 3 (documented): staged bundle → strict verify → restore into a NEW
  ABSENT disposable database → SQLite integrity/FK/Alembic/semantic checks.
  No migration. No production replacement. Equal revision may proceed through
  disposable verification; older revision may be verified historically but must
  not be silently migrated; newer-than-runtime revision fails compatibility for
  that runtime.
- Level 4 (out of scope): service stop, production catalog replacement,
  migration-as-recovery, restart/cutover.

### Future real-host sequencing

Current public repository and current production SHA may intentionally differ
until an authorized immutable deployment. Repository implementation alone does
not make `export-latest` available on the current NUC. Future sequencing:

1. implementation;
2. independent repository acceptance;
3. publication;
4. pre-deployment verified production backup/readiness;
5. immutable deployment of exact published SHA;
6. health/schema acceptance;
7. launcher provisioning;
8. exact sudo rule provisioning;
9. workstation store provisioning;
10. first real pull;
11. offline workstation verification;
12. optional Level-2/3 non-production recovery drill;
13. logical-whole closure.

Do not claim real off-host recovery before stages 10–11 pass.

## State Classes

| State | Path | Backup treatment |
|---|---|---|
| Catalog database | `/var/lib/kronika/catalog.sqlite3` | Included in catalog backup bundle |
| Durable accepted cover facts (migration `0022`) | catalog database | Included in catalog backup bundle |
| Durable cover artifacts | `/var/lib/kronika/covers` | Excluded; regenerable while source is available |
| Cover thumbnails | `/var/cache/kronika/cover-thumbnails` | Excluded; regenerate |
| Non-secret AI configuration | `/var/lib/kronika/ai/config.json` | Manual recovery for now |
| Administrator runtime settings overlay | `/var/lib/kronika/runtime-settings.json` | Excluded; loss returns to env/default |
| Gallery preview cache | `/var/cache/kronika/gallery-previews` | Excluded; regenerate |
| Original media | `/srv/media` | Excluded; needs separate second copy |
| Published upload originals | configured registered publication library | Excluded; needs separate media second copy |
| YouTube acquisition claims and provenance | catalog database | Included in catalog backup bundle |
| YouTube acquisition staging | `/var/lib/kronika/youtube-acquisition` | Excluded; lifecycle-owned transient state |
| Secrets | outside Git | Excluded; re-enter or rotate later |

The catalog bundle contains only:

```text
manifest.json
catalog.sqlite3
```

Revision `0013` publication provenance is catalog data and is included in the
SQLite artifact, but the published media bytes it describes are not. Restoring
the catalog bundle alone therefore does not restore a published upload original
or prove that its destination object is available.

Revision `0019` YouTube claims, retry/reuse lineage, downloader evidence, and
final media/location links are likewise catalog data included in the SQLite
artifact. Claim staging bytes, partial downloads, quarantine bytes, and
published originals are excluded. A restore therefore preserves durable
provenance but does not resume or recreate missing media bytes by itself.

Revision `0022` durable accepted-cover rows are also catalog data and travel
inside the SQLite artifact. Durable cover artifacts and cover thumbnails are
not included in the bundle. A restore therefore preserves accepted-cover facts
but not the cover artifact bytes; an artifact may be regenerated only while the
recorded source location and source version remain available, otherwise manual
re-selection is required. Kronika does not claim complete disaster durability
for cover artifacts in this slice (see
[ADR-0050](adr/0050-durable-manual-cover-foundation.md)).

## Check

Threat: backing up the wrong state, treating rebuildable cache as durable state,
or relying on an untested command during deployment.

Benefit: confirms the operator is acting on the catalog database boundary only.

Limitation: check does not create a backup and does not inspect real media.

Preconditions:

- An authorized operator task exists.
- The source catalog path is known.
- The source is expected to be an existing SQLite catalog database.
- No service stop, restart, migration, or production replacement is part of
  this repository-only check.

Mutation class: read-only.

Stop conditions:

- The source path is not the expected catalog database.
- The operator wants to include `/srv/media`, cache files, `.env` files, or
  credentials in the catalog bundle.
- A command would expose private paths, secrets, or media filenames in shared
  evidence.

Rollback or cleanup: none for read-only checks.

Verification evidence:

- Exact Kronika commit or release.
- The planned source is the catalog database, not a media or cache path.
- Backup tooling help is available:

```text
kronika-backup --help
kronika-backup create --help
kronika-backup verify --help
kronika-backup restore --help
```

## Plan

Threat: creating a local-only backup that is later mistaken for disaster
recovery, or deleting older backups before a new backup is verified.

Benefit: defines destination, retention, and evidence before mutation.

Limitation: the repository does not choose the physical off-device product,
destination, schedule, or retention period.

Preconditions:

- Select a new local bundle directory that does not exist.
- Select an off-device copy destination controlled by the operator.
- Decide how many verified bundles to retain.
- Decide where restore-drill evidence will be recorded.

Mutation class: planning only.

Stop conditions:

- The plan has only one copy on the NUC.
- The plan treats the media SSD as a backup.
- The plan stores secrets in the bundle or manifest.
- The plan includes automatic deletion without verified replacement.

Rollback or cleanup: revise the plan before creating state.

Verification evidence:

- Planned bundle identifier.
- Planned off-device destination class.
- Retention rule.
- Restore-drill target path class.

## Create

Threat: a naive copy of a live SQLite database can miss WAL state or capture an
inconsistent image.

Benefit: `kronika-backup create` uses SQLite online backup, verifies the
snapshot, records manifest metadata, and publishes a complete bundle only after
validation.

Limitation: create does not migrate, stop services, copy cache, copy media,
include AI configuration, include secrets, or copy off-device. It prioritizes
no-overwrite publication; an interrupted final publication may leave a bundle
directory that fails verification and must be treated as unusable.

Preconditions:

- Source catalog database exists.
- Output bundle path is new and absent.
- Output parent exists and is trusted by the operator.

Mutation class: creates a new backup bundle directory.

Command shape:

```text
kronika-backup create \
  --source /var/lib/kronika/catalog.sqlite3 \
  --output /path/to/new/catalog-backup
```

Development launcher route:

```text
./kronika backup create \
  --source /path/to/catalog.sqlite3 \
  --output /path/to/new/catalog-backup
```

Stop conditions:

- Source is missing, a directory, or a symlink.
- Output already exists.
- Output parent is missing, invalid, or unsafe.
- Permission restriction fails on a POSIX platform where restrictive file modes
  are supported.
- The command returns non-zero.

Rollback or cleanup:

- The command removes only owned temporary state on failure.
- Do not manually delete older verified backups merely because creation of a
  new backup started.

Verification evidence:

- Exit code `0`.
- Single JSON success line with `state` equal to `created`.
- Bundle contains exactly `manifest.json` and `catalog.sqlite3`.
- Output does not disclose source or output paths.

## Verify

Threat: a bundle may be incomplete, tampered with, corrupted, or internally
inconsistent even when files exist.

Benefit: verify checks manifest structure, supported schema version, required
files, absence of unexpected bundle entries, symlink safety, byte size,
SHA-256 digest, SQLite integrity, foreign-key check, and Alembic revision.

Limitation: verify does not prove media recovery, secret recovery, service
readiness, or off-device copy success.

Preconditions:

- Bundle directory exists.
- Bundle is not being modified.

Mutation class: read-only.

Command:

```text
kronika-backup verify --bundle /path/to/catalog-backup
```

Stop conditions:

- Verification returns non-zero.
- Manifest is malformed or unsupported.
- The catalog checksum, integrity, or revision check fails.
- The bundle contains files, directories, symlinks, or secret-shaped artifacts
  other than the two declared bundle artifacts.
- The bundle contains temporary/incomplete state.

Rollback or cleanup:

- Keep failed bundles quarantined for diagnosis or delete them only under a
  separate operator cleanup decision.

Verification evidence:

- Exit code `0`.
- Single JSON success line with `state` equal to `verified`.
- Recorded catalog size, SHA-256 digest, and Alembic revision.

## Copy Off-Device

Threat: a backup stored only on the NUC can be lost with the NUC, disk, power
event, theft, or operator error.

Benefit: a verified mounted-filesystem off-device copy protects the catalog
bundle against local filesystem loss after ADR-0056 repository implementation.

Limitation: Kronika does not implement cloud upload, media copy, secret
backup, or off-device retention automation in this slice. Repository evidence
proves the copy/verify contract only; physical/off-host failure-domain proof
remains separate host acceptance.

Preconditions:

- Local scheduled backup-and-restore evidence exists.
- Destination mount `/mnt/framenest-catalog-offdevice` is provisioned with the
  trusted marker and `bundles/` layout.
- `KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID` is set to the marker identity.

Mutation class: Kronika-owned staging copy and no-replace publication under
`bundles/`, plus disposable local restore verification.

Command:

```text
kronika-backup run-offdevice
```

Stop conditions:

- Destination gate fails (missing mount, same device, unsafe marker/layout).
- Source recovery point is absent, unverified, or not ledgered.
- Final destination bundle conflicts and is not an exact identity match.
- Atomic no-replace publication is unsupported.
- Destination restore verification fails.

Rollback or cleanup:

- Keep the verified local bundle.
- Incomplete staging directories may be removed only when they pass exact
  Kronika-owned stage validation.
- Conflicting final destination bundles are never overwritten or deleted by
  this command.

Verification evidence:

- Exit code `0` with sanitized success JSON.
- `status.off_device.readiness` becomes `ready` only after destination restore
  verification succeeds.
- No destination root, marker ID, or device identity appears in ordinary status.

## Restore Drill

Threat: a backup that has never been restored may fail during a real recovery.

Benefit: restore drill proves that a verified bundle can produce a usable
catalog file at a new destination.

Limitation: restore drill does not replace production, run migrations, start the
service, verify health, recover media, or recover secrets.

Preconditions:

- Use a disposable absent destination path.
- The destination parent is safe for test output.
- The source bundle verified successfully.

Mutation class: creates a new restored database file at an absent destination.

Command:

```text
kronika-backup restore \
  --bundle /path/to/catalog-backup \
  --destination /path/to/disposable/restored-catalog.sqlite3
```

Stop conditions:

- Destination exists or is a symlink.
- Restore returns non-zero.
- Permission restriction fails on a POSIX platform where restrictive file modes
  are supported.
- Post-restore checksum, integrity, or revision verification fails.

Rollback or cleanup:

- Delete the disposable restored database only after recording evidence.
- Do not delete or mutate the source bundle.

Verification evidence:

- Exit code `0`.
- Single JSON success line with `state` equal to `restored`.
- Restored catalog digest and Alembic revision match the bundle.

## Production Recovery

Threat: overwriting production state incorrectly can destroy the last usable
catalog or start Kronika against an incompatible schema.

Benefit: separating restore-to-new-path from production replacement keeps the
initial CLI non-destructive.

Limitation: production replacement is not automated in this repository task.

Preconditions:

- A future explicit recovery task authorizes service-affecting steps.
- Current production database is preserved first.
- Bundle verifies.
- Restore to a new path succeeds.
- Migration need is understood.

Mutation class: future service-affecting recovery mutation, not implemented by
`kronika-backup`.

Required future sequence:

```text
stop/switch boundary
-> preserve previous database
-> restore to new path
-> verify
-> controlled replacement
-> explicit migration when needed
-> readiness
-> controlled service start
-> health and log verification
```

Stop conditions:

- No separate preservation of the current production database exists.
- The restore target would overwrite `/var/lib/kronika/catalog.sqlite3`
  directly.
- Migration status is unknown.
- Readiness fails.
- Logs disclose secrets, private paths, SQL, or tracebacks.

Rollback or cleanup:

- Restore the preserved previous database and previous release when recovery
  fails before acceptance.
- Keep failed restored files for diagnosis or remove them only under explicit
  cleanup authority.

Verification evidence:

- Preserved previous database identifier.
- Verified restored database identifier.
- Migration result when migration is authorized.
- Readiness output.
- Health and sanitized log evidence.

## Retention

Threat: too few backups, unverified backups, or automatic deletion can remove
the only recoverable catalog.

Benefit: automated retention keeps the newest verified `auto-` recovery points
while pinning all non-`auto-` historical bundles.

Limitation: automatic expiry never claims off-device durability and never deletes
pinned pre-deployment or manual bundles.

Preconditions:

- Only completed verified ledger-recorded `auto-` bundles are eligible.
- `KRONIKA_CATALOG_BACKUP_KEEP_AUTO` is valid (`>= 3`, default `30`).
- A newer verified automatic recovery point exists according to the policy.

Mutation class: deletes only whole eligible automatic bundle directories after
dry-run or scheduled success.

Stop conditions:

- A current scheduled create/verify/restore pipeline failed.
- Deletion would remove a non-`auto-` bundle.
- Deletion would remove the newest verified automatic bundle.
- Deletion would reduce verified automatic recovery points below three.
- The path is a symlink or escapes the configured backup root.

Rollback or cleanup:

- Retain older verified bundles until replacement evidence exists.
- Use `kronika-backup expire --dry-run` before `--apply`.

Verification evidence:

- List of retained bundle identifiers.
- Verification evidence for retained current bundles.
- Operator status and events for applied expiry.

## Failure Handling

Threat: partial temporary state or unsafe manual cleanup can remove the wrong
files.

Benefit: Kronika-owned operations clean only their own temporary artifacts.

Limitation: external copy tools and operator storage destinations have their
own cleanup rules.

Preconditions:

- Identify which operation failed.
- Preserve stderr/stdout JSON output.

Mutation class: cleanup only when the failed artifact is positively identified
as temporary or disposable.

Stop conditions:

- The path is a symlink.
- The path is not a Kronika-created temporary artifact or disposable restore
  target.
- Cleanup would touch media, secrets, environment files, or production
  database state.

Rollback or cleanup:

- Remove only owned temporary bundle directories beginning with the documented
  temporary prefix when they are inside the selected output parent and are not
  symlinks.
- Remove disposable restore-drill output only after evidence capture.

Verification evidence:

- Failed command output.
- Confirmation that source database and bundle were not mutated unexpectedly.
- Confirmation that no media/cache/secrets were touched.

## Evidence Capture

Capture:

- Exact Kronika commit or release.
- Command executed.
- Exit code.
- Sanitized JSON output.
- Bundle manifest.
- Catalog SHA-256 digest.
- Alembic revision.
- Off-device copy verification.
- Restore-drill result.

Do not capture:

- Real passwords, tokens, cookies, private keys, provider credentials, or
  authorization headers.
- Real `.env` files or `/etc/kronika/kronika.env` content when it may
  contain operational details.
- Private media filenames.
- Private home paths unless the operator has explicitly chosen to record them in
  private notes outside the repository.
- Real IP addresses, disk UUIDs, or serial numbers in repository artifacts.

## What This Runbook Does Not Prove

Creating a catalog backup is not the same as verifying it.

Verifying a backup bundle is not the same as restoring it.

Restoring into a new destination is not the same as replacing production.

Replacing production during a future recovery is not the same as migrating a
restored database.

Migrating a restored database is not the same as proving application readiness.

Application readiness is not the same as recovering original media.

Recovering original media is not the same as recovering secrets.

The NUC is not deployment-ready merely because repository tests pass.
