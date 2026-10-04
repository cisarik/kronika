"""Migration evidence for content classification and movie-identification provenance."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kronika.configuration import KronikaSettings
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


def _seed_at_0016(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Dev')"
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
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES ('dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'image', 30, 40)
            """
        )
        connection.execute(
            """
            INSERT INTO physical_media_locations (
                id, media_id, library_id, relative_path, availability,
                observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
            ) VALUES (
                'ffffffff-ffff-4fff-8fff-ffffffffffff',
                'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
                'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                'existing.mp4', 'available', 8, 1, 10, 20
            )
            """
        )
        connection.execute(
            """
            INSERT INTO media_metadata (
                media_id, display_title, description, collection_key, processed_at_ms,
                created_at_ms, updated_at_ms
            ) VALUES (
                'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'Existing', NULL, NULL, NULL, 10, 20
            )
            """
        )
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state, attempt_count,
                provider_id, model_id, prompt_version, result_schema_version, result_json,
                error_code, error_message, created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (
                'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
                'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
                'ffffffff-ffff-4fff-8fff-ffffffffffff',
                'automatic_post_catalog', 'failed', 1,
                NULL, NULL, NULL, NULL, NULL,
                'PREPARATION_FAILED', 'Local media preparation failed.',
                10, 11, 12, 2
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_classification_migration_defaults_and_preserves_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0016")
    _seed_at_0016(database_path)
    _migrate(database_path, "0017")

    connection = _connect(database_path)
    try:
        row = connection.execute(
            "SELECT content_category, acquisition_source, display_title FROM media_metadata"
        ).fetchone()
        assert row == ("general", "unknown", "Existing")
        kinds = {
            item[0]
            for item in connection.execute("SELECT media_kind FROM logical_media").fetchall()
        }
        assert kinds == {"video", "image"}
        provenance = connection.execute(
            """
            SELECT analysis_profile, reasoning_enabled, provider_submission_occurred
            FROM media_analysis_runs
            """
        ).fetchone()
        assert provenance == ("generic_media", 0, 0)
        connection.execute(
            "UPDATE media_metadata SET content_category = 'movie' WHERE media_id = ?",
            ("cccccccc-cccc-4ccc-8ccc-cccccccccccc",),
        )
        connection.execute(
            """
            INSERT INTO media_genres (media_id, genre_key, position)
            VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'drama', 0)
            """
        )
        connection.commit()
        assert connection.execute("SELECT genre_key FROM media_genres").fetchone()[0] == "drama"
    finally:
        connection.close()

    _migrate(database_path, "0016", downgrade=True)
    connection = _connect(database_path)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(media_metadata)").fetchall()
        }
        assert "content_category" not in columns
        assert connection.execute(
            "SELECT display_title FROM media_metadata"
        ).fetchone()[0] == "Existing"
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'media_genres'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()
