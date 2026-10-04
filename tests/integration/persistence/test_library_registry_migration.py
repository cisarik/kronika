"""Integration tests for library registry migration revision 0003."""

from __future__ import annotations

import importlib.resources
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.domain import DeviceId, LibraryId

PRODUCTION_VERSIONS_PACKAGE = (
    "kronika.infrastructure.persistence.alembic_environment.versions"
)
TARGET_LIBRARY_REVISION = "0003"
CURRENT_HEAD_REVISION = "0035"


def _settings_for(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _upgrade_to_revision(database_path: Path, revision: str) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    settings = _settings_for(database_path)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, revision)
    finally:
        dispose_engine(engine)


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _libraries_sql(database_path: Path) -> str:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'libraries'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row[0]


def test_empty_database_upgrades_to_target_library_revision_0003(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "head-0003.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_LIBRARY_REVISION)
    inspected = inspect_database_migration_status(settings)

    assert inspected.state == "behind"
    assert inspected.current_revision == TARGET_LIBRARY_REVISION
    assert inspected.head_revision == CURRENT_HEAD_REVISION


def test_database_at_0002_upgrades_to_0003(tmp_path: Path) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import (
        _alembic_config,
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "step-upgrade.sqlite3")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "0002")
    finally:
        dispose_engine(engine)

    at_0002 = inspect_database_migration_status(settings)
    assert at_0002.current_revision == "0002"
    assert at_0002.head_revision == CURRENT_HEAD_REVISION

    upgraded = upgrade_database_to_head(settings)
    assert upgraded.current_revision == CURRENT_HEAD_REVISION
    assert upgraded.head_revision == CURRENT_HEAD_REVISION
    assert {
        "alembic_version",
        "devices",
        "libraries",
        "logical_media",
        "physical_media_locations",
    }.issubset(_table_names(settings.database_path))


def test_repeated_migration_at_head_is_safe(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "repeat-0003.sqlite3")
    first = upgrade_database_to_head(settings)
    second = upgrade_database_to_head(settings)
    assert first == second
    assert first.current_revision == CURRENT_HEAD_REVISION


def test_libraries_table_has_required_schema(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path / "schema-0003.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_LIBRARY_REVISION)

    assert _table_names(settings.database_path) == {"alembic_version", "devices", "libraries"}
    sql = _libraries_sql(settings.database_path)
    assert "FOREIGN KEY" in sql
    assert "device_id" in sql
    assert "path_flavor" in sql
    assert "root_path" in sql
    assert "ck_libraries_id_length" in sql
    assert "ck_libraries_device_id_length" in sql
    assert "ck_libraries_display_name_length" in sql
    assert "ck_libraries_path_flavor" in sql
    assert "ck_libraries_root_path_length" in sql
    assert "uq_libraries_device_root" in sql


def test_duplicate_same_device_root_is_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "duplicate-root.sqlite3")
    upgrade_database_to_head(settings)
    connection = sqlite3.connect(settings.database_path)
    try:
        device_id = DeviceId.new().to_string()
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (device_id, "Device"),
        )
        library_id = LibraryId.new().to_string()
        connection.execute(
            "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (library_id, device_id, "First", "posix", "/media/shared"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    LibraryId.new().to_string(),
                    device_id,
                    "Second",
                    "posix",
                    "/media/shared",
                ),
            )
    finally:
        connection.close()


def test_packaged_migration_resources_include_0003() -> None:
    versions = importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
    assert versions.joinpath("0003_library_registry.py").is_file()

    from kronika.infrastructure.persistence.migrations import load_script_directory

    revision = load_script_directory().get_revision("0003")
    assert revision is not None
    assert revision.down_revision == "0002"
