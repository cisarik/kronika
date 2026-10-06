"""Unit tests for mounted-filesystem off-device catalog backup copy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import errno
import json
import os
from pathlib import Path
import shutil
import threading

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


DESTINATION_ID = "0123456789abcdef0123456789abcdef"
OTHER_DESTINATION_ID = "fedcba9876543210fedcba9876543210"


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


def _test_rename_noreplace(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
    os.rename(source, destination)


def _write_marker(
    root: Path,
    destination_id: str = DESTINATION_ID,
    *,
    mode: int = 0o644,
    payload: dict | None = None,
) -> Path:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import MARKER_NAME

    marker = root / MARKER_NAME
    body = payload or {
        "schema_version": 1,
        "purpose": "framenest-catalog-offdevice",
        "destination_id": destination_id,
    }
    marker.write_text(json.dumps(body), encoding="utf-8")
    os.chmod(marker, mode)
    return marker


def _prepare_destination(
    tmp_path: Path,
    *,
    destination_id: str = DESTINATION_ID,
    marker_mode: int = 0o644,
    bundles_mode: int = 0o700,
) -> Path:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import BUNDLES_DIRNAME

    root = tmp_path / "offdevice-root"
    root.mkdir()
    _write_marker(root, destination_id, mode=marker_mode)
    bundles = root / BUNDLES_DIRNAME
    bundles.mkdir(mode=bundles_mode)
    os.chmod(bundles, bundles_mode)
    return root


def _hooks(
    destination_root: Path,
    *,
    is_mountpoint: bool = True,
    same_device: bool = False,
    rename_noreplace=None,
    fsync=None,
    marker_uid_allowed=None,
    bundles_uid_allowed=None,
):
    from kronika.infrastructure.persistence.catalog_backup_offdevice import OffdeviceOsHooks

    dest_resolved = destination_root.resolve()

    def _device_id(path: Path) -> int:
        if same_device:
            return 42
        if Path(path).resolve() == dest_resolved:
            return 101
        return 202

    return OffdeviceOsHooks(
        is_mountpoint=lambda _path: is_mountpoint,
        device_id=_device_id,
        rename_noreplace=rename_noreplace or _test_rename_noreplace,
        fsync=fsync or os.fsync,
        marker_uid_allowed=marker_uid_allowed or (lambda uid: uid == os.getuid()),
        bundles_uid_allowed=bundles_uid_allowed or (lambda uid: uid == os.getuid()),
    )


def _environ(destination_id: str = DESTINATION_ID) -> dict[str, str]:
    return {"FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": destination_id}


def _seed_scheduled(tmp_path: Path, *, day: int = 4):
    from kronika.infrastructure.persistence.catalog_backup_ops import run_scheduled_catalog_backup

    config = _ops_config(tmp_path)
    result = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, day, 3, 17, 0, tzinfo=UTC),
    )
    return config, result


# --- Destination configuration and gate failures ---


def test_parse_configured_destination_id_disabled_and_invalid() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        parse_configured_destination_id,
    )

    assert parse_configured_destination_id({}) is None
    assert parse_configured_destination_id({"FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": ""}) is None
    assert parse_configured_destination_id(_environ()) == DESTINATION_ID
    with pytest.raises(OffdeviceError) as exc:
        parse_configured_destination_id({"FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": "not-hex"})
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_ID_INVALID"
    with pytest.raises(OffdeviceError) as exc:
        parse_configured_destination_id(
            {"FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID": "0123456789ABCDEF0123456789ABCDEF"}
        )
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_ID_INVALID"


def test_run_offdevice_disabled_without_destination_id(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import OffdeviceError
    from kronika.infrastructure.persistence.catalog_backup_ops import run_offdevice_catalog_copy

    config, _ = _seed_scheduled(tmp_path)
    with pytest.raises(OffdeviceError) as exc:
        run_offdevice_catalog_copy(config, environ={})
    assert exc.value.error_code == "OFFDEVICE_DISABLED"


def test_destination_unavailable_when_missing(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    missing = tmp_path / "missing-offdevice"
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=missing,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=tmp_path / "catalog-backups",
            hooks=_hooks(missing),
        )
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_UNAVAILABLE"


def test_destination_not_mount(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root, is_mountpoint=False),
        )
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_NOT_MOUNT"


def test_destination_same_device(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root, same_device=True),
        )
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_SAME_DEVICE"


def test_marker_missing(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        MARKER_NAME,
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = tmp_path / "offdevice-root"
    root.mkdir()
    (root / BUNDLES_DIRNAME).mkdir(mode=0o700)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    assert not (root / MARKER_NAME).exists()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_INVALID"


def test_marker_symlink_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        MARKER_NAME,
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = tmp_path / "offdevice-root"
    root.mkdir()
    target = tmp_path / "marker-target.json"
    target.write_text("{}", encoding="utf-8")
    (root / MARKER_NAME).symlink_to(target)
    (root / BUNDLES_DIRNAME).mkdir(mode=0o700)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_INVALID"


def test_marker_malformed_json(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        MARKER_NAME,
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    (root / MARKER_NAME).write_text("{not-json", encoding="utf-8")
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_MALFORMED"


def test_marker_malformed_extra_keys(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    _write_marker(
        root,
        payload={
            "schema_version": 1,
            "purpose": "framenest-catalog-offdevice",
            "destination_id": DESTINATION_ID,
            "extra": True,
        },
    )
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_MALFORMED"


def test_marker_purpose_mismatch(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    _write_marker(
        root,
        payload={
            "schema_version": 1,
            "purpose": "something-else",
            "destination_id": DESTINATION_ID,
        },
    )
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_PURPOSE_MISMATCH"


def test_marker_id_invalid(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    _write_marker(
        root,
        payload={
            "schema_version": 1,
            "purpose": "framenest-catalog-offdevice",
            "destination_id": "short",
        },
    )
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_ID_INVALID"


def test_destination_id_mismatch(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path, destination_id=OTHER_DESTINATION_ID)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_ID_MISMATCH"


def test_marker_unsafe_mode(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path, marker_mode=0o666)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_UNSAFE"


def test_marker_unsafe_uid(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root, marker_uid_allowed=lambda _uid: False),
        )
    assert exc.value.error_code == "OFFDEVICE_MARKER_UNSAFE"


def test_bundles_missing(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = tmp_path / "offdevice-root"
    root.mkdir()
    _write_marker(root)
    assert not (root / BUNDLES_DIRNAME).exists()
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_BUNDLES_UNSAFE"


def test_bundles_symlink_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = tmp_path / "offdevice-root"
    root.mkdir()
    _write_marker(root)
    outside = tmp_path / "outside-bundles"
    outside.mkdir()
    (root / BUNDLES_DIRNAME).symlink_to(outside)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_BUNDLES_UNSAFE"


def test_bundles_world_writable_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path, bundles_mode=0o777)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root),
        )
    assert exc.value.error_code == "OFFDEVICE_BUNDLES_UNSAFE"


def test_bundles_uid_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    with pytest.raises(OffdeviceError) as exc:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(root, bundles_uid_allowed=lambda _uid: False),
        )
    assert exc.value.error_code == "OFFDEVICE_BUNDLES_UNSAFE"


def test_validate_destination_success(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        validate_offdevice_destination,
    )

    root = _prepare_destination(tmp_path)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    validated = validate_offdevice_destination(
        destination_root=root,
        configured_destination_id=DESTINATION_ID,
        local_backup_root=backup_root,
        hooks=_hooks(root),
    )
    assert validated.root == root
    assert validated.bundles_dir == root / BUNDLES_DIRNAME
    assert validated.destination_id == DESTINATION_ID


# --- Publish, reuse, conflict, injection failures ---


def test_successful_copy_idempotent_reuse_and_conflict(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        OffdeviceError,
    )
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        run_offdevice_catalog_copy,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    first = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
    )
    second = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 5, 3, 17, 0, tzinfo=UTC),
    )
    assert first.bundle_id != second.bundle_id

    dest = _prepare_destination(tmp_path)
    hooks = _hooks(dest)
    env = _environ()

    copied = run_offdevice_catalog_copy(
        config,
        now=datetime(2026, 8, 5, 4, 17, 0, tzinfo=UTC),
        hooks=hooks,
        destination_root=dest,
        environ=env,
    )
    assert copied.reused_existing is False
    assert copied.bundle_id == second.bundle_id
    assert copied.pending_cleanup is False
    final = dest / BUNDLES_DIRNAME / second.bundle_id
    assert (final / "manifest.json").is_file()
    assert (final / "catalog.sqlite3").is_file()

    reused = run_offdevice_catalog_copy(
        config,
        now=datetime(2026, 8, 5, 4, 20, 0, tzinfo=UTC),
        hooks=hooks,
        destination_root=dest,
        environ=env,
    )
    assert reused.reused_existing is True
    assert reused.bundle_id == second.bundle_id

    # Corrupt the published final so identity no longer matches the source.
    catalog = final / "catalog.sqlite3"
    catalog.write_bytes(catalog.read_bytes() + b"\x00")
    with pytest.raises(OffdeviceError) as exc:
        run_offdevice_catalog_copy(
            config,
            now=datetime(2026, 8, 5, 4, 25, 0, tzinfo=UTC),
            hooks=hooks,
            destination_root=dest,
            environ=env,
        )
    assert exc.value.error_code == "OFFDEVICE_COPY_CONFLICT"


def test_publish_rename_enosys_maps_to_unsupported(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        capture_bundle_identity,
        publish_or_reuse_offdevice_bundle,
        validate_offdevice_destination,
    )
    from kronika.infrastructure.persistence.catalog_backup_ops import run_scheduled_catalog_backup

    config = _ops_config(tmp_path)
    scheduled = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
    )
    source = config.backup_root / scheduled.bundle_id
    identity = capture_bundle_identity(source)
    dest = _prepare_destination(tmp_path)

    def boom_rename(_source: Path, _destination: Path) -> None:
        raise OSError(errno.ENOSYS, "renameat2 unsupported")

    destination = validate_offdevice_destination(
        destination_root=dest,
        configured_destination_id=DESTINATION_ID,
        local_backup_root=config.backup_root,
        hooks=_hooks(dest),
    )
    with pytest.raises(OffdeviceError) as exc:
        publish_or_reuse_offdevice_bundle(
            source_bundle=source,
            source_identity=identity,
            destination=destination,
            hooks=_hooks(dest, rename_noreplace=boom_rename),
        )
    assert exc.value.error_code == "OFFDEVICE_ATOMIC_PUBLISH_UNSUPPORTED"


def test_publish_rename_enospc_maps_to_destination_full(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        capture_bundle_identity,
        publish_or_reuse_offdevice_bundle,
        validate_offdevice_destination,
    )
    from kronika.infrastructure.persistence.catalog_backup_ops import run_scheduled_catalog_backup

    config = _ops_config(tmp_path)
    scheduled = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
    )
    source = config.backup_root / scheduled.bundle_id
    identity = capture_bundle_identity(source)
    dest = _prepare_destination(tmp_path)

    def full_rename(_source: Path, _destination: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    destination = validate_offdevice_destination(
        destination_root=dest,
        configured_destination_id=DESTINATION_ID,
        local_backup_root=config.backup_root,
        hooks=_hooks(dest),
    )
    with pytest.raises(OffdeviceError) as exc:
        publish_or_reuse_offdevice_bundle(
            source_bundle=source,
            source_identity=identity,
            destination=destination,
            hooks=_hooks(dest, rename_noreplace=full_rename),
        )
    assert exc.value.error_code == "OFFDEVICE_DESTINATION_FULL"


def test_fsync_failure_during_copy(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        capture_bundle_identity,
        publish_or_reuse_offdevice_bundle,
        validate_offdevice_destination,
    )
    from kronika.infrastructure.persistence.catalog_backup_ops import run_scheduled_catalog_backup

    config = _ops_config(tmp_path)
    scheduled = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
    )
    source = config.backup_root / scheduled.bundle_id
    identity = capture_bundle_identity(source)
    dest = _prepare_destination(tmp_path)

    def boom_fsync(_fd: int) -> None:
        raise OSError(errno.EIO, "I/O error")

    destination = validate_offdevice_destination(
        destination_root=dest,
        configured_destination_id=DESTINATION_ID,
        local_backup_root=config.backup_root,
        hooks=_hooks(dest),
    )
    with pytest.raises(OffdeviceError) as exc:
        publish_or_reuse_offdevice_bundle(
            source_bundle=source,
            source_identity=identity,
            destination=destination,
            hooks=_hooks(dest, fsync=boom_fsync),
        )
    assert exc.value.error_code == "OFFDEVICE_COPY_FAILED"


def test_staging_cleanup_only_owned_stages(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        STAGE_PREFIX,
        OffdeviceError,
        capture_bundle_identity,
        publish_or_reuse_offdevice_bundle,
        validate_offdevice_destination,
    )
    from kronika.infrastructure.persistence.catalog_backup_ops import run_scheduled_catalog_backup

    config = _ops_config(tmp_path)
    scheduled = run_scheduled_catalog_backup(
        config,
        now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC),
    )
    source = config.backup_root / scheduled.bundle_id
    identity = capture_bundle_identity(source)
    dest = _prepare_destination(tmp_path)
    bundles = dest / BUNDLES_DIRNAME

    foreign = bundles / "operator-owned-keep-me"
    foreign.mkdir(mode=0o700)
    (foreign / "note.txt").write_text("keep", encoding="utf-8")

    stray_stage = bundles / f"{STAGE_PREFIX}leftover.deadbeef"
    stray_stage.mkdir(mode=0o700)
    (stray_stage / "manifest.json").write_text("{}", encoding="utf-8")

    def boom_rename(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EIO, "publish failed")

    destination = validate_offdevice_destination(
        destination_root=dest,
        configured_destination_id=DESTINATION_ID,
        local_backup_root=config.backup_root,
        hooks=_hooks(dest),
    )
    with pytest.raises(OffdeviceError) as exc:
        publish_or_reuse_offdevice_bundle(
            source_bundle=source,
            source_identity=identity,
            destination=destination,
            hooks=_hooks(dest, rename_noreplace=boom_rename),
        )
    assert exc.value.error_code == "OFFDEVICE_PUBLISH_FAILED"

    remaining = {path.name for path in bundles.iterdir()}
    assert "operator-owned-keep-me" in remaining
    assert foreign.exists()
    assert (foreign / "note.txt").read_text(encoding="utf-8") == "keep"
    assert stray_stage.name in remaining
    # Failed attempt stage for this publish must be cleaned; unrelated objects remain.
    assert not any(
        name.startswith(f"{STAGE_PREFIX}{scheduled.bundle_id}.") for name in remaining
    )


# --- Readiness, status sanitization, retention, lock ---


def test_derive_offdevice_readiness_states() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        derive_offdevice_readiness,
    )

    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    assert (
        derive_offdevice_readiness(
            configured=False,
            status={},
            destination_health="ok",
            now=now,
        )
        == "disabled"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status={"current_operation": "run-offdevice"},
            destination_health="ok",
            now=now,
        )
        == "busy"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status={},
            destination_health="ok",
            now=now,
            lock_held_elsewhere=True,
        )
        == "busy"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status={"current_operation": "run-scheduled"},
            destination_health="ok",
            now=now,
        )
        == "busy"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status={},
            destination_health="missing",
            now=now,
        )
        == "unavailable"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status={},
            destination_health="ok",
            now=now,
        )
        == "never_verified"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status={"last_offdevice_attempt": {"state": "failed"}},
            destination_health="ok",
            now=now,
        )
        == "failed"
    )
    success = {
        "last_successful_offdevice_copy_and_restore": {
            "completed_at_utc": "2026-08-08T12:00:00Z",
            "attempt_seq": 1,
        }
    }
    assert (
        derive_offdevice_readiness(
            configured=True,
            status=success,
            destination_health="ok",
            now=now,
        )
        == "ready"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status=success,
            destination_health="ok",
            now=now + timedelta(hours=48),
        )
        == "ready"
    )
    assert (
        derive_offdevice_readiness(
            configured=True,
            status=success,
            destination_health="ok",
            now=now + timedelta(hours=48, seconds=1),
        )
        == "stale"
    )
    failed_after = {
        **success,
        "last_offdevice_attempt": {
            "state": "failed",
            "completed_at_utc": "2026-08-08T12:00:00Z",
            "attempt_seq": 2,
        },
    }
    assert (
        derive_offdevice_readiness(
            configured=True,
            status=failed_after,
            destination_health="ok",
            now=now,
        )
        == "failed"
    )


def test_sanitized_offdevice_status_omits_sensitive_fields(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        DEFAULT_OFFDEVICE_ROOT,
        build_sanitized_offdevice_status,
        derive_offdevice_readiness,
    )

    status = {
        "last_successful_offdevice_copy_and_restore": {
            "bundle_id": "auto-20260804T031700Z-deadbeef",
            "completed_at_utc": "2026-08-08T04:17:00Z",
            "catalog_sha256": "a" * 64,
            "alembic_revision": "0001",
            "catalog_size_bytes": 12,
            "reused_existing": False,
            "attempt_seq": 1,
            "semantic": {"alembic_revision": "0001", "counts": {}},
            "destination_id": DESTINATION_ID,
            "path": str(DEFAULT_OFFDEVICE_ROOT),
        },
        "last_offdevice_attempt": {
            "state": "succeeded",
            "operation": "run-offdevice",
            "bundle_id": "auto-20260804T031700Z-deadbeef",
            "attempt_seq": 1,
            "started_at_utc": "2026-08-08T04:17:00Z",
            "completed_at_utc": "2026-08-08T04:17:00Z",
        },
        "offdevice_pending_cleanup": None,
    }
    readiness = derive_offdevice_readiness(
        configured=True,
        status=status,
        destination_health="ok",
        now=datetime(2026, 8, 8, 5, 0, 0, tzinfo=UTC),
    )
    payload = build_sanitized_offdevice_status(
        configured=True,
        readiness=readiness,
        destination_health="ok",
        status=status,
        local_recovery_point={
            "bundle_id": "auto-20260804T031700Z-deadbeef",
            "catalog_sha256": "a" * 64,
            "alembic_revision": "0001",
            "catalog_size_bytes": 12,
            "completed_at_utc": "2026-08-08T03:17:00Z",
            "destination_id": DESTINATION_ID,
        },
    )
    encoded = json.dumps(payload)
    assert str(DEFAULT_OFFDEVICE_ROOT) not in encoded
    assert "/mnt/" not in encoded
    assert DESTINATION_ID not in encoded
    assert "destination_id" not in encoded
    assert payload["configured"] is True
    assert payload["readiness"] == "ready"
    assert payload["destination_health"] == "ok"
    assert payload["last_successful_copy_and_restore"]["bundle_id"] == (
        "auto-20260804T031700Z-deadbeef"
    )


def test_operator_status_off_device_block(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import DEFAULT_OFFDEVICE_ROOT
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        read_operator_status,
        run_offdevice_catalog_copy,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    disabled = read_operator_status(config, offdevice_environ={})
    assert disabled["off_device"]["configured"] is False
    assert disabled["off_device"]["readiness"] == "disabled"

    dest = _prepare_destination(tmp_path)
    hooks = _hooks(dest)
    env = _environ()
    run_offdevice_catalog_copy(
        config,
        now=datetime(2026, 8, 4, 4, 17, 0, tzinfo=UTC),
        hooks=hooks,
        destination_root=dest,
        environ=env,
    )
    status = read_operator_status(
        config,
        now=datetime(2026, 8, 4, 5, 0, 0, tzinfo=UTC),
        offdevice_hooks=hooks,
        offdevice_destination_root=dest,
        offdevice_environ=env,
    )
    encoded = json.dumps(status)
    assert str(DEFAULT_OFFDEVICE_ROOT) not in encoded
    assert DESTINATION_ID not in encoded
    assert status["off_device"]["configured"] is True
    assert status["off_device"]["readiness"] == "ready"
    assert status["off_device"]["destination_health"] == "ok"


def test_retention_plan_ignores_offdevice_bundles(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import BUNDLES_DIRNAME
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        build_retention_plan,
        run_offdevice_catalog_copy,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path, keep_auto=3)
    created = []
    for day in range(1, 5):
        created.append(
            run_scheduled_catalog_backup(
                config,
                now=datetime(2026, 8, day, 3, 17, 0, tzinfo=UTC),
            ).bundle_id
        )
    dest = _prepare_destination(tmp_path)
    hooks = _hooks(dest)
    env = _environ()
    run_offdevice_catalog_copy(
        config,
        now=datetime(2026, 8, 5, 4, 17, 0, tzinfo=UTC),
        hooks=hooks,
        destination_root=dest,
        environ=env,
    )
    offdevice_names = {path.name for path in (dest / BUNDLES_DIRNAME).iterdir()}
    assert created[-1] in offdevice_names

    plan = build_retention_plan(config, verify=True)
    assert all(not str(item).startswith(str(dest)) for item in plan.expire)
    assert all(not str(item).startswith(str(dest)) for item in plan.retain)
    assert created[-1] not in plan.expire
    assert set(plan.expire).isdisjoint(offdevice_names - set(created))
    # Off-device copy must not expand local retention eligibility.
    assert set(plan.retain) | set(plan.expire) <= set(created)
    assert (dest / BUNDLES_DIRNAME / created[-1]).exists()


def test_shared_lock_busy_for_offdevice(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        operation_lock,
        run_offdevice_catalog_copy,
        run_scheduled_catalog_backup,
    )

    config = _ops_config(tmp_path)
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 4, 3, 17, 0, tzinfo=UTC))
    dest = _prepare_destination(tmp_path)
    hooks = _hooks(dest)
    env = _environ()

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
    with pytest.raises(CatalogBackupOpsError) as exc:
        run_offdevice_catalog_copy(
            config,
            hooks=hooks,
            destination_root=dest,
            environ=env,
        )
    assert exc.value.error_code == "BACKUP_OPS_BUSY"
    release.set()
    thread.join(timeout=5)


def test_inspect_destination_health_mapping(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        inspect_destination_health,
    )

    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()
    assert (
        inspect_destination_health(
            configured=False,
            configured_destination_id=None,
            local_backup_root=backup_root,
        )
        == "unconfigured"
    )
    missing = tmp_path / "missing"
    assert (
        inspect_destination_health(
            configured=True,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(missing),
            destination_root=missing,
        )
        == "missing"
    )
    dest = _prepare_destination(tmp_path)
    assert (
        inspect_destination_health(
            configured=True,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(dest, same_device=True),
            destination_root=dest,
        )
        == "unsafe"
    )
    assert (
        inspect_destination_health(
            configured=True,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_hooks(dest),
            destination_root=dest,
        )
        == "ok"
    )


def test_default_offdevice_root_constant() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        BUNDLES_DIRNAME,
        DEFAULT_OFFDEVICE_ROOT,
        MARKER_NAME,
    )

    assert DEFAULT_OFFDEVICE_ROOT == Path("/mnt/framenest-catalog-offdevice")
    assert MARKER_NAME == ".kronika-catalog-offdevice.json"
    assert BUNDLES_DIRNAME == "bundles"
