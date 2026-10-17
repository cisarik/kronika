# Kronika Ubuntu Deployment Support

This directory holds the discoverable, tested, repository-native Ubuntu
deployment support, including the routine immutable release-update contract
(`kronika-release` / `kronika_release.py`). Routine refresh of the
development-and-testing NUC to the exact public `main` SHA uses that helper
under standing
[ADR-0075](../../docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)
authority. Non-routine host work still requires a separately authorized
bounded task.

The current authoritative runbook is
[docs/UBUNTU_NUC_DEPLOYMENT.md](../../docs/UBUNTU_NUC_DEPLOYMENT.md). The
architecture decision is
[ADR-0032](../../docs/adr/0032-ubuntu-nuc-deployment-foundation.md). The
catalog backup and recovery prerequisite is documented in
[docs/BACKUP_AND_RECOVERY.md](../../docs/BACKUP_AND_RECOVERY.md) and
[ADR-0033](../../docs/adr/0033-catalog-backup-and-recovery-foundation.md).
Mounted off-device copy is
[ADR-0056](../../docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md).
Operator-workstation pull is
[ADR-0057](../../docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md).
Routine immutable release updates are
[ADR-0060](../../docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md).
Independent Mullvad egress and operator network recovery are
[docs/OPERATOR_NETWORK.md](../../docs/OPERATOR_NETWORK.md) and
[ADR-0058](../../docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md).

Repository source for the later root-owned export launcher:

```text
deploy/ubuntu/kronika-catalog-export-v1
```

That launcher is source material only until a later bounded host-provisioning
task installs it under `/usr/local/libexec/` with root ownership and activates
the exact no-argument sudoers rule documented in the backup runbook.

## Routine Immutable Release Update

```text
deploy/ubuntu/kronika-release status
deploy/ubuntu/kronika-release check --release <40-hex-SHA>
deploy/ubuntu/kronika-release deploy --release <40-hex-SHA> --yes
deploy/ubuntu/kronika-release rollback --release <40-hex-SHA> --yes
deploy/ubuntu/kronika-release activate-capture --release <40-hex-SHA> --yes
deploy/ubuntu/kronika-release rollback-capture --release <40-hex-SHA> --yes
```

`deploy` and `rollback` move only the web pointer `/opt/kronika/current` and
`kronika.service`. They report the web SHA and the capture SHA. They do not
switch the capture pointer and they do not restart the capture bridge or
runner. The capture pointer is `/opt/kronika/capture-current`.
`activate-capture` switches it to a reviewed capture release and restarts the
bridge, then the runner, once, after the drain and five-minute brake checks.
`rollback-capture` switches it back to an existing capture release under those
same checks and the same single bridge-then-runner restart.
The one-time capture-path migration has already run and `migrate-capture-paths` is gone. Routine `status`, `check`, `deploy`, and `rollback` address only the canonical layout and do not migrate.
Capture units
under `deploy/systemd/kronika-capture-*.service` and
`deploy/systemd/kronika-capture.env.example` are source material. Nothing in
this directory is evidence that capture is already deployed.

`kronika-release` is the single Fish-compatible operator entry point; it
resolves the repository root and runs `deploy/ubuntu/kronika_release.py`
through the repository `.venv/bin/python`. The engine is standard-library only
and remains Ubuntu system Python 3.12 compatible for its private transferred
remote mode. It never invokes `uv`, never runs migrations, and never accepts a
user-supplied remote command. Schema jumps stop `deploy --yes` at exit 13
(`migration-required`) after the target tree is published; the documented
continuation is the runbook section 5 annex, not a fifth helper command.
Full operating rules and the same-schema, checkpoint, cutover, rollback,
privilege-release, and schema-jump-continuation boundaries are documented
in [docs/UBUNTU_NUC_DEPLOYMENT.md](../../docs/UBUNTU_NUC_DEPLOYMENT.md) and
[ADR-0060](../../docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md).

## Phase Map

| Phase | Repository support | Mutation class |
|---|---|---|
| check | Runbook sections `0`, `1`, and `6` | read-only |
| plan | Runbook section `2` | no host mutation |
| apply | Runbook sections `3`, `4`, `5`, and `7` | planned, reversible, or service-affecting |
| verify | Runbook sections `6`, `8`, and `10` | read-only after mutation |
| rollback | Runbook section `9` | planned recovery mutation |
| catalog backup | Backup runbook sections `create`, `verify`, and `restore drill` | new bundle or disposable restored catalog |

## Boundaries

`fn-production-env-deploy` is the repository-owned Fish entry point for the
first production AI credential/configuration helper. It has a non-mutating check
mode that validates the selected private credential source and the selected
provider-specific tracked systemd drop-in template before any remote activity.
The helper is source material until a later bounded deployment task grants
exact host authority.

The helper manages only production AI provider configuration and the selected
provider credential. It must not be used as a general environment-file copier
or as authorization to run real NUC commands outside an explicit host task.
It atomically acquires `/run/kronika-ai-credential-deploy`; existing recovery
material causes a fail-closed stop before credential transmission or production
mutation. After a complete remote backup marker exists, failed deployment
mutations roll back the selected credential, systemd drop-in, and non-secret AI
configuration. The drop-in installed remotely is the exact tracked template
bytes transferred as a non-secret stdin payload, verified by deterministic byte
equivalence before and after atomic rename. Before restart, the helper verifies
systemd syntax, daemon-reloads, confirms `kronika.service` is still enabled,
and checks that systemd loaded the intended drop-in and credential identity.
Before restart, loaded-credential acceptance verifies the exact trusted drop-in
bytes, `DropInPaths`, and `systemctl cat` mapping without relying on redacted
`systemctl show LoadCredential` output. After readiness, it checks only the
same loopback capability endpoint used by the web status modal and requires the
selected provider/model to be configured, available, and credential-available.
A historical connection-test record does not fail deployment and is not treated
as proof that the newly installed credential is valid. Rollback daemon-reloads,
restarts, and waits up to 30 seconds for readiness on the restored service. Deployment and rollback
distinguish terminal service failure from readiness timeout. If rollback or
cleanup fails, the helper reports the sanitized phase and leaves recovery
material under `/run/kronika-ai-credential-deploy` for operator recovery.

This directory must not contain secrets, host UUIDs, disk serials, LAN IP
addresses, SSH fingerprints, private media filenames, or generated deployment
logs.
