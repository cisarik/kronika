"""Integration tests for media catalog migration revision 0004."""

from __future__ import annotations

import importlib.resources
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings

PRODUCTION_VERSIONS_PACKAGE = (
    "kronika.infrastructure.persistence.alembic_environment.versions"
)
TARGET_MEDIA_CATALOG_REVISION = "0004"
CURRENT_HEAD_REVISION = "0035"
TARGET_PREVIOUS_REVISION = "0003"

DEVICE_ID = "12345678-1234-4234-9234-123456789abc"
SECOND_DEVICE_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
LIBRARY_ID = "11111111-2222-4333-8444-555555555555"
SECOND_LIBRARY_ID = "22222222-3333-4444-8555-666666666666"
MEDIA_ID = "33333333-4444-4555-8666-777777777777"
LOCATION_ID = "44444444-5555-4666-8777-888888888888"


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


def _downgrade_to_revision(database_path: Path, revision: str) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.downgrade(config, revision)
    finally:
        dispose_engine(engine)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_names(database_path: Path) -> set[str]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()


def _table_sql(database_path: Path, table_name: str) -> str:
    connection = _connect(database_path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def _columns(database_path: Path, table_name: str) -> dict[str, tuple[object, ...]]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]): row for row in rows}
    finally:
        connection.close()


def _foreign_keys(database_path: Path, table_name: str) -> list[tuple[object, ...]]:
    connection = _connect(database_path)
    try:
        return connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
    finally:
        connection.close()


def _indexes(database_path: Path, table_name: str) -> dict[str, tuple[object, ...]]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(f"PRAGMA index_list({table_name})").fetchall()
        return {str(row[1]): row for row in rows}
    finally:
        connection.close()


def _insert_device_and_libraries(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (DEVICE_ID, "Device"),
        )
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (SECOND_DEVICE_ID, "Second Device"),
        )
        connection.execute(
            "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (LIBRARY_ID, DEVICE_ID, "Library", "posix", "/media/library"),
        )
        connection.execute(
            "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                SECOND_LIBRARY_ID,
                SECOND_DEVICE_ID,
                "Second Library",
                "posix",
                "/media/second",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _insert_media(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?)",
            (MEDIA_ID, "video", 10, 20),
        )
        connection.commit()
    finally:
        connection.close()


def test_packaged_migration_resources_include_0004() -> None:
    versions = importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
    assert versions.joinpath("0004_media_catalog_foundation.py").is_file()

    from kronika.infrastructure.persistence.migrations import load_script_directory

    revision = load_script_directory().get_revision(TARGET_MEDIA_CATALOG_REVISION)
    assert revision is not None
    assert revision.down_revision == TARGET_PREVIOUS_REVISION


def test_empty_database_upgrades_to_current_head_revision_0009(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "head-0004.sqlite3")
    status = upgrade_database_to_head(settings)
    inspected = inspect_database_migration_status(settings)

    assert status.state == "at_head"
    assert status.current_revision == CURRENT_HEAD_REVISION
    assert status.head_revision == CURRENT_HEAD_REVISION
    assert inspected == status


def test_database_at_0003_reports_behind_current_head(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

    settings = _settings_for(tmp_path / "behind-0003.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)

    status = inspect_database_migration_status(settings)

    assert status.state == "behind"
    assert status.current_revision == TARGET_PREVIOUS_REVISION
    assert status.head_revision == CURRENT_HEAD_REVISION


def test_upgrade_from_0003_to_0004_preserves_existing_device_and_library_rows(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "preserve.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)
    _insert_device_and_libraries(settings.database_path)

    status = upgrade_database_to_head(settings)

    assert status.current_revision == CURRENT_HEAD_REVISION
    connection = _connect(settings.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 2
    finally:
        connection.close()


def test_media_catalog_tables_have_required_schema_constraints_and_indexes(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "schema.sqlite3")
    upgrade_database_to_head(settings)

    assert {
        "logical_media",
        "physical_media_locations",
    }.issubset(_table_names(settings.database_path))

    logical_columns = _columns(settings.database_path, "logical_media")
    assert set(logical_columns) == {"id", "media_kind", "created_at_ms", "updated_at_ms"}
    assert logical_columns["id"][2] == "TEXT"
    assert logical_columns["id"][3] == 1
    assert logical_columns["id"][5] == 1
    assert logical_columns["media_kind"][2] == "TEXT"
    assert logical_columns["created_at_ms"][2] == "INTEGER"
    assert logical_columns["updated_at_ms"][2] == "INTEGER"

    location_columns = _columns(settings.database_path, "physical_media_locations")
    assert set(location_columns) == {
        "id",
        "media_id",
        "library_id",
        "relative_path",
        "availability",
        "observed_size_bytes",
        "observed_mtime_ns",
        "created_at_ms",
        "updated_at_ms",
    }
    assert location_columns["id"][2] == "TEXT"
    assert location_columns["id"][5] == 1
    assert location_columns["media_id"][2] == "TEXT"
    assert location_columns["library_id"][2] == "TEXT"
    assert location_columns["relative_path"][2] == "TEXT"
    assert location_columns["availability"][2] == "TEXT"

    logical_sql = _table_sql(settings.database_path, "logical_media")
    assert "ck_logical_media_id_length" in logical_sql
    assert "ck_logical_media_kind" in logical_sql
    assert "'video', 'animated_image'" in logical_sql
    assert "ck_logical_media_created_at_ms_non_negative" in logical_sql
    assert "ck_logical_media_updated_at_ms_non_negative" in logical_sql

    location_sql = _table_sql(settings.database_path, "physical_media_locations")
    assert "ck_physical_media_locations_id_length" in location_sql
    assert "ck_physical_media_locations_media_id_length" in location_sql
    assert "ck_physical_media_locations_library_id_length" in location_sql
    assert "ck_physical_media_locations_relative_path_length" in location_sql
    assert "ck_physical_media_locations_availability" in location_sql
    assert "'available', 'offline', 'missing', 'unverified', 'archived'" in location_sql
    assert "ck_physical_media_locations_observed_size_non_negative" in location_sql
    assert "ck_physical_media_locations_observed_mtime_non_negative" in location_sql
    assert "uq_physical_media_locations_library_path" in location_sql

    location_foreign_keys = _foreign_keys(settings.database_path, "physical_media_locations")
    assert {
        (row[2], row[3], row[4], row[6])
        for row in location_foreign_keys
    } == {
        ("libraries", "library_id", "id", "RESTRICT"),
        ("logical_media", "media_id", "id", "RESTRICT"),
    }

    indexes = _indexes(settings.database_path, "physical_media_locations")
    assert "ix_physical_media_locations_media_id" in indexes
    assert any(row[2] == 1 and row[3] == "u" for row in indexes.values())


def test_location_uniqueness_foreign_keys_and_different_library_paths(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "constraints.sqlite3")
    upgrade_database_to_head(settings)
    _insert_device_and_libraries(settings.database_path)
    _insert_media(settings.database_path)

    connection = _connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, observed_size_bytes, "
            "observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (LOCATION_ID, MEDIA_ID, LIBRARY_ID, "clips/a.mp4", "available", 123, 456, 1, 2),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO physical_media_locations "
                "(id, media_id, library_id, relative_path, availability, observed_size_bytes, "
                "observed_mtime_ns, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "55555555-6666-4777-8888-999999999999",
                    MEDIA_ID,
                    LIBRARY_ID,
                    "clips/a.mp4",
                    "available",
                    None,
                    None,
                    1,
                    2,
                ),
            )
        connection.execute(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, observed_size_bytes, "
            "observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "66666666-7777-4888-9999-aaaaaaaaaaaa",
                MEDIA_ID,
                SECOND_LIBRARY_ID,
                "clips/a.mp4",
                "available",
                None,
                None,
                1,
                2,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO physical_media_locations "
                "(id, media_id, library_id, relative_path, availability, observed_size_bytes, "
                "observed_mtime_ns, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "77777777-8888-4999-aaaa-bbbbbbbbbbbb",
                    "88888888-9999-4aaa-bbbb-cccccccccccc",
                    LIBRARY_ID,
                    "clips/missing-media.mp4",
                    "available",
                    None,
                    None,
                    1,
                    2,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO physical_media_locations "
                "(id, media_id, library_id, relative_path, availability, observed_size_bytes, "
                "observed_mtime_ns, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "88888888-9999-4aaa-bbbb-cccccccccccc",
                    MEDIA_ID,
                    "99999999-aaaa-4bbb-8ccc-dddddddddddd",
                    "clips/missing-library.mp4",
                    "available",
                    None,
                    None,
                    1,
                    2,
                ),
            )
    finally:
        connection.close()


def test_downgrade_from_0004_to_0003_removes_only_media_catalog_objects(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
    )

    settings = _settings_for(tmp_path / "downgrade.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_MEDIA_CATALOG_REVISION)
    _insert_device_and_libraries(settings.database_path)
    _insert_media(settings.database_path)

    _downgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)

    status = inspect_database_migration_status(settings)
    assert status.state == "behind"
    assert status.current_revision == TARGET_PREVIOUS_REVISION
    assert status.head_revision == CURRENT_HEAD_REVISION
    assert _table_names(settings.database_path) == {"alembic_version", "devices", "libraries"}
    connection = _connect(settings.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 2
    finally:
        connection.close()
