"""Migration evidence for first-class still-image media kinds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine


def _settings(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


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


def _seed_preimage_rows(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO media_byte_identities (
                id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms
            ) VALUES (?, 'sha256', 8, ?, 10)
            """,
            ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO upload_sessions (
                id, state, storage_key, display_filename,
                declared_size_bytes, received_size_bytes,
                checksum_algorithm, checksum_hex,
                validated_media_kind, validated_format, byte_identity_id,
                duplicate_disposition,
                created_at_ms, updated_at_ms, expires_at_ms,
                failure_code, version
            ) VALUES (
                '11111111-1111-4111-8111-111111111111', 'cataloged',
                'synthetic-upload-0001', 'synthetic.mp4', 8, 8,
                'sha256', ?, 'video', 'mp4',
                'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', NULL,
                10, 20, 100, NULL, 4
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'video', 10, 20)
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_still_image_migration_preserves_rows_and_accepts_image_pairs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0015")
    _seed_preimage_rows(database_path)
    _migrate(database_path, "0016")

    connection = _connect(database_path)
    try:
        logical_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'logical_media'"
        ).fetchone()[0]
        assert "image" in logical_sql
        session_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'upload_sessions'"
        ).fetchone()[0]
        assert "validated_format = 'jpg'" in session_sql
        assert "validated_format = 'png'" in session_sql
        publication_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'upload_publications'"
        ).fetchone()[0]
        assert ".jpg" in publication_sql
        assert ".png" in publication_sql

        preserved = connection.execute(
            "SELECT media_kind FROM logical_media"
        ).fetchone()[0]
        assert preserved == "video"

        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES ('dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'image', 30, 40)
            """
        )
        connection.execute(
            """
            INSERT INTO upload_sessions (
                id, state, storage_key, display_filename,
                declared_size_bytes, received_size_bytes,
                checksum_algorithm, checksum_hex,
                validated_media_kind, validated_format, byte_identity_id,
                duplicate_disposition,
                created_at_ms, updated_at_ms, expires_at_ms,
                failure_code, version
            ) VALUES (
                '22222222-2222-4222-8222-222222222222', 'publish_pending',
                'synthetic-upload-0002', 'still.png', 8, 8,
                'sha256', ?, 'image', 'png',
                'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', NULL,
                30, 40, 200, NULL, 1
            )
            """,
            ("b" * 64,),
        )
        connection.commit()
        image_count = connection.execute(
            "SELECT COUNT(*) FROM logical_media WHERE media_kind = 'image'"
        ).fetchone()[0]
        assert image_count == 1
        connection.execute(
            "DELETE FROM upload_sessions WHERE id = '22222222-2222-4222-8222-222222222222'"
        )
        connection.execute(
            "DELETE FROM logical_media WHERE id = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'"
        )
        connection.commit()
    finally:
        connection.close()

    _migrate(database_path, "0015", downgrade=True)
