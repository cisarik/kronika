"""Contract tests for the bounded identity-path migration.

Every fixture here is synthetic and lives under the pytest temporary
directory: the migration must never need the Cooperator's real development
state, AI configuration, capture state, profiles or credentials to be proved
correct, and these tests never read them.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import stat
from unittest import mock

import pytest

from kronika.configuration import KronikaSettings, resolved_runtime_settings_path
from kronika.infrastructure.ai.configuration import (
    default_ai_config_path,
    default_ai_status_snapshot_path,
    default_ai_test_state_path,
)
from kronika.infrastructure.runtime import local_state_migration as migration_module
from kronika.infrastructure.runtime.development import (
    RuntimeStatus,
    resolve_development_paths,
)
from kronika.infrastructure.runtime.local_state_migration import (
    ALREADY_VERIFIED_STATUS,
    COPIED_VERIFIED_STATUS,
    COPY_READY_STATUS,
    COPY_REFUSED_STATUS,
    DESTINATION_OCCUPIED_REFUSED_STATUS,
    NO_DURABLE_CONTENT_STATUS,
    OVERRIDE_STATUS,
    SOURCE_ABSENT_STATUS,
    LegacyStateReplacementRefusal,
    LocalStateMigration,
    LocalStateMigrationError,
    LocalStateMigrationIdentityError,
    ManagedDevelopmentServerActiveError,
    MigrationPlan,
    build_migration_plan,
    refuse_silent_legacy_state_replacement,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_MACOS_CLASS_KEYS = frozenset(
    {
        "macos_development_database",
        "macos_development_runtime",
        "macos_development_logs",
        "macos_development_runtime_settings",
        "macos_ai_configuration",
        "macos_ai_test_state",
        "macos_ai_status_snapshot",
        "temporary_development_database",
        "temporary_development_runtime_settings",
        "temporary_development_gallery_previews",
        "temporary_development_covers",
        "temporary_development_cover_thumbnails",
    }
)

_XDG_CLASS_KEYS = frozenset(
    {
        "xdg_development_database",
        "xdg_development_runtime",
        "xdg_development_logs",
        "xdg_development_runtime_settings",
        "xdg_ai_configuration",
        "xdg_ai_test_state",
        "xdg_ai_status_snapshot",
        "temporary_development_database",
        "temporary_development_runtime_settings",
        "temporary_development_gallery_previews",
        "temporary_development_covers",
        "temporary_development_cover_thumbnails",
    }
)


def _resolved(path: Path) -> Path:
    return path.resolve(strict=False)


class _StubRuntime:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            kind=self._kind,  # type: ignore[arg-type]
            url=None,
            pid=None,
            database_state="unrelated",
            log_available=False,
            message="stub",
        )


def _runtime_factory(kind: str):
    def factory(**kwargs: object) -> _StubRuntime:
        return _StubRuntime(kind)

    return factory


def _linux_layout(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "xdg-data"
    state_root = tmp_path / "xdg-state"
    config_root = tmp_path / "xdg-config"
    return {
        "home": tmp_path / "home",
        "temp_root": tmp_path / "temp-root",
        "data": data_root,
        "state": state_root,
        "config": config_root,
        "development": data_root / "FrameNest" / "development",
        "runtime": state_root / "FrameNest" / "development" / "runtime",
        "logs": state_root / "FrameNest" / "development" / "logs",
        "ai": config_root / "framenest" / "ai",
        "temp_development": tmp_path / "temp-root" / "framenest-development",
    }


def _macos_layout(tmp_path: Path) -> dict[str, Path]:
    application_support = tmp_path / "home" / "Library" / "Application Support"
    return {
        "home": application_support.parents[1],
        "temp_root": tmp_path / "temp-root",
        "development": application_support / "FrameNest" / "development",
        "logs": tmp_path / "home" / "Library" / "Logs" / "FrameNest" / "development",
        "ai": application_support / "FrameNest" / "ai",
        "temp_development": tmp_path / "temp-root" / "framenest-development",
    }


def _xdg_environ(layout: dict[str, Path]) -> dict[str, str]:
    return {
        "XDG_DATA_HOME": str(layout["data"]),
        "XDG_STATE_HOME": str(layout["state"]),
        "XDG_CONFIG_HOME": str(layout["config"]),
    }


def _migration(
    tmp_path: Path,
    *,
    platform_name: str = "linux",
    environ: dict[str, str] | None = None,
    runtime_kind: str = "stopped",
    home: Path | None = None,
    temp_root: Path | None = None,
    receipt_dir: Path | None = None,
) -> LocalStateMigration:
    return LocalStateMigration(
        environ={} if environ is None else environ,
        platform_name=platform_name,
        home=tmp_path / "home" if home is None else home,
        temp_root=tmp_path / "temp-root" if temp_root is None else temp_root,
        receipt_dir=tmp_path / "receipts" if receipt_dir is None else receipt_dir,
        runtime_factory=_runtime_factory(runtime_kind),
    )


def _outcome(report, key: str):
    matches = [outcome for outcome in report.outcomes if outcome.key == key]
    assert len(matches) == 1
    return matches[0]


def _write_file(path: Path, content: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.chmod(path, mode)
    return path


def _digest(path: Path) -> tuple[int, int, int]:
    path_stat = path.stat()
    return (path_stat.st_ino, path_stat.st_mtime_ns, path_stat.st_size)


def _make_database(path: Path, *, rows: int = 3, wal: bool = False):
    """Create a synthetic migrated catalog with the application's private modes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    connection = sqlite3.connect(str(path))
    if wal:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute(
        "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    connection.execute("DELETE FROM alembic_version")
    connection.execute("INSERT INTO alembic_version (version_num) VALUES ('0035')")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
    )
    connection.executemany(
        "INSERT INTO items (name) VALUES (?)",
        [(f"row-{index}",) for index in range(rows)],
    )
    connection.commit()
    os.chmod(path, 0o600)
    for suffix in ("-wal", "-shm"):
        auxiliary = Path(f"{path}{suffix}")
        if auxiliary.exists():
            os.chmod(auxiliary, 0o600)
    return connection


# ---------------------------------------------------------------------------
# Mapping matrix
# ---------------------------------------------------------------------------


def test_macos_plan_covers_exactly_the_owned_classes_and_brand_components(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    temp_root = tmp_path / "temp-root"
    plan = build_migration_plan(
        environ={}, platform_name="darwin", home=home, temp_root=temp_root
    )

    assert {entry.key for entry in plan.entries} == _MACOS_CLASS_KEYS
    database = plan.by_key("macos_development_database")
    assert database.source == _resolved(
        home / "Library" / "Application Support" / "FrameNest" / "development" / "catalog.sqlite3"
    )
    assert database.destination == _resolved(
        home / "Library" / "Application Support" / "Kronika" / "development" / "catalog.sqlite3"
    )
    runtime = plan.by_key("macos_development_runtime")
    assert runtime.source == _resolved(
        home / "Library" / "Application Support" / "FrameNest" / "development" / "runtime"
    )
    assert runtime.destination == _resolved(
        home / "Library" / "Application Support" / "Kronika" / "development" / "runtime"
    )
    assert runtime.excluded_names == ("server-state.json", "operation.lock")
    logs = plan.by_key("macos_development_logs")
    assert logs.source == _resolved(home / "Library" / "Logs" / "FrameNest" / "development")
    assert logs.destination == _resolved(
        home / "Library" / "Logs" / "Kronika" / "development"
    )
    settings = plan.by_key("macos_development_runtime_settings")
    assert settings.source == _resolved(
        home / "Library" / "Application Support" / "FrameNest" / "development" / "runtime-settings.json"
    )
    assert settings.destination == _resolved(
        home / "Library" / "Application Support" / "Kronika" / "development" / "runtime-settings.json"
    )
    ai = plan.by_key("macos_ai_configuration")
    assert ai.source == home / "Library" / "Application Support" / "FrameNest" / "ai" / "config.json"
    assert ai.destination == home / "Library" / "Application Support" / "Kronika" / "ai" / "config.json"
    assert plan.by_key("macos_ai_test_state").destination == (
        home / "Library" / "Application Support" / "Kronika" / "ai" / "test-state.json"
    )
    assert plan.by_key("macos_ai_status_snapshot").destination == (
        home / "Library" / "Application Support" / "Kronika" / "ai" / "status-snapshot.json"
    )


def test_xdg_plan_uses_xdg_roots_and_the_lowercase_ai_directory(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    plan = build_migration_plan(
        environ=_xdg_environ(layout),
        platform_name="linux",
        home=layout["home"],
        temp_root=layout["temp_root"],
    )

    assert {entry.key for entry in plan.entries} == _XDG_CLASS_KEYS
    database = plan.by_key("xdg_development_database")
    assert database.source == _resolved(
        layout["data"] / "FrameNest" / "development" / "catalog.sqlite3"
    )
    assert database.destination == _resolved(
        layout["data"] / "Kronika" / "development" / "catalog.sqlite3"
    )
    runtime = plan.by_key("xdg_development_runtime")
    assert runtime.source == _resolved(
        layout["state"] / "FrameNest" / "development" / "runtime"
    )
    assert runtime.destination == _resolved(
        layout["state"] / "Kronika" / "development" / "runtime"
    )
    assert runtime.excluded_names == ("server-state.json", "operation.lock")
    logs = plan.by_key("xdg_development_logs")
    assert logs.source == _resolved(
        layout["state"] / "FrameNest" / "development" / "logs"
    )
    assert logs.destination == _resolved(
        layout["state"] / "Kronika" / "development" / "logs"
    )
    ai = plan.by_key("xdg_ai_configuration")
    assert ai.source == _resolved(layout["config"]) / "framenest" / "ai" / "config.json"
    assert ai.destination == _resolved(layout["config"]) / "kronika" / "ai" / "config.json"
    assert plan.by_key("xdg_ai_test_state").destination == (
        _resolved(layout["config"]) / "kronika" / "ai" / "test-state.json"
    )
    assert plan.by_key("xdg_ai_status_snapshot").destination == (
        _resolved(layout["config"]) / "kronika" / "ai" / "status-snapshot.json"
    )


def test_xdg_plan_falls_back_to_home_relative_roots_without_xdg_variables(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    plan = build_migration_plan(
        environ={}, platform_name="linux", home=home, temp_root=tmp_path / "temp-root"
    )

    assert plan.by_key("xdg_development_database").source == _resolved(
        home / ".local" / "share" / "FrameNest" / "development" / "catalog.sqlite3"
    )
    assert plan.by_key("xdg_development_runtime").source == _resolved(
        home / ".local" / "state" / "FrameNest" / "development" / "runtime"
    )
    assert plan.by_key("xdg_development_logs").source == _resolved(
        home / ".local" / "state" / "FrameNest" / "development" / "logs"
    )
    assert plan.by_key("xdg_ai_configuration").source == (
        home / ".config" / "framenest" / "ai" / "config.json"
    )


def test_temporary_root_covers_database_settings_previews_covers_and_thumbnails(
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "temp-root"
    plan = build_migration_plan(
        environ={}, platform_name="linux", home=tmp_path / "home", temp_root=temp_root
    )
    legacy_root = _resolved(temp_root) / "framenest-development"
    canonical_root = _resolved(temp_root) / "kronika-development"

    assert plan.by_key("temporary_development_database").source == (
        legacy_root / "catalog.sqlite3"
    )
    assert plan.by_key("temporary_development_database").destination == (
        canonical_root / "catalog.sqlite3"
    )
    assert plan.by_key("temporary_development_runtime_settings").source == (
        legacy_root / "runtime-settings.json"
    )
    assert plan.by_key("temporary_development_runtime_settings").destination == (
        canonical_root / "runtime-settings.json"
    )
    assert plan.by_key("temporary_development_gallery_previews").source == (
        legacy_root / "gallery-previews"
    )
    assert plan.by_key("temporary_development_gallery_previews").destination == (
        canonical_root / "gallery-previews"
    )
    assert plan.by_key("temporary_development_covers").source == legacy_root / "covers"
    assert plan.by_key("temporary_development_covers").destination == (
        canonical_root / "covers"
    )
    assert plan.by_key("temporary_development_cover_thumbnails").source == (
        legacy_root / "cover-thumbnails"
    )
    assert plan.by_key("temporary_development_cover_thumbnails").destination == (
        canonical_root / "cover-thumbnails"
    )
    for entry in plan.entries:
        assert entry.source != entry.destination


def test_plan_sources_still_equal_the_public_default_functions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defaults did not move: the plan's legacy side is the live default."""
    home = tmp_path / "home"
    temp_root = tmp_path / "temp-root"

    darwin_plan = build_migration_plan(
        environ={}, platform_name="darwin", home=home, temp_root=temp_root
    )
    darwin_paths = resolve_development_paths(
        environ={}, platform_name="darwin", home=home
    )
    assert darwin_plan.by_key("macos_development_database").source == (
        darwin_paths.database_path
    )
    assert darwin_plan.by_key("macos_development_runtime").source == (
        darwin_paths.runtime_dir
    )
    assert darwin_plan.by_key("macos_development_logs").source == (
        darwin_paths.log_path.parent
    )
    darwin_ai = default_ai_config_path({}, platform="darwin", home=home)
    assert darwin_plan.by_key("macos_ai_configuration").source == darwin_ai
    assert darwin_plan.by_key("macos_ai_test_state").source == (
        default_ai_test_state_path(darwin_ai)
    )
    assert darwin_plan.by_key("macos_ai_status_snapshot").source == (
        default_ai_status_snapshot_path(darwin_ai)
    )
    assert darwin_plan.by_key("macos_ai_configuration").destination != darwin_ai

    xdg_environ = {
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
    }
    xdg_plan = build_migration_plan(
        environ=xdg_environ, platform_name="linux", home=home, temp_root=temp_root
    )
    xdg_paths = resolve_development_paths(
        environ=xdg_environ, platform_name="linux", home=home
    )
    assert xdg_plan.by_key("xdg_development_database").source == xdg_paths.database_path
    assert xdg_plan.by_key("xdg_development_runtime").source == xdg_paths.runtime_dir
    assert xdg_plan.by_key("xdg_development_logs").source == xdg_paths.log_path.parent
    xdg_ai = default_ai_config_path(
        xdg_environ, platform="linux", home=home
    )
    assert xdg_plan.by_key("xdg_ai_configuration").source == xdg_ai
    assert xdg_plan.by_key("xdg_ai_test_state").source == default_ai_test_state_path(
        xdg_ai
    )
    assert xdg_plan.by_key("xdg_ai_status_snapshot").source == (
        default_ai_status_snapshot_path(xdg_ai)
    )
    assert xdg_plan.by_key("xdg_ai_configuration").destination != xdg_ai
    assert xdg_ai.parent.parent.name == "framenest"
    assert xdg_plan.by_key("xdg_ai_configuration").destination.parent.parent.name == (
        "kronika"
    )

    monkeypatch.setattr(
        migration_module.tempfile, "gettempdir", lambda: str(temp_root)
    )
    with mock.patch.dict(os.environ, {}, clear=True):
        settings = KronikaSettings(_env_file=None)
    assert settings.database_path == (
        build_migration_plan(
            environ={}, platform_name="linux", home=home, temp_root=temp_root
        )
        .by_key("temporary_development_database")
        .source
    )
    assert settings.gallery_preview_cache_path == (
        build_migration_plan(
            environ={}, platform_name="linux", home=home, temp_root=temp_root
        )
        .by_key("temporary_development_gallery_previews")
        .source
    )
    assert settings.cover_storage_root == (
        build_migration_plan(
            environ={}, platform_name="linux", home=home, temp_root=temp_root
        )
        .by_key("temporary_development_covers")
        .source
    )
    assert settings.cover_thumbnail_cache_path == (
        build_migration_plan(
            environ={}, platform_name="linux", home=home, temp_root=temp_root
        )
        .by_key("temporary_development_cover_thumbnails")
        .source
    )
    assert resolved_runtime_settings_path(settings) == (
        build_migration_plan(
            environ={}, platform_name="linux", home=home, temp_root=temp_root
        )
        .by_key("temporary_development_runtime_settings")
        .source
    )


def test_plan_validation_fails_loudly_on_duplication_and_ambiguity(
    tmp_path: Path,
) -> None:
    valid = build_migration_plan(
        environ={},
        platform_name="linux",
        home=Path("/synthetic/home"),
        temp_root=Path("/synthetic/tmp"),
    )

    duplicate_key_entries = tuple(
        replace(entry, key=valid.entries[0].key)
        if entry.key == "xdg_development_logs"
        else entry
        for entry in valid.entries
    )
    with pytest.raises(LocalStateMigrationError, match="class key"):
        MigrationPlan(platform="linux", entries=duplicate_key_entries).validate()

    duplicate_source_entries = tuple(
        replace(entry, source=valid.entries[0].source)
        if entry.key == "xdg_development_logs"
        else entry
        for entry in valid.entries
    )
    with pytest.raises(LocalStateMigrationError, match="source location"):
        MigrationPlan(platform="linux", entries=duplicate_source_entries).validate()

    duplicate_destination_entries = tuple(
        replace(entry, destination=valid.entries[0].destination)
        if entry.key == "xdg_development_logs"
        else entry
        for entry in valid.entries
    )
    with pytest.raises(LocalStateMigrationError, match="destination location"):
        MigrationPlan(platform="linux", entries=duplicate_destination_entries).validate()

    unowned_key_entries = tuple(
        replace(entry, key="capture_state")
        if entry.key == "xdg_development_logs"
        else entry
        for entry in valid.entries
    )
    with pytest.raises(LocalStateMigrationError, match="outside the owned"):
        MigrationPlan(platform="linux", entries=unowned_key_entries).validate()

    capture_mapping_entries = tuple(
        replace(
            entry,
            source=Path("/synthetic/framenest-chatgpt-page"),
            destination=Path("/synthetic/kronika-chatgpt-page"),
        )
        if entry.key == "temporary_development_covers"
        else entry
        for entry in valid.entries
    )
    with pytest.raises(LocalStateMigrationError, match="brand component"):
        MigrationPlan(platform="linux", entries=capture_mapping_entries).validate()

    depth_change_entries = tuple(
        replace(
            entry,
            destination=Path("/synthetic/tmp/kronika-development/extra/catalog.sqlite3"),
        )
        if entry.key == "temporary_development_database"
        else entry
        for entry in valid.entries
    )
    with pytest.raises(LocalStateMigrationError, match="path depth"):
        MigrationPlan(platform="linux", entries=depth_change_entries).validate()


def test_a_receipt_directory_inside_the_repository_is_refused(tmp_path: Path) -> None:
    probe = REPOSITORY_ROOT / ".migration-receipt-probe"

    with pytest.raises(LocalStateMigrationError, match="outside the repository"):
        _migration(tmp_path, receipt_dir=probe)

    assert not probe.exists()


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_is_read_only_for_state_and_records_a_private_receipt(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    database = _make_database(layout["development"] / "catalog.sqlite3")
    database.close()
    covers = _write_file(layout["temp_development"] / "covers" / "cover.jpg", b"cover")
    database_before = (layout["development"] / "catalog.sqlite3").read_bytes()

    report = migration.check()

    statuses = {outcome.key: outcome.status for outcome in report.outcomes}
    assert statuses["xdg_development_database"] == COPY_READY_STATUS
    assert statuses["temporary_development_covers"] == COPY_READY_STATUS
    assert statuses["temporary_development_database"] == SOURCE_ABSENT_STATUS
    assert report.counts["classes"] == 12
    assert report.counts["sources_present"] == 2
    assert report.counts["sources_absent"] == 10
    assert report.counts["destinations_absent"] == 2
    assert report.counts["destinations_occupied"] == 0
    assert (layout["development"] / "catalog.sqlite3").read_bytes() == database_before
    assert covers.read_bytes() == b"cover"
    assert not (layout["data"] / "Kronika").exists()
    assert not (layout["temp_root"] / "kronika-development").exists()

    receipt = report.receipt_path
    assert receipt.name == "identity-paths-check.json"
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["operation"] == "check"
    assert payload["platform"] == "linux"
    recorded = {entry["class"]: entry for entry in payload["entries"]}
    assert recorded["xdg_development_database"]["source"] == str(
        _resolved(layout["development"] / "catalog.sqlite3")
    )
    assert recorded["xdg_development_database"]["destination"] == str(
        _resolved(layout["data"] / "Kronika" / "development" / "catalog.sqlite3")
    )


@pytest.mark.parametrize("kind", ["running", "unhealthy", "conflict"])
def test_check_and_apply_refuse_while_the_managed_server_is_not_stopped(
    tmp_path: Path,
    kind: str,
) -> None:
    migration = _migration(tmp_path, runtime_kind=kind)

    with pytest.raises(ManagedDevelopmentServerActiveError):
        migration.check()
    with pytest.raises(ManagedDevelopmentServerActiveError):
        migration.apply()

    assert not (tmp_path / "receipts").exists()


@pytest.mark.parametrize("kind", ["stopped", "stale"])
def test_check_and_apply_proceed_when_the_managed_server_is_stopped(
    tmp_path: Path,
    kind: str,
) -> None:
    migration = _migration(tmp_path, runtime_kind=kind)

    assert migration.check().operation == "check"
    assert migration.apply().operation == "apply"


def test_the_real_runtime_factory_proceeds_when_no_state_file_exists(
    tmp_path: Path,
) -> None:
    """The guard reads the real absent-state input, not only the stub."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    layout = _linux_layout(tmp_path)
    environ = _xdg_environ(layout) | {
        "FRAMENEST_DEVELOPMENT_RUNTIME_DIR": str(tmp_path / "runtime-override"),
        "FRAMENEST_PORT": str(port),
    }
    migration = LocalStateMigration(
        environ=environ,
        platform_name="linux",
        home=layout["home"],
        temp_root=layout["temp_root"],
        receipt_dir=tmp_path / "receipts",
    )

    assert migration.check().operation == "check"


def test_an_uninspectable_runtime_state_is_a_sanitized_failure(tmp_path: Path) -> None:
    layout = _linux_layout(tmp_path)
    migration = LocalStateMigration(
        environ=_xdg_environ(layout) | {"FRAMENEST_PORT": "not-a-number"},
        platform_name="linux",
        home=layout["home"],
        temp_root=layout["temp_root"],
        receipt_dir=tmp_path / "receipts",
    )

    with pytest.raises(LocalStateMigrationError) as excinfo:
        migration.check()

    assert "not-a-number" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_copies_a_populated_wal_database_consistently_and_verifies(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    database = layout["development"] / "catalog.sqlite3"
    connection = _make_database(database, rows=5, wal=True)
    try:
        assert (database.parent / "catalog.sqlite3-wal").is_file()

        report = migration.apply()

        outcome = _outcome(report, "xdg_development_database")
        assert outcome.status == COPIED_VERIFIED_STATUS
        assert outcome.evidence is not None
        assert isinstance(outcome.evidence["sha256"], str)
        assert outcome.evidence["integrity_check"] == "ok"
        assert outcome.evidence["table_counts"] == {"alembic_version": 1, "items": 5}
        assert outcome.evidence["alembic_version"] == "0035"
    finally:
        connection.close()

    destination = layout["data"] / "Kronika" / "development" / "catalog.sqlite3"
    copied = sqlite3.connect(str(destination))
    try:
        rows = copied.execute("SELECT name FROM items ORDER BY id").fetchall()
        assert [row[0] for row in rows] == [f"row-{index}" for index in range(5)]
        assert copied.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        copied.close()
    source = sqlite3.connect(str(database))
    try:
        assert source.execute("SELECT COUNT(*) FROM items").fetchone() == (5,)
    finally:
        source.close()

    receipt = json.loads(report.receipt_path.read_text(encoding="utf-8"))
    recorded = {entry["class"]: entry for entry in receipt["entries"]}
    assert recorded["xdg_development_database"]["status"] == COPIED_VERIFIED_STATUS
    assert recorded["xdg_development_database"]["evidence"]["alembic_version"] == "0035"


def test_apply_never_overwrites_a_conflicting_destination(tmp_path: Path) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    source = layout["development"] / "catalog.sqlite3"
    connection = _make_database(source, rows=1)
    connection.close()
    destination = layout["data"] / "Kronika" / "development" / "catalog.sqlite3"
    _write_file(destination, b"unrelated-destination-bytes")
    destination_before = destination.read_bytes()

    report = migration.apply()

    assert _outcome(report, "xdg_development_database").status == (
        DESTINATION_OCCUPIED_REFUSED_STATUS
    )
    assert report.refused_keys == ("xdg_development_database",)
    assert destination.read_bytes() == destination_before
    assert source.read_bytes()[:16] == b"SQLite format 3\x00"


def test_apply_creates_no_empty_replacement_when_the_source_is_absent(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))

    report = migration.apply()

    statuses = {outcome.key: outcome.status for outcome in report.outcomes}
    assert statuses["xdg_development_database"] == SOURCE_ABSENT_STATUS
    assert statuses["temporary_development_database"] == SOURCE_ABSENT_STATUS
    assert report.counts["copied_verified"] == 0
    assert not (layout["data"] / "Kronika").exists()
    assert not (layout["temp_root"] / "kronika-development").exists()
    assert not (layout["state"] / "Kronika").exists()
    assert not (layout["config"] / "kronika").exists()
    assert not (layout["home"] / "Library").exists()


def test_apply_copies_files_and_directories_with_content_equality(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    _write_file(
        layout["development"] / "runtime-settings.json",
        b'{"automatic": true}\n',
        mode=0o640,
    )
    _write_file(
        layout["temp_development"] / "covers" / "nested" / "cover.jpg",
        b"nested-cover",
        mode=0o644,
    )
    _write_file(layout["temp_development"] / "covers" / "cover.jpg", b"outer-cover")
    _write_file(
        layout["temp_development"] / "cover-thumbnails" / "thumb.jpg", b"thumbnail"
    )
    _write_file(layout["ai"] / "config.json", b'{"schema_version": 3}\n')
    _write_file(layout["ai"] / "test-state.json", b'{"schema_version": 1}\n')
    _write_file(layout["ai"] / "status-snapshot.json", b'{"schema_version": 1}\n')

    report = migration.apply()

    assert _outcome(report, "xdg_development_runtime_settings").status == (
        COPIED_VERIFIED_STATUS
    )
    covers = _outcome(report, "temporary_development_covers")
    assert covers.status == COPIED_VERIFIED_STATUS
    assert covers.evidence["file_count"] == 2
    assert _outcome(report, "xdg_ai_configuration").status == COPIED_VERIFIED_STATUS
    assert _outcome(report, "xdg_ai_test_state").status == COPIED_VERIFIED_STATUS

    copied_settings = layout["data"] / "Kronika" / "development" / "runtime-settings.json"
    assert copied_settings.read_bytes() == b'{"automatic": true}\n'
    assert stat.S_IMODE(copied_settings.stat().st_mode) == 0o640
    copied_root = layout["temp_root"] / "kronika-development" / "covers"
    assert (copied_root / "cover.jpg").read_bytes() == b"outer-cover"
    assert (copied_root / "nested" / "cover.jpg").read_bytes() == b"nested-cover"
    assert (
        layout["temp_root"] / "kronika-development" / "cover-thumbnails" / "thumb.jpg"
    ).read_bytes() == b"thumbnail"
    assert (layout["config"] / "kronika" / "ai" / "config.json").read_bytes() == (
        b'{"schema_version": 3}\n'
    )
    assert (layout["development"] / "runtime-settings.json").read_bytes() == (
        b'{"automatic": true}\n'
    )


def test_apply_excludes_managed_process_records_and_keeps_nested_names(
    tmp_path: Path,
) -> None:
    layout = _macos_layout(tmp_path)
    migration = _migration(
        tmp_path,
        platform_name="darwin",
        home=layout["home"],
        temp_root=layout["temp_root"],
    )
    runtime_dir = layout["development"] / "runtime"
    _write_file(runtime_dir / "server-state.json", b'{"pid": 4242}')
    _write_file(runtime_dir / "operation.lock", b"")
    _write_file(runtime_dir / "notes.json", b"durable")
    _write_file(runtime_dir / "session" / "server-state.json", b"nested")

    report = migration.apply()

    assert _outcome(report, "macos_development_runtime").status == (
        COPIED_VERIFIED_STATUS
    )
    destination = (
        layout["home"]
        / "Library"
        / "Application Support"
        / "Kronika"
        / "development"
        / "runtime"
    )
    assert (destination / "notes.json").read_bytes() == b"durable"
    assert (destination / "session" / "server-state.json").read_bytes() == b"nested"
    assert not (destination / "server-state.json").exists()
    assert not (destination / "operation.lock").exists()


def test_apply_reports_no_durable_content_when_only_liveness_records_exist(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    _write_file(layout["runtime"] / "server-state.json", b'{"pid": 4242}')

    report = migration.apply()

    assert _outcome(report, "xdg_development_runtime").status == (
        NO_DURABLE_CONTENT_STATUS
    )
    assert not (layout["state"] / "Kronika" / "development" / "runtime").exists()


def test_apply_leaves_explicitly_overridden_paths_unchanged(tmp_path: Path) -> None:
    layout = _linux_layout(tmp_path)
    custom = tmp_path / "custom" / "catalog.sqlite3"
    custom_connection = _make_database(custom, rows=1)
    custom_connection.close()
    legacy_connection = _make_database(layout["development"] / "catalog.sqlite3", rows=1)
    legacy_connection.close()
    custom_before = custom.read_bytes()
    legacy_path = layout["development"] / "catalog.sqlite3"
    legacy_before = legacy_path.read_bytes()
    environ = _xdg_environ(layout) | {"FRAMENEST_DATABASE_PATH": str(custom)}
    migration = _migration(tmp_path, environ=environ)

    report = migration.apply()

    assert _outcome(report, "xdg_development_database").status == OVERRIDE_STATUS
    assert _outcome(report, "temporary_development_database").status == OVERRIDE_STATUS
    assert custom.read_bytes() == custom_before
    assert legacy_path.read_bytes() == legacy_before
    assert not (layout["data"] / "Kronika" / "development" / "catalog.sqlite3").exists()
    assert not (layout["temp_root"] / "kronika-development" / "catalog.sqlite3").exists()
    receipt = json.loads(report.receipt_path.read_text(encoding="utf-8"))
    recorded = {entry["class"]: entry for entry in receipt["entries"]}
    assert recorded["xdg_development_database"]["status"] == OVERRIDE_STATUS


def test_a_repeated_apply_is_a_verified_noop_not_a_second_copy(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    connection = _make_database(layout["development"] / "catalog.sqlite3", rows=2)
    connection.close()
    _write_file(layout["development"] / "runtime-settings.json", b'{"a": 1}\n')
    _write_file(layout["temp_development"] / "gallery-previews" / "p.png", b"png")

    first = migration.apply()
    assert _outcome(first, "xdg_development_database").status == COPIED_VERIFIED_STATUS

    snapshots = {
        "database": _digest(
            layout["data"] / "Kronika" / "development" / "catalog.sqlite3"
        ),
        "settings": _digest(
            layout["data"] / "Kronika" / "development" / "runtime-settings.json"
        ),
        "previews": _digest(
            layout["temp_root"] / "kronika-development" / "gallery-previews"
        ),
    }

    second = migration.apply()

    assert _outcome(second, "xdg_development_database").status == (
        ALREADY_VERIFIED_STATUS
    )
    assert _outcome(second, "xdg_development_runtime_settings").status == (
        ALREADY_VERIFIED_STATUS
    )
    assert _outcome(second, "temporary_development_gallery_previews").status == (
        ALREADY_VERIFIED_STATUS
    )
    assert _digest(
        layout["data"] / "Kronika" / "development" / "catalog.sqlite3"
    ) == snapshots["database"]
    assert _digest(
        layout["data"] / "Kronika" / "development" / "runtime-settings.json"
    ) == snapshots["settings"]
    assert _digest(
        layout["temp_root"] / "kronika-development" / "gallery-previews"
    ) == snapshots["previews"]


def test_apply_refuses_after_a_verified_destination_changed(tmp_path: Path) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    _write_file(layout["development"] / "runtime-settings.json", b"original")
    first = migration.apply()
    assert _outcome(first, "xdg_development_runtime_settings").status == (
        COPIED_VERIFIED_STATUS
    )
    destination = layout["data"] / "Kronika" / "development" / "runtime-settings.json"
    destination.write_bytes(b"original-plus-later-writes")

    second = migration.apply()

    assert _outcome(second, "xdg_development_runtime_settings").status == (
        DESTINATION_OCCUPIED_REFUSED_STATUS
    )
    assert destination.read_bytes() == b"original-plus-later-writes"


def test_interrupted_copy_partials_are_recovered_without_corruption(
    tmp_path: Path,
) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    source = _write_file(layout["development"] / "runtime-settings.json", b"state")
    destination = layout["data"] / "Kronika" / "development" / "runtime-settings.json"
    destination.parent.mkdir(parents=True)
    stale_file = destination.parent / f".{destination.name}.migration-partial-stale"
    stale_file.write_bytes(b"garbage")
    stale_directory = destination.parent / f".{destination.name}.migration-partial-dir"
    stale_directory.mkdir()
    (stale_directory / "garbage").write_bytes(b"garbage")

    report = migration.apply()

    assert _outcome(report, "xdg_development_runtime_settings").status == (
        COPIED_VERIFIED_STATUS
    )
    assert destination.read_bytes() == source.read_bytes()
    assert not list(
        destination.parent.glob(f".{destination.name}.migration-partial-*")
    )


def test_a_failed_copy_is_refused_and_retryable(tmp_path: Path) -> None:
    layout = _linux_layout(tmp_path)
    migration = _migration(tmp_path, environ=_xdg_environ(layout))
    source = _write_file(layout["development"] / "runtime-settings.json", b"state")
    destination = layout["data"] / "Kronika" / "development" / "runtime-settings.json"

    with mock.patch.object(
        migration_module, "_copy_file", side_effect=OSError("synthetic failure")
    ):
        first = migration.apply()

    assert _outcome(first, "xdg_development_runtime_settings").status == (
        COPY_REFUSED_STATUS
    )
    assert not destination.exists()
    if destination.parent.is_dir():
        assert not list(
            destination.parent.glob(f".{destination.name}.migration-partial-*")
        )

    second = migration.apply()

    assert _outcome(second, "xdg_development_runtime_settings").status == (
        COPIED_VERIFIED_STATUS
    )
    assert destination.read_bytes() == source.read_bytes()


# ---------------------------------------------------------------------------
# Legacy-state refusal safeguard
# ---------------------------------------------------------------------------


def test_legacy_state_detector_refuses_and_clears(tmp_path: Path) -> None:
    layout = _linux_layout(tmp_path)
    environ = _xdg_environ(layout)
    _make_database(layout["development"] / "catalog.sqlite3", rows=1).close()

    with pytest.raises(LegacyStateReplacementRefusal) as excinfo:
        refuse_silent_legacy_state_replacement(
            environ=environ,
            platform_name="linux",
            home=layout["home"],
            temp_root=layout["temp_root"],
        )

    assert "xdg_development_database" in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)

    canonical = layout["data"] / "Kronika" / "development" / "catalog.sqlite3"
    _make_database(canonical, rows=1).close()
    refuse_silent_legacy_state_replacement(
        environ=environ,
        platform_name="linux",
        home=layout["home"],
        temp_root=layout["temp_root"],
    )

    canonical.unlink()
    (layout["development"] / "catalog.sqlite3").unlink()
    refuse_silent_legacy_state_replacement(
        environ=environ,
        platform_name="linux",
        home=layout["home"],
        temp_root=layout["temp_root"],
    )

    _make_database(layout["development"] / "catalog.sqlite3", rows=1).close()
    refuse_silent_legacy_state_replacement(
        environ=environ | {"FRAMENEST_DATABASE_PATH": str(tmp_path / "elsewhere.sqlite3")},
        platform_name="linux",
        home=layout["home"],
        temp_root=layout["temp_root"],
    )


def test_capture_profile_credentials_and_media_trees_stay_outside_the_mapping(
    tmp_path: Path,
) -> None:
    layout = _macos_layout(tmp_path)
    migration = _migration(
        tmp_path,
        platform_name="darwin",
        home=layout["home"],
        temp_root=layout["temp_root"],
    )
    forbidden = {
        "capture": layout["home"]
        / "Library"
        / "Application Support"
        / "framenest-chatgpt-page"
        / "state.json",
        "profile": layout["home"] / "profiles" / "framenest-browser-profile" / "Cookies",
        "credentials": layout["home"] / ".config" / "framenest" / "credentials.json",
        "media": layout["home"] / "media" / "original.mov",
    }
    digests = {}
    for name, path in forbidden.items():
        _write_file(path, b"private-" + name.encode("utf-8"))
        digests[name] = _digest(path)

    report = migration.apply()

    for name, path in forbidden.items():
        assert _digest(path) == digests[name]
    for entry in migration.plan.entries:
        for path in forbidden.values():
            assert not path.is_relative_to(entry.source)
            assert not path.is_relative_to(entry.destination)
    for outcome in report.outcomes:
        assert "framenest-chatgpt-page" not in str(outcome.destination)


# ---------------------------------------------------------------------------
# Prefix routing for override detection
# ---------------------------------------------------------------------------


def test_override_detection_reads_either_prefix_and_fails_closed(
    tmp_path: Path,
) -> None:
    compatible = _migration(
        tmp_path,
        environ={"FRAMENEST_DATABASE_PATH": str(tmp_path / "compatible.sqlite3")},
    )
    statuses = {
        outcome.key: outcome.status for outcome in compatible.check().outcomes
    }
    assert statuses["xdg_development_database"] == OVERRIDE_STATUS
    assert statuses["temporary_development_database"] == OVERRIDE_STATUS

    primary = _migration(
        tmp_path,
        environ={"KRONIKA_DATABASE_PATH": str(tmp_path / "primary.sqlite3")},
    )
    statuses = {outcome.key: outcome.status for outcome in primary.check().outcomes}
    assert statuses["xdg_development_database"] == OVERRIDE_STATUS

    with pytest.raises(LocalStateMigrationIdentityError) as excinfo:
        conflicted = _migration(
            tmp_path,
            environ={
                "KRONIKA_DATABASE_PATH": str(tmp_path / "a.sqlite3"),
                "FRAMENEST_DATABASE_PATH": str(tmp_path / "b.sqlite3"),
            },
        )
        conflicted.check()
    assert excinfo.value.exit_status == 2
    assert "DATABASE_PATH" in str(excinfo.value)
    assert str(tmp_path / "a.sqlite3") not in str(excinfo.value)
    assert str(tmp_path / "b.sqlite3") not in str(excinfo.value)
