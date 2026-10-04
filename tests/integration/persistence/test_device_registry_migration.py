"""Integration tests for device registry migration revision 0002."""

from __future__ import annotations

import importlib.resources
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.domain import DeviceId

PRODUCTION_VERSIONS_PACKAGE = (
    "kronika.infrastructure.persistence.alembic_environment.versions"
)
EXPECTED_HEAD_REVISION = "0002"
CURRENT_HEAD_REVISION = "0035"


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


def _settings_for(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _devices_columns(database_path: Path) -> list[tuple[str, str, int, str | None, int]]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute("PRAGMA table_info(devices)").fetchall()
    finally:
        connection.close()


def _devices_sql(database_path: Path) -> str:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'devices'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row[0]


def test_empty_database_upgrades_to_revision_0002(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

    settings = _settings_for(tmp_path / "head-0002.sqlite3")
    _upgrade_to_revision(settings.database_path, "0002")
    inspected = inspect_database_migration_status(settings)

    assert inspected.state == "behind"
    assert inspected.current_revision == EXPECTED_HEAD_REVISION
    assert inspected.head_revision == CURRENT_HEAD_REVISION


def test_database_at_0001_upgrades_to_0002(tmp_path: Path) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.migrations import (
        _alembic_config,
        inspect_database_migration_status,
        load_script_directory,
    )
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine

    settings = _settings_for(tmp_path / "step-upgrade.sqlite3")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "0001")
    finally:
        dispose_engine(engine)

    at_0001 = inspect_database_migration_status(settings)
    assert at_0001.current_revision == "0001"
    assert at_0001.head_revision == CURRENT_HEAD_REVISION

    _upgrade_to_revision(settings.database_path, "0002")
    upgraded = inspect_database_migration_status(settings)
    assert upgraded.current_revision == EXPECTED_HEAD_REVISION
    assert upgraded.head_revision == CURRENT_HEAD_REVISION
    assert _table_names(settings.database_path) == {"alembic_version", "devices"}


def test_repeated_upgrade_to_0002_is_safe(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

    settings = _settings_for(tmp_path / "repeat-0002.sqlite3")
    _upgrade_to_revision(settings.database_path, "0002")
    first = inspect_database_migration_status(settings)
    _upgrade_to_revision(settings.database_path, "0002")
    second = inspect_database_migration_status(settings)
    assert first == second
    assert first.current_revision == EXPECTED_HEAD_REVISION


def test_devices_table_exists_with_required_schema(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path / "schema-0002.sqlite3")
    _upgrade_to_revision(settings.database_path, "0002")

    assert _table_names(settings.database_path) == {"alembic_version", "devices"}
    columns = {row[1]: row for row in _devices_columns(settings.database_path)}
    assert set(columns) == {"id", "display_name"}
    assert columns["id"][2] == "TEXT"
    assert columns["display_name"][2] == "TEXT"
    assert columns["id"][3] == 1
    assert columns["display_name"][3] == 1
    assert columns["id"][5] == 1
    sql = _devices_sql(settings.database_path)
    assert "PRIMARY KEY" in sql
    assert "ck_devices_id_length" in sql
    assert "ck_devices_display_name_length" in sql


def test_duplicate_device_ids_are_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "duplicate-id.sqlite3")
    upgrade_database_to_head(settings)
    device_id = DeviceId.new().to_string()
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (device_id, "First"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO devices (id, display_name) VALUES (?, ?)",
                (device_id, "Second"),
            )
    finally:
        connection.close()


def test_duplicate_display_names_are_allowed(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "duplicate-name.sqlite3")
    upgrade_database_to_head(settings)
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (DeviceId.new().to_string(), "Shared"),
        )
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (DeviceId.new().to_string(), "Shared"),
        )
        connection.commit()
        count = connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        assert count == 2
    finally:
        connection.close()


def test_packaged_migration_resources_include_0002() -> None:
    versions = importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
    assert versions.joinpath("0001_initial_foundation.py").is_file()
    assert versions.joinpath("0002_device_registry.py").is_file()

    script_directory = __import__(
        "kronika.infrastructure.persistence.migrations",
        fromlist=["load_script_directory"],
    ).load_script_directory()
    revision = script_directory.get_revision("0002")
    assert revision is not None
    assert revision.down_revision == "0001"
