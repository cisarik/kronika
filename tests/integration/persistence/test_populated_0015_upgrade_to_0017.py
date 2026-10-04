"""Populated revision-0015 upgrade through still-image and classification heads."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine


DEVICE_ID = "12345678-1234-4234-8234-123456789abc"
LIBRARY_ID = "11111111-2222-4333-8433-555555555555"
MEDIA_ID = "33333333-4444-4555-8555-777777777777"
LOCATION_ID = "44444444-5555-4666-8666-888888888888"
GIF_MEDIA_ID = "33333333-4444-4555-8555-777777777778"
GIF_LOCATION_ID = "44444444-5555-4666-8666-888888888889"
BYTE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UPLOAD_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PUBLICATION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ANALYSIS_RUN_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
TAG_KEY = "mathematics"
RESULT_JSON = json.dumps(
    {
        "title": "Synthetic",
        "description": "Synthetic durable suggestion.",
        "tags": ["Math"],
        "suggested_filename": "synthetic.mp4",
        "confidence": 0.9,
        "evidence": [],
        "uncertainties": [],
    },
    separators=(",", ":"),
)


def _settings(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _migrate(database_path: Path, revision: str) -> None:
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
                command.upgrade(config, revision)
    finally:
        dispose_engine(engine)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _seed_populated_0015(database_path: Path) -> None:
    connection = _connect(database_path)
    checksum = "a" * 64
    relative_target = f"{PUBLICATION_ID.replace('-', '')}.mp4"
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic Device')",
            (DEVICE_ID,),
        )
        connection.execute(
            """
            INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path)
            VALUES (?, ?, 'Synthetic Library', 'posix', '/tmp/synthetic-library')
            """,
            (LIBRARY_ID, DEVICE_ID),
        )
        connection.execute(
            """
            INSERT INTO media_byte_identities (
                id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms
            ) VALUES (?, 'sha256', 12, ?, 10)
            """,
            (BYTE_ID, checksum),
        )
        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES (?, 'video', 10, 20)
            """,
            (MEDIA_ID,),
        )
        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES (?, 'animated_image', 11, 21)
            """,
            (GIF_MEDIA_ID,),
        )
        connection.execute(
            """
            INSERT INTO physical_media_locations (
                id, media_id, library_id, relative_path, availability,
                observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, 'clips/synthetic.mp4', 'available', 12, 100, 10, 20)
            """,
            (LOCATION_ID, MEDIA_ID, LIBRARY_ID),
        )
        connection.execute(
            """
            INSERT INTO physical_media_locations (
                id, media_id, library_id, relative_path, availability,
                observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, 'clips/synthetic.gif', 'available', 9, 101, 11, 21)
            """,
            (GIF_LOCATION_ID, GIF_MEDIA_ID, LIBRARY_ID),
        )
        connection.execute(
            """
            INSERT INTO media_metadata (
                media_id, display_title, description, collection_key,
                processed_at_ms, created_at_ms, updated_at_ms
            ) VALUES (?, 'Synthetic Title', 'Synthetic description', 'processed', 15, 10, 20)
            """,
            (MEDIA_ID,),
        )
        connection.execute(
            """
            INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms)
            VALUES (?, 'Math', 1, 1)
            """,
            (TAG_KEY,),
        )
        connection.execute(
            """
            INSERT INTO media_canonical_tags (media_id, tag_key, position)
            VALUES (?, ?, 0)
            """,
            (MEDIA_ID, TAG_KEY),
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
                ?, 'cataloged', 'synthetic-upload-0001', 'synthetic.mp4',
                12, 12, 'sha256', ?, 'video', 'mp4', ?, NULL,
                10, 20, 100, NULL, 4
            )
            """,
            (UPLOAD_ID, checksum, BYTE_ID),
        )
        connection.execute(
            """
            INSERT INTO upload_publications (
                upload_id, publication_id, destination_id, relative_target,
                byte_identity_id, expected_size_bytes, checksum_algorithm, checksum_hex,
                validated_media_kind, validated_format, state, cleanup_state,
                created_at_ms, updated_at_ms, verified_at_ms, cleanup_completed_at_ms,
                version, media_id, media_location_id
            ) VALUES (
                ?, ?, ?, ?, ?, 12, 'sha256', ?, 'video', 'mp4',
                'verified', 'complete', 10, 20, 15, 18, 3, ?, ?
            )
            """,
            (
                UPLOAD_ID,
                PUBLICATION_ID,
                LIBRARY_ID,
                relative_target,
                BYTE_ID,
                checksum,
                MEDIA_ID,
                LOCATION_ID,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state, attempt_count,
                provider_id, model_id, prompt_version, result_schema_version, result_json,
                error_code, error_message, created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (
                ?, ?, ?, 'automatic_post_catalog', 'analyzed', 1,
                'nvidia-nim', 'fake-model', 'framenest-media-suggestion-v3',
                'framenest-media-suggestion-result-v1', ?,
                NULL, NULL, 10, 12, 14, 2
            )
            """,
            (ANALYSIS_RUN_ID, MEDIA_ID, LOCATION_ID, RESULT_JSON),
        )
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_populated_0015_upgrades_to_0017_preserving_identities_and_relationships(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    database_path = tmp_path / "populated-0015.sqlite3"
    _migrate(database_path, "0015")
    _seed_populated_0015(database_path)

    status = upgrade_database_to_head(_settings(database_path))

    assert status.current_revision == status.head_revision == "0035"
    connection = _connect(database_path)
    try:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        media_rows = connection.execute(
            "SELECT id, media_kind FROM logical_media ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["media_kind"]) for row in media_rows] == [
            (MEDIA_ID, "video"),
            (GIF_MEDIA_ID, "animated_image"),
        ]

        location_rows = connection.execute(
            """
            SELECT id, media_id, library_id, relative_path
            FROM physical_media_locations ORDER BY id
            """
        ).fetchall()
        assert [
            (row["id"], row["media_id"], row["library_id"], row["relative_path"])
            for row in location_rows
        ] == [
            (LOCATION_ID, MEDIA_ID, LIBRARY_ID, "clips/synthetic.mp4"),
            (GIF_LOCATION_ID, GIF_MEDIA_ID, LIBRARY_ID, "clips/synthetic.gif"),
        ]

        metadata = connection.execute(
            """
            SELECT media_id, display_title, description, collection_key,
                   content_category, acquisition_source
            FROM media_metadata WHERE media_id = ?
            """,
            (MEDIA_ID,),
        ).fetchone()
        assert metadata["display_title"] == "Synthetic Title"
        assert metadata["description"] == "Synthetic description"
        assert metadata["collection_key"] == "processed"
        assert metadata["content_category"] == "general"
        assert metadata["acquisition_source"] == "unknown"

        tags = connection.execute(
            """
            SELECT tag_key, position FROM media_canonical_tags
            WHERE media_id = ? ORDER BY position
            """,
            (MEDIA_ID,),
        ).fetchall()
        assert [(row["tag_key"], row["position"]) for row in tags] == [(TAG_KEY, 0)]

        publication = connection.execute(
            """
            SELECT upload_id, publication_id, media_id, media_location_id,
                   validated_media_kind, validated_format, state
            FROM upload_publications WHERE upload_id = ?
            """,
            (UPLOAD_ID,),
        ).fetchone()
        assert publication["publication_id"] == PUBLICATION_ID
        assert publication["media_id"] == MEDIA_ID
        assert publication["media_location_id"] == LOCATION_ID
        assert publication["validated_media_kind"] == "video"
        assert publication["validated_format"] == "mp4"
        assert publication["state"] == "verified"

        analysis = connection.execute(
            """
            SELECT id, media_id, media_location_id, analysis_definition, state,
                   result_json, analysis_profile, reasoning_enabled, supersedes_run_id
            FROM media_analysis_runs WHERE id = ?
            """,
            (ANALYSIS_RUN_ID,),
        ).fetchone()
        assert analysis["media_id"] == MEDIA_ID
        assert analysis["media_location_id"] == LOCATION_ID
        assert analysis["analysis_definition"] == "automatic_post_catalog"
        assert analysis["state"] == "analyzed"
        assert analysis["result_json"] == RESULT_JSON
        assert analysis["analysis_profile"] == "generic_media"
        assert analysis["reasoning_enabled"] == 0
        assert analysis["supersedes_run_id"] is None

        assert (
            connection.execute("SELECT COUNT(*) FROM media_genres").fetchone()[0] == 0
        )
        logical_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'logical_media'"
        ).fetchone()[0]
        assert "image" in logical_sql
    finally:
        connection.close()
