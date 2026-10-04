"""Integration tests for media metadata migration revision 0005."""

from __future__ import annotations

import importlib.resources
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings

PRODUCTION_VERSIONS_PACKAGE = (
    "kronika.infrastructure.persistence.alembic_environment.versions"
)
CURRENT_HEAD_REVISION = "0035"
TARGET_COLLECTION_REVISION = "0007"
TARGET_PREVIOUS_REVISION = "0006"
DEVICE_ID = "12345678-1234-4234-9234-123456789abc"
LIBRARY_ID = "11111111-2222-4333-8444-555555555555"
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


def _insert_populated_0004_rows(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute("INSERT INTO devices (id, display_name) VALUES (?, ?)", (DEVICE_ID, "Device"))
        connection.execute(
            "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (LIBRARY_ID, DEVICE_ID, "Library", "posix", "/media/library"),
        )
        connection.execute(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?)",
            (MEDIA_ID, "video", 10, 20),
        )
        connection.execute(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, observed_size_bytes, "
            "observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (LOCATION_ID, MEDIA_ID, LIBRARY_ID, "clips/a.mp4", "available", 123, 456, 30, 40),
        )
        connection.commit()
    finally:
        connection.close()


def test_packaged_migration_resources_include_0007() -> None:
    versions = importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
    assert versions.joinpath("0007_automatic_processed_collection.py").is_file()
    assert versions.joinpath("0006_persistent_media_description.py").is_file()
    assert versions.joinpath("0005_media_metadata_and_canonical_tags.py").is_file()

    from kronika.infrastructure.persistence.migrations import load_script_directory

    revision = load_script_directory().get_revision(TARGET_COLLECTION_REVISION)
    assert revision is not None
    assert revision.down_revision == TARGET_PREVIOUS_REVISION


def test_empty_database_upgrades_to_current_head_revision_0009(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "head-0009.sqlite3")
    status = upgrade_database_to_head(settings)

    assert status.state == "at_head"
    assert status.current_revision == CURRENT_HEAD_REVISION
    assert inspect_database_migration_status(settings) == status
    assert {"canonical_tags", "media_metadata", "media_canonical_tags"}.issubset(
        _table_names(settings.database_path)
    )


def test_media_metadata_tables_have_required_schema_constraints_and_indexes(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "schema.sqlite3")
    upgrade_database_to_head(settings)

    assert set(_columns(settings.database_path, "canonical_tags")) == {
        "key",
        "display_name",
        "created_at_ms",
        "updated_at_ms",
    }
    assert set(_columns(settings.database_path, "media_metadata")) == {
        "media_id",
        "display_title",
        "description",
        "content_category",
        "acquisition_source",
        "creator_attribution_kind",
        "creator_stable_id",
        "creator_handle",
        "creator_display_name",
        "collection_key",
        "processed_at_ms",
        "created_at_ms",
        "updated_at_ms",
    }
    assert set(_columns(settings.database_path, "media_canonical_tags")) == {
        "media_id",
        "tag_key",
        "position",
    }

    tag_sql = _table_sql(settings.database_path, "canonical_tags")
    metadata_sql = _table_sql(settings.database_path, "media_metadata")
    assignments_sql = _table_sql(settings.database_path, "media_canonical_tags")
    assert "ck_canonical_tags_key_length" in tag_sql
    assert "ck_canonical_tags_key_lowercase" in tag_sql
    assert "ck_canonical_tags_key_slug" in tag_sql
    assert "ck_canonical_tags_display_name_length" in tag_sql
    assert "ck_canonical_tags_updated_not_before_created" in tag_sql
    assert "ck_media_metadata_collection_key_valid" in metadata_sql
    assert "ck_media_metadata_collection_paired" in metadata_sql
    assert "ck_media_metadata_processed_at_ms_non_negative" in metadata_sql
    assert "ck_media_metadata_title_length" in metadata_sql
    assert "ck_media_metadata_description_length" in metadata_sql
    assert "ck_media_metadata_updated_not_before_created" in metadata_sql
    assert "ck_media_metadata_content_category" in metadata_sql
    assert "ck_media_metadata_creator_attribution" in metadata_sql
    assert "ck_media_canonical_tags_position_range" in assignments_sql

    assert {
        (row[2], row[3], row[4], row[6])
        for row in _foreign_keys(settings.database_path, "media_metadata")
    } == {("logical_media", "media_id", "id", "RESTRICT")}
    assert {
        (row[2], row[3], row[4], row[6])
        for row in _foreign_keys(settings.database_path, "media_canonical_tags")
    } == {
        ("canonical_tags", "tag_key", "key", "RESTRICT"),
        ("media_metadata", "media_id", "media_id", "RESTRICT"),
    }
    indexes = _indexes(settings.database_path, "media_canonical_tags")
    assert any(row[2] == 1 and row[3] == "u" for row in indexes.values())
    metadata_indexes = _indexes(settings.database_path, "media_metadata")
    assert "ix_media_metadata_collection" in metadata_indexes
    assert "ix_media_metadata_creator_stable" in metadata_indexes
    assert "ix_media_metadata_creator_handle" in metadata_indexes


def test_upgrade_from_populated_0004_preserves_rows_and_does_not_backfill_metadata(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "preserve.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)
    _insert_populated_0004_rows(settings.database_path)

    status = upgrade_database_to_head(settings)

    assert status.current_revision == CURRENT_HEAD_REVISION
    connection = _connect(settings.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM physical_media_locations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM media_metadata").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM canonical_tags").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM media_canonical_tags").fetchone()[0] == 0
    finally:
        connection.close()


def test_canonical_tag_metadata_and_ordered_assignments_round_trip(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "roundtrip.sqlite3")
    upgrade_database_to_head(settings)
    _insert_populated_0004_rows(settings.database_path)

    connection = _connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('mathematics', 'Math', 1, 1)"
        )
        connection.execute(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('compression', 'Compression', 1, 1)"
        )
        connection.execute(
            "INSERT INTO media_metadata (media_id, display_title, created_at_ms, updated_at_ms) "
            "VALUES (?, 'Reinventing Entropy', 2, 3)",
            (MEDIA_ID,),
        )
        connection.execute(
            "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, ?, ?)",
            (MEDIA_ID, "mathematics", 0),
        )
        connection.execute(
            "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, ?, ?)",
            (MEDIA_ID, "compression", 1),
        )
        connection.commit()

        rows = connection.execute(
            "SELECT tag_key FROM media_canonical_tags WHERE media_id = ? ORDER BY position",
            (MEDIA_ID,),
        ).fetchall()
        assert rows == [("mathematics",), ("compression",)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, ?, ?)",
                (MEDIA_ID, "mathematics", 2),
            )
    finally:
        connection.close()


def test_downgrade_from_0007_to_0006_removes_collection_columns_metadata_tables_remain(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "downgrade.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_COLLECTION_REVISION)
    _insert_populated_0004_rows(settings.database_path)

    connection = _connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('mathematics', 'Math', 1, 1)"
        )
        connection.execute(
            "INSERT INTO media_metadata (media_id, display_title, description, collection_key, processed_at_ms, created_at_ms, updated_at_ms) "
            "VALUES (?, 'Title', 'A description', 'processed', 100, 2, 2)",
            (MEDIA_ID,),
        )
        connection.execute(
            "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, 'mathematics', 0)",
            (MEDIA_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    connection = _connect(settings.database_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "DELETE FROM media_canonical_tags WHERE media_id = ?", (MEDIA_ID,)
        )
        connection.commit()
    finally:
        connection.close()

    _downgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)

    status = inspect_database_migration_status(settings)
    assert status.state == "behind"
    assert status.current_revision == TARGET_PREVIOUS_REVISION
    assert status.head_revision == CURRENT_HEAD_REVISION
    assert {"canonical_tags", "media_metadata", "media_canonical_tags"}.issubset(
        _table_names(settings.database_path)
    )
    columns = _columns(settings.database_path, "media_metadata")
    assert "collection_key" not in columns
    assert "processed_at_ms" not in columns
    assert "description" in columns
    assert "display_title" in columns
    connection = _connect(settings.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM physical_media_locations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM canonical_tags").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM media_metadata").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM media_canonical_tags").fetchone()[0] == 0
        row = connection.execute(
            "SELECT display_title FROM media_metadata WHERE media_id = ?", (MEDIA_ID,)
        ).fetchone()
        assert row is not None
        assert row[0] == "Title"
    finally:
        connection.close()

    upgraded = upgrade_database_to_head(settings)
    assert upgraded.current_revision == CURRENT_HEAD_REVISION
    assert {"canonical_tags", "media_metadata", "media_canonical_tags"}.issubset(
        _table_names(settings.database_path)
    )
    columns_after = _columns(settings.database_path, "media_metadata")
    assert "collection_key" in columns_after
    assert "processed_at_ms" in columns_after
