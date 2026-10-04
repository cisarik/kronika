"""Populated 0017 → 0018 analysis-run history migration evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine


DEVICE_ID = "12345678-1234-4234-8234-123456789abc"
LIBRARY_ID = "11111111-2222-4333-8433-555555555555"
MEDIA_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MEDIA_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
MEDIA_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
LOC_A = "a1111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOC_B = "b2222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LOC_C = "c3333333-cccc-4ccc-8ccc-cccccccccccc"
SUCCESS_RUN = "f49b1b1c-e398-4f2e-a51b-5258df9ea421"
PROVIDER_FAIL_RUN = "51c2f844-0240-4c26-8d6c-e185dd42332a"
LOCAL_FAIL_RUN = "8d996278-1e86-4288-9c06-723f47ad7147"
RESULT_JSON = json.dumps(
    {
        "title": "Synthetic",
        "description": "Synthetic durable suggestion.",
        "collection": "processed",
        "tags": ["synthetic"],
        "suggested_filename": "synthetic.mp4",
        "confidence": 0.9,
        "evidence": ["frame"],
        "uncertainties": [],
    },
    separators=(",", ":"),
)


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
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _seed_populated_0017(database_path: Path) -> None:
    connection = _connect(database_path)
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
        for media_id, location_id, kind, relative in (
            (MEDIA_A, LOC_A, "video", "clips/a.mp4"),
            (MEDIA_B, LOC_B, "image", "stills/b.jpg"),
            (MEDIA_C, LOC_C, "video", "clips/c.mp4"),
        ):
            connection.execute(
                """
                INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
                VALUES (?, ?, 10, 10)
                """,
                (media_id, kind),
            )
            connection.execute(
                """
                INSERT INTO physical_media_locations (
                    id, media_id, library_id, relative_path, availability,
                    observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, 'available', 12, 100, 10, 10)
                """,
                (location_id, media_id, LIBRARY_ID, relative),
            )
            connection.execute(
                """
                INSERT INTO media_metadata (
                    media_id, display_title, description, collection_key,
                    content_category, acquisition_source,
                    processed_at_ms, created_at_ms, updated_at_ms
                ) VALUES (?, 'Synthetic', 'Synthetic description', 'processed',
                          'general', 'unknown', 15, 10, 10)
                """,
                (media_id,),
            )

        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state, attempt_count,
                provider_id, model_id, prompt_version, result_schema_version, result_json,
                error_code, error_message, analysis_profile, reasoning_enabled,
                derivative_strategy, derivative_count, provider_submission_occurred,
                created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (
                ?, ?, ?, 'automatic_post_catalog', 'analyzed', 1,
                'nvidia-nim', 'fake-model', 'framenest-media-suggestion-v3',
                'framenest-media-suggestion-result-v1', ?,
                NULL, NULL, 'generic_media', 0, 'representative_frames_jpeg_v1', NULL, 1,
                10, 12, 14, 2
            )
            """,
            (SUCCESS_RUN, MEDIA_A, LOC_A, RESULT_JSON),
        )
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state, attempt_count,
                provider_id, model_id, prompt_version, result_schema_version, result_json,
                error_code, error_message, analysis_profile, reasoning_enabled,
                derivative_strategy, derivative_count, provider_submission_occurred,
                created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (
                ?, ?, ?, 'automatic_post_catalog', 'failed', 1,
                NULL, NULL, 'framenest-media-suggestion-v3', NULL, NULL,
                'PROVIDER_UNAVAILABLE', 'AI provider is temporarily unavailable.',
                'generic_media', 0, 'representative_frames_jpeg_v1', NULL, 1,
                20, 21, 22, 2
            )
            """,
            (PROVIDER_FAIL_RUN, MEDIA_B, LOC_B),
        )
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state, attempt_count,
                provider_id, model_id, prompt_version, result_schema_version, result_json,
                error_code, error_message, analysis_profile, reasoning_enabled,
                derivative_strategy, derivative_count, provider_submission_occurred,
                created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (
                ?, ?, ?, 'movie_identification', 'failed', 1,
                NULL, NULL, NULL, NULL, NULL,
                'ANALYSIS_FAILED', 'Movie identification failed.',
                'movie_identification', 1, 'bounded_contact_sheet_jpeg_v1', 1, 0,
                30, 31, 32, 2
            )
            """,
            (LOCAL_FAIL_RUN, MEDIA_C, LOC_C),
        )
        connection.commit()
    finally:
        connection.close()


def test_populated_0017_upgrades_to_0018_preserving_history(tmp_path: Path) -> None:
    database_path = tmp_path / "populated.sqlite3"
    _migrate(database_path, "0017")
    _seed_populated_0017(database_path)
    _migrate(database_path, "0018")

    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "0018"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        rows = {
            row["id"]: row
            for row in connection.execute(
                """
                SELECT id, media_id, analysis_definition, state, error_code,
                       result_json, provider_submission_occurred, supersedes_run_id,
                       attempt_count, analysis_profile, reasoning_enabled
                FROM media_analysis_runs
                """
            ).fetchall()
        }
        assert set(rows) == {SUCCESS_RUN, PROVIDER_FAIL_RUN, LOCAL_FAIL_RUN}
        assert rows[SUCCESS_RUN]["state"] == "analyzed"
        assert rows[SUCCESS_RUN]["result_json"] == RESULT_JSON
        assert rows[SUCCESS_RUN]["provider_submission_occurred"] == 1
        assert rows[SUCCESS_RUN]["supersedes_run_id"] is None
        assert rows[PROVIDER_FAIL_RUN]["state"] == "failed"
        assert rows[PROVIDER_FAIL_RUN]["error_code"] == "PROVIDER_UNAVAILABLE"
        assert rows[PROVIDER_FAIL_RUN]["provider_submission_occurred"] == 1
        assert rows[LOCAL_FAIL_RUN]["state"] == "failed"
        assert rows[LOCAL_FAIL_RUN]["error_code"] == "ANALYSIS_FAILED"
        assert rows[LOCAL_FAIL_RUN]["provider_submission_occurred"] == 0
        assert rows[LOCAL_FAIL_RUN]["analysis_definition"] == "movie_identification"

        index_sql = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index'
              AND name = 'uq_media_analysis_runs_active_media_definition'
            """
        ).fetchone()[0]
        assert "UNIQUE" in index_sql.upper()
        assert "pending" in index_sql
        assert "analyzing" in index_sql

        # Multiple terminal rows for same media/definition are now allowed.
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, error_code, error_message,
                created_at_ms, started_at_ms, completed_at_ms, version,
                supersedes_run_id
            ) VALUES (
                'dddddddd-dddd-4ddd-8ddd-dddddddddddd', ?, ?,
                'automatic_post_catalog', 'failed', 1,
                'PROVIDER_FAILED', 'provider failed',
                40, 41, 42, 1, ?
            )
            """,
            (MEDIA_B, LOC_B, PROVIDER_FAIL_RUN),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, created_at_ms, version
                ) VALUES (
                    'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', ?, ?,
                    'automatic_post_catalog', 'pending', 0, 50, 1
                )
                """,
                (MEDIA_A, LOC_A),
            )
            connection.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, created_at_ms, version
                ) VALUES (
                    'ffffffff-ffff-4fff-8fff-ffffffffffff', ?, ?,
                    'automatic_post_catalog', 'pending', 0, 51, 1
                )
                """,
                (MEDIA_A, LOC_A),
            )
        connection.rollback()

        # One active is fine.
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, created_at_ms, version, supersedes_run_id
            ) VALUES (
                'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', ?, ?,
                'automatic_post_catalog', 'pending', 0, 50, 1, ?
            )
            """,
            (MEDIA_A, LOC_A, SUCCESS_RUN),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, created_at_ms, version
                ) VALUES (
                    'ffffffff-ffff-4fff-8fff-ffffffffffff', ?, ?,
                    'automatic_post_catalog', 'pending', 0, 51, 1
                )
                """,
                (MEDIA_A, LOC_A),
            )
        connection.rollback()
    finally:
        connection.close()


def test_0018_downgrade_fails_closed_when_history_exists(tmp_path: Path) -> None:
    database_path = tmp_path / "history.sqlite3"
    _migrate(database_path, "0017")
    _seed_populated_0017(database_path)
    _migrate(database_path, "0018")
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, error_code, error_message,
                created_at_ms, started_at_ms, completed_at_ms, version,
                supersedes_run_id
            ) VALUES (
                'dddddddd-dddd-4ddd-8ddd-dddddddddddd', ?, ?,
                'automatic_post_catalog', 'failed', 1,
                'PROVIDER_FAILED', 'provider failed',
                40, 41, 42, 1, ?
            )
            """,
            (MEDIA_B, LOC_B, PROVIDER_FAIL_RUN),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception) as exc_info:
        _migrate(database_path, "0017", downgrade=True)
    assert "multiple runs" in str(exc_info.value).lower()


def test_0018_downgrade_restores_lifetime_unique_when_single_runs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "single.sqlite3"
    _migrate(database_path, "0017")
    _seed_populated_0017(database_path)
    _migrate(database_path, "0018")
    _migrate(database_path, "0017", downgrade=True)

    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "0017"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(media_analysis_runs)")
        }
        assert "supersedes_run_id" not in columns
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, error_code, error_message,
                    created_at_ms, started_at_ms, completed_at_ms, version
                ) VALUES (
                    'dddddddd-dddd-4ddd-8ddd-dddddddddddd', ?, ?,
                    'automatic_post_catalog', 'failed', 1,
                    'PROVIDER_FAILED', 'provider failed',
                    40, 41, 42, 1
                )
                """,
                (MEDIA_B, LOC_B),
            )
    finally:
        connection.close()
