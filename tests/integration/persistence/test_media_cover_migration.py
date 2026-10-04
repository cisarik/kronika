"""Migration evidence for durable manually selected accepted covers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings

MEDIA_A = "11111111-1111-4111-8111-111111111111"
MEDIA_B = "22222222-2222-4222-8222-222222222222"
LOCATION_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_A = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
DEVICE_A = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

DIGEST = "a" * 64
SOURCE_OBS = "b" * 64


def _migrate(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.migrations import _alembic_config

    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                if downgrade:
                    command.downgrade(config, revision)
                else:
                    command.upgrade(config, revision)
    finally:
        dispose_engine(engine)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _seed_catalog(connection: sqlite3.Connection, *, with_location: bool = True) -> None:
    connection.executemany(
        "INSERT INTO logical_media "
        "(id, media_kind, created_at_ms, updated_at_ms) VALUES (?, 'video', ?, ?)",
        (
            (MEDIA_A, 10, 10),
            (MEDIA_B, 20, 20),
        ),
    )
    if with_location:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'device')",
            (DEVICE_A,),
        )
        connection.execute(
            "INSERT INTO libraries "
            "(id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, 'library', 'posix', '/media/movies')",
            (LIBRARY_A, DEVICE_A),
        )
        connection.execute(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            " observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, 'items/clip.mp4', 'available', 12345, 123, 5, 5)",
            (LOCATION_A, MEDIA_A, LIBRARY_A),
        )
    connection.commit()


def _insert_cover(
    connection: sqlite3.Connection,
    media_id: str,
    *,
    source_location_id: str | None,
    revision: int = 1,
    source_kind: str = "mp4",
    timestamp_ms: int = 500,
    duration_ms: int | None = 1000,
) -> None:
    connection.execute(
        "INSERT INTO media_covers ("
        " media_id, source_location_id, source_reference, source_kind, "
        " source_timestamp_ms, source_size_bytes, source_mtime_ns, "
        " source_duration_ms, source_observation_version, source_observation_digest, "
        " artifact_profile, artifact_media_type, artifact_digest, artifact_width, "
        " artifact_height, artifact_byte_size, revision, accepted_at_ms) "
        "VALUES (?, ?, ?, ?, ?, 12345, 123, ?, "
        " 'cover-source-observation-v1', ?, 'durable-cover-jpeg-v1', "
        " 'image/jpeg', ?, 512, 288, 20000, ?, 100)",
        (
            media_id,
            source_location_id,
            f"location:{source_location_id}" if source_location_id else "location:" + "0" * 36,
            source_kind,
            timestamp_ms,
            duration_ms,
            SOURCE_OBS,
            DIGEST,
            revision,
        ),
    )
    connection.commit()


def _media_covers_sql(connection: sqlite3.Connection) -> str:
    return connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'media_covers'"
    ).fetchone()[0]


def test_empty_and_populated_0021_databases_upgrade_without_backfill(
    tmp_path: Path,
) -> None:
    empty_path = tmp_path / "empty.sqlite3"
    populated_path = tmp_path / "populated.sqlite3"
    _migrate(empty_path, "0022")
    _migrate(populated_path, "0021")
    connection = _connect(populated_path)
    try:
        _seed_catalog(connection)
    finally:
        connection.close()
    _migrate(populated_path, "0022")

    empty = _connect(empty_path)
    populated = _connect(populated_path)
    try:
        assert empty.execute("SELECT COUNT(*) FROM media_covers").fetchone() == (0,)
        assert populated.execute("SELECT COUNT(*) FROM media_covers").fetchone() == (0,)
        assert populated.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert populated.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        empty.close()
        populated.close()


def test_one_accepted_cover_per_medium_and_schema_constraints_hold(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraints.sqlite3"
    _migrate(database_path, "0022")
    connection = _connect(database_path)
    try:
        _seed_catalog(connection)
        _insert_cover(connection, MEDIA_A, source_location_id=LOCATION_A)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO media_covers ("
                " media_id, source_reference, source_kind, source_timestamp_ms, "
                " source_size_bytes, source_observation_version, "
                " source_observation_digest, artifact_profile, artifact_media_type, "
                " artifact_digest, artifact_width, artifact_height, "
                " artifact_byte_size, revision, accepted_at_ms) "
                "VALUES (?, 'location:' || '0' * 36, "
                " 'mp4', 600, 100, 'cover-source-observation-v1', ?, "
                " 'durable-cover-jpeg-v1', 'image/jpeg', ?, 100, 100, 100, 1, 100)",
                (MEDIA_A, SOURCE_OBS, DIGEST),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO media_covers ("
                " media_id, source_reference, source_kind, source_timestamp_ms, "
                " source_size_bytes, source_observation_version, "
                " source_observation_digest, artifact_profile, artifact_media_type, "
                " artifact_digest, artifact_width, artifact_height, "
                " artifact_byte_size, revision, accepted_at_ms) "
                "VALUES (?, 'location:' || '0' * 36, "
                " 'jpeg', 600, 100, 'cover-source-observation-v1', ?, "
                " 'durable-cover-jpeg-v1', 'image/jpeg', ?, 100, 100, 100, 1, 100)",
                (MEDIA_B, SOURCE_OBS, DIGEST),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_covers SET revision = 0 WHERE media_id = ?",
                (MEDIA_A,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_covers SET artifact_digest = 'zz' WHERE media_id = ?",
                (MEDIA_A,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_covers SET source_observation_version = 'stale-v1' "
                "WHERE media_id = ?",
                (MEDIA_A,),
            )
        connection.rollback()

        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(media_covers)"
        ).fetchall()
        by_table = {row[2]: row for row in foreign_keys}
        assert by_table["logical_media"][6] == "CASCADE"
        assert by_table["physical_media_locations"][6] == "SET NULL"
    finally:
        connection.close()


def test_physical_location_deletion_sets_null_and_keeps_cover(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "location-delete.sqlite3"
    _migrate(database_path, "0022")
    connection = _connect(database_path)
    try:
        _seed_catalog(connection)
        _insert_cover(connection, MEDIA_A, source_location_id=LOCATION_A)
        connection.execute(
            "DELETE FROM physical_media_locations WHERE id = ?",
            (LOCATION_A,),
        )
        connection.commit()
        row = connection.execute(
            "SELECT source_location_id, source_reference FROM media_covers "
            "WHERE media_id = ?",
            (MEDIA_A,),
        ).fetchone()
        assert row[0] is None
        assert row[1] == f"location:{LOCATION_A}"
    finally:
        connection.close()


def test_logical_media_deletion_cascades_cover_row(tmp_path: Path) -> None:
    database_path = tmp_path / "media-delete.sqlite3"
    _migrate(database_path, "0022")
    connection = _connect(database_path)
    try:
        _seed_catalog(connection, with_location=False)
        _insert_cover(connection, MEDIA_A, source_location_id=None)
        connection.execute("DELETE FROM logical_media WHERE id = ?", (MEDIA_A,))
        assert connection.execute(
            "SELECT COUNT(*) FROM media_covers"
        ).fetchone() == (0,)
        connection.commit()
    finally:
        connection.close()


def test_downgrade_is_guarded_against_populated_cover_state(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.sqlite3"
    _migrate(empty_path, "0022")
    _migrate(empty_path, "0021", downgrade=True)
    empty = _connect(empty_path)
    try:
        assert empty.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'media_covers'"
        ).fetchone() is None
        assert empty.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0021",
        )
    finally:
        empty.close()

    populated_path = tmp_path / "populated.sqlite3"
    _migrate(populated_path, "0022")
    connection = _connect(populated_path)
    try:
        _seed_catalog(connection)
        _insert_cover(connection, MEDIA_A, source_location_id=LOCATION_A)
    finally:
        connection.close()
    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade durable covers while accepted media covers exist",
    ):
        _migrate(populated_path, "0021", downgrade=True)
    populated = _connect(populated_path)
    try:
        assert populated.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0022",
        )
        assert populated.execute(
            "SELECT COUNT(*) FROM media_covers"
        ).fetchone() == (1,)
    finally:
        populated.close()


def test_packaged_head_is_0023(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = KronikaSettings(database_path=tmp_path / "head.sqlite3", _env_file=None)
    status = upgrade_database_to_head(settings)
    assert status.current_revision == status.head_revision == "0035"
    assert inspect_database_migration_status(settings) == status


def test_0023_upgrade_widens_source_kind_and_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    _migrate(database_path, "0022")
    connection = _connect(database_path)
    try:
        _seed_catalog(connection)
        _insert_cover(connection, MEDIA_A, source_location_id=LOCATION_A, source_kind="mp4")
        _insert_cover(connection, MEDIA_B, source_location_id=None, source_kind="gif")
        preserved = connection.execute(
            "SELECT media_id, source_reference, source_kind, source_timestamp_ms, "
            " source_size_bytes, source_mtime_ns, source_duration_ms, "
            " source_observation_version, source_observation_digest, "
            " artifact_profile, artifact_media_type, artifact_digest, "
            " artifact_width, artifact_height, artifact_byte_size, revision, "
            " accepted_at_ms FROM media_covers ORDER BY media_id"
        ).fetchall()
    finally:
        connection.close()

    _migrate(database_path, "0023")
    connection = _connect(database_path)
    try:
        sql = _media_covers_sql(connection)
        assert "'gif'" in sql and "'mp4'" in sql and "'image'" in sql
        after = connection.execute(
            "SELECT media_id, source_reference, source_kind, source_timestamp_ms, "
            " source_size_bytes, source_mtime_ns, source_duration_ms, "
            " source_observation_version, source_observation_digest, "
            " artifact_profile, artifact_media_type, artifact_digest, "
            " artifact_width, artifact_height, artifact_byte_size, revision, "
            " accepted_at_ms FROM media_covers ORDER BY media_id"
        ).fetchall()
        assert after == preserved
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        # A timeless still-image cover row persists at head.
        connection.execute(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES ('99999999-9999-4999-8999-999999999999', 'image', 1, 1)"
        )
        _insert_cover(
            connection,
            "99999999-9999-4999-8999-999999999999",
            source_location_id=None,
            source_kind="image",
            timestamp_ms=0,
            duration_ms=None,
        )
        row = connection.execute(
            "SELECT source_kind, source_timestamp_ms, source_duration_ms "
            "FROM media_covers WHERE media_id = '99999999-9999-4999-8999-999999999999'"
        ).fetchone()
        assert row == ("image", 0, None)
        # One accepted cover per logical medium remains enforced.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO media_covers ("
                " media_id, source_reference, source_kind, source_timestamp_ms, "
                " source_size_bytes, source_observation_version, "
                " source_observation_digest, artifact_profile, artifact_media_type, "
                " artifact_digest, artifact_width, artifact_height, "
                " artifact_byte_size, revision, accepted_at_ms) "
                "VALUES ('99999999-9999-4999-8999-999999999999', "
                " 'location:' || '0' * 36, 'image', 0, 100, "
                " 'cover-source-observation-v1', ?, 'durable-cover-jpeg-v1', "
                " 'image/jpeg', ?, 100, 100, 100, 2, 101)",
                (SOURCE_OBS, DIGEST),
            )
        connection.rollback()
    finally:
        connection.close()


def test_0023_downgrade_is_guarded_against_image_source_rows(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.sqlite3"
    _migrate(empty_path, "0023")
    _migrate(empty_path, "0022", downgrade=True)
    empty = _connect(empty_path)
    try:
        assert empty.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0022",)
        assert "'image'" not in _media_covers_sql(empty)
    finally:
        empty.close()

    gif_only_path = tmp_path / "gif-only.sqlite3"
    _migrate(gif_only_path, "0023")
    connection = _connect(gif_only_path)
    try:
        _seed_catalog(connection)
        _insert_cover(connection, MEDIA_A, source_location_id=LOCATION_A, source_kind="mp4")
        _insert_cover(connection, MEDIA_B, source_location_id=None, source_kind="gif")
    finally:
        connection.close()
    _migrate(gif_only_path, "0022", downgrade=True)
    connection = _connect(gif_only_path)
    try:
        assert "'image'" not in _media_covers_sql(connection)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_covers"
        ).fetchone() == (2,)
    finally:
        connection.close()

    image_path = tmp_path / "image.sqlite3"
    _migrate(image_path, "0023")
    connection = _connect(image_path)
    try:
        _seed_catalog(connection)
        _insert_cover(
            connection,
            MEDIA_A,
            source_location_id=LOCATION_A,
            source_kind="image",
            timestamp_ms=0,
            duration_ms=None,
        )
    finally:
        connection.close()
    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade still-image covers while image-source covers exist",
    ):
        _migrate(image_path, "0022", downgrade=True)
    connection = _connect(image_path)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0023",)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_covers"
        ).fetchone() == (1,)
    finally:
        connection.close()
