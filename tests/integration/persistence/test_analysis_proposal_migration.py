"""Migration evidence for durable analysis proposals (0033)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

CLAIM_MEDIA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LOCATION_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


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


def _seed_populated_0032(database_path: Path) -> None:
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


def test_empty_database_upgrades_to_0033(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.sqlite3"
    _migrate(database_path, "0033")
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "media_analysis_proposals" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0033",)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_analysis_proposals"
        ).fetchone() == (0,)
        indexes = {
            row[1]
            for row in connection.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "ix_media_analysis_proposals_created_at" in indexes
        assert "ix_media_analysis_proposals_status_created" in indexes
        sql = _table_sql(connection, "media_analysis_proposals")
        assert "proposed_by_login_key" in sql
        assert "open" in sql
    finally:
        connection.close()


def test_populated_0032_upgrade_preserves_catalog_and_is_rerunnable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0032")
    _seed_populated_0032(database_path)
    _migrate(database_path, "0033")
    _migrate(database_path, "0033")
    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT publication_origin FROM media_content_publications"
        ).fetchone() == ("legacy_backfill",)
        assert connection.execute(
            "SELECT id FROM logical_media"
        ).fetchone() == (CLAIM_MEDIA,)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_analysis_proposals"
        ).fetchone() == (0,)
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0033",)
        connection.execute(
            """
            INSERT INTO media_analysis_proposals
            (id, media_id, proposed_by_login_key, created_at_ms, status)
            VALUES (?, ?, 'alice@example.com', 99, 'open')
            """,
            ("ffffffff-ffff-4fff-8fff-ffffffffffff", CLAIM_MEDIA),
        )
        connection.commit()
        assert connection.execute(
            "SELECT proposed_by_login_key FROM media_analysis_proposals"
        ).fetchone() == ("alice@example.com",)
    finally:
        connection.close()


def test_empty_0033_downgrade_restores_0032_and_head_reupgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0033")
    _migrate(database_path, "0032", downgrade=True)
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "media_analysis_proposals" not in tables
        assert "companion_review_tag_sources" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0032",)
    finally:
        connection.close()
    _migrate(database_path, "0033")
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "media_analysis_proposals" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0033",)
    finally:
        connection.close()
