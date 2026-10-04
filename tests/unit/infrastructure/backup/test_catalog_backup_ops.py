"""Unit and integration tests for automated catalog backup operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import threading

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _migrated_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


def _ops_config(tmp_path: Path, *, keep_auto: int = 30):
    from kronika.infrastructure.persistence.catalog_backup_ops import CatalogBackupOpsConfig

    database = _migrated_database(tmp_path / "catalog.sqlite3")
    return CatalogBackupOpsConfig(
        database_path=database,
        backup_root=tmp_path / "catalog-backups",
        restore_verify_root=tmp_path / "catalog-restore-verify",
        ops_root=tmp_path / "catalog-backup-ops",
        keep_auto=keep_auto,
    )


def test_automatic_and_pinned_bundle_name_classification() -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import is_automatic_bundle_name

    assert is_automatic_bundle_name("auto-20260804T031700Z-deadbeef")
    assert not is_automatic_bundle_name("pre-admin-catalog-removal-deploy-20260804T150358Z-3f89b8b2")
    assert not is_automatic_bundle_name("auto-bad")
    assert not is_automatic_bundle_name("auto-20260804T031700Z-deadbee")  # 7 hex


def test_keep_auto_validation_rejects_invalid_values() -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        parse_keep_auto,
    )

    assert parse_keep_auto(None) == 30
    assert parse_keep_auto("30") == 30
    assert parse_keep_auto(3) == 3
    for raw in (2, 0, -1, "2", "nope", "30.5", True, 3651):
        with pytest.raises(CatalogBackupOpsError, match="retention"):
            parse_keep_auto(raw)


def test_restore_readiness_boundaries() -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import derive_restore_readiness

    now = datetime(2026, 8, 4, 15, 0, 0, tzinfo=UTC)
    assert derive_restore_readiness(status={}, now=now) == "never_verified"
    assert derive_restore_readiness(status={"current_operation": "run-scheduled"}, now=now) == "busy"
    assert (
        derive_restore_readiness(
            status={"last_scheduled_attempt": {"state": "failed", "completed_at_utc": "2026-08-04T14:00:00Z"}},
            now=now,
        )
        == "failed"
    )
    success = {
        "last_successful_scheduled_backup_and_restore": {
            "completed_at_utc": "2026-08-04T15:00:00Z",
            "attempt_seq": 1,
        }
    }
    assert derive_restore_readiness(status=success, now=now) == "ready"
    assert (
        derive_restore_readiness(
            status=success,
            now=now + timedelta(hours=48),
        )
        == "ready"
    )
    assert (
        derive_restore_readiness(
            status=success,
            now=now + timedelta(hours=48, seconds=1),
        )
        == "stale"
    )
    failed_after = {
        **success,
        "last_scheduled_attempt": {
            "state": "failed",
            "completed_at_utc": "2026-08-04T15:00:00Z",  # same wall clock as success
            "attempt_seq": 2,
        },
    }
    assert derive_restore_readiness(status=failed_after, now=now) == "failed"
    later_success = {
        "last_successful_scheduled_backup_and_restore": {
            "completed_at_utc": "2026-08-04T15:00:00Z",
            "attempt_seq": 3,
        },
        "last_scheduled_attempt": {
            "state": "failed",
            "completed_at_utc": "2026-08-04T15:00:00Z",
            "attempt_seq": 2,
        },
    }
    assert derive_restore_readiness(status=later_success, now=now) == "ready"
    malformed = {
        "last_successful_scheduled_backup_and_restore": {
            "completed_at_utc": "2026-08-04T15:00:00Z",
            "attempt_seq": 1,
        },
        "last_scheduled_attempt": {
            "state": "failed",
            "completed_at_utc": "2026-08-04T16:00:00Z",
            "attempt_seq": "not-an-int",
        },
    }
    assert derive_restore_readiness(status=malformed, now=now) == "ready"


def test_run_scheduled_pipeline_success_and_source_unchanged(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import sha256_file
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        read_operator_status,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    before = sha256_file(config.database_path)
    unrelated = config.restore_verify_root / "keep-me.txt"
    config.restore_verify_root.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("unrelated", encoding="utf-8")
    result = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
    )
    assert result.bundle_id.startswith("auto-")
    assert result.pending_cleanup is False
    assert (config.backup_root / result.bundle_id / "manifest.json").is_file()
    assert sha256_file(config.database_path) == before
    status = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert status["last_successful_scheduled_backup_and_restore"]["bundle_id"] == result.bundle_id
    assert status["last_successful_scheduled_backup_and_restore"]["attempt_seq"] == 1
    assert status["last_manual_verify_restore"] is None
    assert status.get("last_successful_offdevice_copy_and_restore") is None
    assert unrelated.read_text(encoding="utf-8") == "unrelated"
    leftovers = [
        path
        for path in config.restore_verify_root.iterdir()
        if path.name != "keep-me.txt"
    ]
    assert leftovers == []
    operator = read_operator_status(config, offdevice_environ={})
    assert operator["off_device"]["configured"] is False
    assert operator["off_device"]["readiness"] == "disabled"


def test_same_timestamp_failure_uses_attempt_seq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.infrastructure.persistence import catalog_backup_ops as ops
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        derive_restore_readiness,
        run_scheduled_catalog_backup,
        verify_restore_bundle,
    )

    config = _ops_config(tmp_path)
    clock = datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC)
    success = run_scheduled_catalog_backup(config, now=clock)
    assert success.pending_cleanup is False

    def boom(*_args, **_kwargs):
        raise CatalogBackupOpsError("forced", error_code="FORCED")

    monkeypatch.setattr(ops, "create_catalog_backup", boom)
    with pytest.raises(CatalogBackupOpsError):
        run_scheduled_catalog_backup(config, now=clock)  # identical wall clock
    status = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert status["last_scheduled_attempt"]["state"] == "failed"
    assert status["last_scheduled_attempt"]["attempt_seq"] == 2
    assert status["last_successful_scheduled_backup_and_restore"]["attempt_seq"] == 1
    assert status["last_scheduled_attempt"]["completed_at_utc"] == status[
        "last_successful_scheduled_backup_and_restore"
    ]["completed_at_utc"]
    assert derive_restore_readiness(status=status, now=clock) == "failed"

    reloaded = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert derive_restore_readiness(status=reloaded, now=clock) == "failed"

    evidence = verify_restore_bundle(config, success.bundle_id, now=clock)
    assert evidence["operation"] == "verify-restore"
    after_manual = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert after_manual["last_manual_verify_restore"]["bundle_id"] == success.bundle_id
    assert derive_restore_readiness(status=after_manual, now=clock) == "failed"

    monkeypatch.setattr(ops, "create_catalog_backup", create_catalog_backup)
    later = run_scheduled_catalog_backup(config, now=clock)
    assert later.bundle_id != success.bundle_id
    final = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert final["last_successful_scheduled_backup_and_restore"]["attempt_seq"] == 3
    assert derive_restore_readiness(status=final, now=clock) == "ready"


def test_nested_operation_lock_does_not_self_deadlock(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        operation_lock,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    with operation_lock(config) as held:
        assert held
        result = run_scheduled_catalog_backup(
            config,
            now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
        )
    assert result.bundle_id.startswith("auto-")
    assert result.pending_cleanup is False


def test_failed_pipeline_skips_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.infrastructure.persistence import catalog_backup_ops as ops
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path, keep_auto=3)
    first = run_scheduled_catalog_backup(config, now=datetime(2026, 8, 1, 3, 17, 0, tzinfo=UTC))
    second = run_scheduled_catalog_backup(config, now=datetime(2026, 8, 2, 3, 17, 0, tzinfo=UTC))
    third = run_scheduled_catalog_backup(config, now=datetime(2026, 8, 3, 3, 17, 0, tzinfo=UTC))
    assert {first.bundle_id, second.bundle_id, third.bundle_id}

    def boom(*_args, **_kwargs):
        raise CatalogBackupOpsError("forced", error_code="FORCED")

    monkeypatch.setattr(ops, "create_catalog_backup", boom)
    with pytest.raises(CatalogBackupOpsError):
        run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    remaining = {path.name for path in config.backup_root.iterdir() if path.is_dir()}
    assert first.bundle_id in remaining
    assert second.bundle_id in remaining
    assert third.bundle_id in remaining


def test_retention_keep_n_floor_newest_and_pinned(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsConfig,
        build_retention_plan,
        expire_automatic_backups,
        run_scheduled_catalog_backup,
    )

    wide = _ops_config(tmp_path, keep_auto=30)
    created = []
    for day in range(1, 7):
        created.append(
            run_scheduled_catalog_backup(
                wide,
                now=datetime(2026, 8, day, 3, 17, 0, tzinfo=UTC),
            ).bundle_id
        )
    pinned = wide.backup_root / "pre-deploy-manual-20260801T000000Z"
    create_catalog_backup(wide.database_path, pinned)

    config = CatalogBackupOpsConfig(
        database_path=wide.database_path,
        backup_root=wide.backup_root,
        restore_verify_root=wide.restore_verify_root,
        ops_root=wide.ops_root,
        keep_auto=3,
    )
    plan = build_retention_plan(config, verify=True)
    assert pinned.name not in plan.expire
    assert created[-1] in plan.retain
    assert created[-1] not in plan.expire
    assert len(plan.retain) == 3
    assert set(plan.expire) == set(created[:-3])

    dry = expire_automatic_backups(config, apply=False)
    assert dry["mode"] == "dry-run"
    assert set(dry["expire"]) == set(plan.expire)
    assert pinned.exists()

    applied = expire_automatic_backups(config, apply=True)
    assert set(applied["deleted"]) == set(plan.expire)
    assert pinned.exists()
    assert (config.backup_root / created[-1]).exists()
    assert len([p for p in config.backup_root.iterdir() if p.name.startswith("auto-")]) == 3

    again = expire_automatic_backups(config, apply=True)
    assert again["deleted"] == []


def test_fewer_than_n_eligible_expire_empty(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        build_retention_plan,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path, keep_auto=30)
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    plan = build_retention_plan(config, verify=True)
    assert plan.expire == ()
    assert len(plan.retain) == 1


def test_manual_verify_restore_does_not_masquerade_as_scheduled(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        run_scheduled_catalog_backup,
        verify_restore_bundle,
    )

    config = _ops_config(tmp_path)
    scheduled = run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    before = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    evidence = verify_restore_bundle(config, scheduled.bundle_id, now=datetime(2026, 8, 4, 4, 0, 0, tzinfo=UTC))
    after = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert evidence["operation"] == "verify-restore"
    assert after["last_successful_scheduled_backup"] == before["last_successful_scheduled_backup"]
    assert after["last_manual_verify_restore"]["bundle_id"] == scheduled.bundle_id


def test_symlink_and_root_escape_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        expire_automatic_backups,
        load_catalog_backup_ops_config,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    outside = tmp_path / "outside"
    outside.mkdir()
    link = config.backup_root / "auto-20260101T000000Z-aaaaaaaa"
    link.symlink_to(outside)
    plan_expire = expire_automatic_backups(config, apply=False)
    assert link.name not in plan_expire["expire"]

    with pytest.raises(CatalogBackupOpsError):
        load_catalog_backup_ops_config(
            {
                "FRAMENEST_DATABASE_PATH": str(config.database_path),
                "FRAMENEST_CATALOG_BACKUP_ROOT": "/mnt/umbrel-data/backups",
                "FRAMENEST_CATALOG_RESTORE_VERIFY_ROOT": str(config.restore_verify_root),
                "FRAMENEST_CATALOG_BACKUP_OPS_ROOT": str(config.ops_root),
            }
        )


def test_incomplete_bundle_not_retention_eligible(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        build_retention_plan,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path, keep_auto=3)
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    incomplete = config.backup_root / "auto-20260101T000000Z-bbbbbbbb"
    incomplete.mkdir()
    (incomplete / "catalog.sqlite3").write_bytes(b"x")
    plan = build_retention_plan(config, verify=True)
    assert incomplete.name not in plan.expire
    assert incomplete.name not in plan.retain


def test_flock_conflict_returns_busy(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        operation_lock,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with operation_lock(config) as held:
            assert held
            started.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(timeout=5)
    with pytest.raises(CatalogBackupOpsError, match="in progress") as exc:
        run_scheduled_catalog_backup(config)
    assert exc.value.error_code == "BACKUP_OPS_BUSY"
    release.set()
    thread.join(timeout=5)


def test_pending_cleanup_preserves_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.infrastructure.persistence import catalog_backup_ops as ops
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        derive_restore_readiness,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    original = ops._restore_and_semantic

    def force_pending(*args, **kwargs):  # type: ignore[no-untyped-def]
        semantic, disposable, _pending = original(*args, **kwargs)
        return semantic, disposable, True

    monkeypatch.setattr(ops, "_restore_and_semantic", force_pending)
    with pytest.raises(CatalogBackupOpsError) as exc:
        run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    assert exc.value.error_code == "PENDING_CLEANUP"
    status = json.loads((config.ops_root / "status.json").read_text(encoding="utf-8"))
    assert status["last_successful_scheduled_backup_and_restore"] is not None
    assert status["pending_cleanup"] is not None
    assert derive_restore_readiness(status=status, now=datetime(2026, 8, 4, 4, 0, 0, tzinfo=UTC)) == "ready"


def test_corrupt_and_tampered_manifest_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, verify_catalog_backup
    from kronika.infrastructure.persistence.catalog_backup_ops import run_scheduled_catalog_backup

    config = _ops_config(tmp_path)
    result = run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    bundle = config.backup_root / result.bundle_id
    (bundle / "manifest.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(BackupError):
        verify_catalog_backup(bundle)
