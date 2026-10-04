"""Migration evidence for companion review inbox schema (0031)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

CLAIM_MEDIA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
RUN_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
LOCATION_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
RESULT_JSON = (
    '{"collection":"memes","confidence":0.9,"description":"A description.",'
    '"evidence":["visible subject"],"suggested_filename":"clip.gif",'
    '"tags":["alpha"],"title":"Stored title","uncertainties":[]}'
)


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


def _table_sql(connection: sqlite3.Connection, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _seed_populated_0030(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'Dev')",
            (DEVICE_ID,),
        )
        connection.execute(
            """
            INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path)
            VALUES (?, ?, 'Lib', 'posix', '/tmp/synthetic')
            """,
            (LIBRARY_ID, DEVICE_ID),
        )
        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES (?, 'video', 10, 20)
            """,
            (CLAIM_MEDIA,),
        )
        connection.execute(
            """
            INSERT INTO physical_media_locations (
                id, media_id, library_id, relative_path, availability,
                observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, 'clip.mp4', 'available', 8, NULL, 10, 20)
            """,
            (LOCATION_ID, CLAIM_MEDIA, LIBRARY_ID),
        )
        connection.execute(
            """
            INSERT INTO media_content_publications (
                media_id, published_at_ms, publication_origin
            ) VALUES (?, 10, 'legacy_backfill')
            """,
            (CLAIM_MEDIA,),
        )
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, provider_id, model_id, prompt_version,
                result_schema_version, result_json, error_code, error_message,
                analysis_profile, created_at_ms, started_at_ms, completed_at_ms,
                version
            ) VALUES (
                ?, ?, ?, 'automatic_post_catalog', 'analyzed', 1,
                'nvidia-nim', 'test-model', 'framenest-media-suggestion-v3',
                'framenest-media-suggestion-result-v1', ?, NULL, NULL,
                'generic_media', 10, 11, 12, 2
            )
            """,
            (RUN_ID, CLAIM_MEDIA, LOCATION_ID, RESULT_JSON),
        )
        connection.commit()
    finally:
        connection.close()


def test_head_is_0034() -> None:
    from kronika.infrastructure.persistence.migrations import _alembic_config

    with _alembic_config(
        "kronika.infrastructure.persistence.alembic_environment"
    ) as config:
        from alembic.script import ScriptDirectory

        scripts = ScriptDirectory.from_config(config)
        assert scripts.get_current_head() == "0035"


def test_empty_database_upgrades_to_0031(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.sqlite3"
    _migrate(database_path, "0031")
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "companion_review_open_states" in tables
        assert "companion_review_field_sources" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0031",)
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_open_states"
        ).fetchone() == (0,)
        indexes = {
            row[1]
            for row in connection.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "ix_companion_review_open_states_opened_run_id" in indexes
        assert "ix_companion_review_field_sources_analysis_run_id" in indexes
        assert "ix_companion_review_successful_inbox" in indexes
        assert "ix_companion_review_per_media_history" in indexes
        origin_sql = _table_sql(connection, "media_content_publications")
        assert "companion_review" in origin_sql
    finally:
        connection.close()


def test_populated_0030_upgrade_preserves_catalog_and_adds_review_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0030")
    _seed_populated_0030(database_path)
    _migrate(database_path, "0031")
    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT publication_origin FROM media_content_publications"
        ).fetchone() == ("legacy_backfill",)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_analysis_runs"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_open_states"
        ).fetchone() == (0,)
        connection.execute(
            """
            INSERT INTO media_content_publications (
                media_id, published_at_ms, publication_origin
            ) VALUES (?, 99, 'companion_review')
            ON CONFLICT(media_id) DO UPDATE SET
                publication_origin='companion_review', published_at_ms=99
            """,
            (CLAIM_MEDIA,),
        )
        connection.commit()
        stored = connection.execute(
            "SELECT publication_origin FROM media_content_publications"
        ).fetchone()
        assert stored == ("companion_review",)
        open_fk = connection.execute(
            "PRAGMA foreign_key_list(companion_review_open_states)"
        ).fetchall()
        assert {item[6] for item in open_fk} == {"CASCADE"}
        source_fk = connection.execute(
            "PRAGMA foreign_key_list(companion_review_field_sources)"
        ).fetchall()
        assert {item[6] for item in source_fk} == {"CASCADE"}
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_content_publications "
                "SET publication_origin = 'other' WHERE media_id = ?",
                (CLAIM_MEDIA,),
            )
            connection.commit()
        connection.rollback()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        connection.close()


def test_empty_0031_downgrade_restores_0030_contract(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0030")
    _seed_populated_0030(database_path)
    _migrate(database_path, "0031")
    _migrate(database_path, "0030", downgrade=True)
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "companion_review_open_states" not in tables
        assert "companion_review_field_sources" not in tables
        origin_sql = _table_sql(connection, "media_content_publications")
        assert "companion_review" not in origin_sql
        assert "legacy_backfill" in origin_sql
        assert connection.execute(
            "SELECT COUNT(*) FROM media_analysis_runs"
        ).fetchone() == (1,)
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0030",)
    finally:
        connection.close()


def test_downgrade_refuses_opened_row(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0030")
    _seed_populated_0030(database_path)
    _migrate(database_path, "0031")
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO companion_review_open_states (
                actor_login_key, media_id, opened_run_id, opened_at_ms
            ) VALUES ('admin@example.com', ?, ?, 20)
            """,
            (CLAIM_MEDIA, RUN_ID),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception, match="companion-review inbox downgrade"):
        _migrate(database_path, "0030", downgrade=True)


def test_downgrade_refuses_companion_review_publication(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0030")
    _seed_populated_0030(database_path)
    _migrate(database_path, "0031")
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            UPDATE media_content_publications
            SET publication_origin = 'companion_review'
            WHERE media_id = ?
            """,
            (CLAIM_MEDIA,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception, match="companion-review inbox downgrade"):
        _migrate(database_path, "0030", downgrade=True)


def test_empty_database_upgrades_to_0032(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.sqlite3"
    _migrate(database_path, "0032")
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "companion_review_tag_sources" in tables
        assert "companion_review_field_sources" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0032",)
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_tag_sources"
        ).fetchone() == (0,)
        indexes = {
            row[1]
            for row in connection.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "ix_companion_review_tag_sources_analysis_run_id" in indexes
    finally:
        connection.close()


def test_populated_0031_upgrade_does_not_backfill_tag_sources(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0030")
    _seed_populated_0030(database_path)
    _migrate(database_path, "0031")
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO media_metadata (
                media_id, display_title, description, created_at_ms, updated_at_ms,
                content_category, acquisition_source
            ) VALUES (?, 'Title', 'Desc', 10, 10, 'general', 'manual_upload')
            """,
            (CLAIM_MEDIA,),
        )
        connection.execute(
            """
            INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms)
            VALUES ('alpha', 'Alpha', 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO media_canonical_tags (media_id, tag_key, position)
            VALUES (?, 'alpha', 0)
            """,
            (CLAIM_MEDIA,),
        )
        connection.commit()
    finally:
        connection.close()
    _migrate(database_path, "0032")
    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM media_canonical_tags"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_tag_sources"
        ).fetchone() == (0,)
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0032",)
        fks = connection.execute(
            "PRAGMA foreign_key_list(companion_review_tag_sources)"
        ).fetchall()
        by_table = {item[2]: item[6] for item in fks}
        assert by_table["media_metadata"] == "CASCADE"
        assert by_table["canonical_tags"] == "RESTRICT"
        assert by_table["media_analysis_runs"] == "CASCADE"
        assert "media_canonical_tags" not in by_table
    finally:
        connection.close()


def test_0032_foreign_keys_and_assignment_delete_preserve_sources(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0032")
    _seed_populated_0030(database_path)
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO media_metadata (
                media_id, display_title, description, created_at_ms, updated_at_ms,
                content_category, acquisition_source
            ) VALUES (?, 'Title', 'Desc', 10, 10, 'general', 'manual_upload')
            """,
            (CLAIM_MEDIA,),
        )
        connection.execute(
            """
            INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms)
            VALUES ('alpha', 'Alpha', 1, 1), ('beta', 'Beta', 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO media_canonical_tags (media_id, tag_key, position)
            VALUES (?, 'alpha', 0)
            """,
            (CLAIM_MEDIA,),
        )
        connection.execute(
            """
            INSERT INTO companion_review_tag_sources (
                media_id, tag_key, analysis_run_id, applied_by_login_key, applied_at_ms
            ) VALUES (?, 'alpha', ?, 'admin@example.com', 20)
            """,
            (CLAIM_MEDIA, RUN_ID),
        )
        connection.commit()
        connection.execute(
            "DELETE FROM media_canonical_tags WHERE media_id = ? AND tag_key = 'alpha'",
            (CLAIM_MEDIA,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_tag_sources"
        ).fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM canonical_tags WHERE key = 'alpha'")
            connection.commit()
        connection.rollback()
        connection.execute(
            "DELETE FROM media_analysis_runs WHERE id = ?",
            (RUN_ID,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_tag_sources"
        ).fetchone() == (0,)
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, provider_id, model_id, prompt_version,
                result_schema_version, result_json, error_code, error_message,
                analysis_profile, created_at_ms, started_at_ms, completed_at_ms,
                version
            ) VALUES (
                ?, ?, ?, 'automatic_post_catalog', 'analyzed', 1,
                'nvidia-nim', 'test-model', 'framenest-media-suggestion-v3',
                'framenest-media-suggestion-result-v1', ?, NULL, NULL,
                'generic_media', 10, 11, 12, 2
            )
            """,
            (RUN_ID, CLAIM_MEDIA, LOCATION_ID, RESULT_JSON),
        )
        connection.execute(
            """
            INSERT INTO companion_review_tag_sources (
                media_id, tag_key, analysis_run_id, applied_by_login_key, applied_at_ms
            ) VALUES (?, 'beta', ?, 'admin@example.com', 21)
            """,
            (CLAIM_MEDIA, RUN_ID),
        )
        connection.commit()
        connection.execute(
            "DELETE FROM media_metadata WHERE media_id = ?",
            (CLAIM_MEDIA,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_review_tag_sources"
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        connection.close()


def test_empty_0032_downgrade_restores_0031_and_head_reupgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0032")
    _migrate(database_path, "0031", downgrade=True)
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "companion_review_tag_sources" not in tables
        assert "companion_review_field_sources" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0031",)
    finally:
        connection.close()
    _migrate(database_path, "0032")
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "companion_review_tag_sources" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0032",)
    finally:
        connection.close()
