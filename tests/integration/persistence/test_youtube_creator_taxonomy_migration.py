"""Migration evidence for YouTube category and creator attribution (0027)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine


def _migrate(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    from alembic import command
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


def _seed_at_0026(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES "
            "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Dev')"
        )
        connection.execute(
            """
            INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path)
            VALUES (
                'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                'Lib', 'posix', '/tmp/synthetic'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'video', 10, 20)
            """
        )
        connection.execute(
            """
            INSERT INTO media_metadata (
                media_id, display_title, description, content_category,
                acquisition_source, collection_key, processed_at_ms,
                created_at_ms, updated_at_ms
            ) VALUES (
                'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'Existing Title', NULL,
                'general', 'youtube_manual_claim', NULL, NULL, 10, 20
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_upgrade_0026_to_0027_preserves_rows_without_backfill(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0026")
    _seed_at_0026(database_path)
    _migrate(database_path, "0027")

    connection = _connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT display_title, content_category, acquisition_source,
                   creator_attribution_kind, creator_stable_id,
                   creator_handle, creator_display_name
            FROM media_metadata
            """
        ).fetchone()
        assert row == (
            "Existing Title",
            "general",
            "youtube_manual_claim",
            None,
            None,
            None,
            None,
        )
        columns = {
            item[1]
            for item in connection.execute("PRAGMA table_info(media_metadata)").fetchall()
        }
        assert "creator_attribution_kind" in columns
        assert "creator_stable_id" in columns
        assert "creator_handle" in columns
        assert "creator_display_name" in columns
        index_names = {
            item[1]
            for item in connection.execute("PRAGMA index_list(media_metadata)").fetchall()
        }
        assert "ix_media_metadata_creator_stable" in index_names
        assert "ix_media_metadata_creator_handle" in index_names
        connection.execute(
            """
            UPDATE media_metadata
            SET content_category = 'youtube',
                creator_attribution_kind = 'youtube_channel',
                creator_stable_id = 'UC123',
                creator_display_name = 'Channel'
            WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
            """
        )
        connection.commit()
        updated = connection.execute(
            "SELECT content_category, creator_stable_id FROM media_metadata"
        ).fetchone()
        assert updated == ("youtube", "UC123")
    finally:
        connection.close()


def test_creator_check_rejects_inconsistent_attribution(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0027")
    _seed_at_0026(database_path)

    def _reject(sql: str) -> None:
        connection = _connect(database_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
                connection.commit()
        finally:
            connection.close()

    _reject(
        """
        UPDATE media_metadata
        SET creator_attribution_kind = 'youtube_channel'
        WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
        """
    )
    _reject(
        """
        UPDATE media_metadata
        SET creator_stable_id = 'UC123'
        WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
        """
    )
    _reject(
        """
        UPDATE media_metadata
        SET content_category = 'tiktok'
        WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
        """
    )


def test_safe_downgrade_succeeds_when_new_fields_unused(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0026")
    _seed_at_0026(database_path)
    _migrate(database_path, "0027")
    _migrate(database_path, "0026", downgrade=True)
    connection = _connect(database_path)
    try:
        columns = {
            item[1]
            for item in connection.execute("PRAGMA table_info(media_metadata)").fetchall()
        }
        assert "creator_attribution_kind" not in columns
        row = connection.execute(
            "SELECT display_title, content_category, acquisition_source FROM media_metadata"
        ).fetchone()
        assert row == ("Existing Title", "general", "youtube_manual_claim")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_downgrade_refuses_when_youtube_or_creator_data_exists(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0026")
    _seed_at_0026(database_path)
    _migrate(database_path, "0027")
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            UPDATE media_metadata
            SET content_category = 'youtube',
                creator_attribution_kind = 'youtube_channel',
                creator_stable_id = 'UC123',
                creator_display_name = 'Channel'
            WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
            """
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception, match="Refusing creator-taxonomy downgrade"):
        _migrate(database_path, "0026", downgrade=True)
