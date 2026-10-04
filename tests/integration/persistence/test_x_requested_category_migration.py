"""Migration evidence for X claim requested content category (0030)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from kronika.domain.media_classification import ContentCategory
from kronika.domain.x_acquisition import XPostClaim
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.x_acquisition_claim_repository import (
    SqliteXAcquisitionClaimRepository,
)

CLAIM_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
ASSET_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
STAGE_KEY = "0123456789abcdef0123456789abcdef"


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


def _table_sql(connection: sqlite3.Connection, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    assert row is not None
    return row[0]


def _index_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        item[1]
        for item in connection.execute(f"PRAGMA index_list({table})").fetchall()
    }


def _seed_populated_0029(database_path: Path) -> None:
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
            INSERT INTO x_post_claims (
                id, state, acquisition_source, submitted_url, canonical_url,
                x_post_id, extractor_key, created_by_login_key,
                discovered_asset_count, success_count, failure_count,
                created_at_ms, updated_at_ms, cleanup_state, version
            ) VALUES (
                ?, 'submitted', 'x_manual_claim',
                'https://x.com/a/status/1', 'https://x.com/a/status/1',
                '1', 'X', 'alice@example.com',
                1, 0, 0, 10, 10, 'pending', 0
            )
            """,
            (CLAIM_ID,),
        )
        connection.execute(
            """
            INSERT INTO x_assets (
                id, claim_id, ordinal, media_type, expected_mime, state, stage_key,
                created_at_ms, updated_at_ms, cleanup_state, version
            ) VALUES (
                ?, ?, 0, 'video', 'video/mp4', 'pending', ?,
                10, 10, 'pending', 0
            )
            """,
            (ASSET_ID, CLAIM_ID, STAGE_KEY),
        )
        connection.execute(
            """
            INSERT INTO x_claim_pending_aliases (
                claim_id, login_key, display_title, description,
                created_at_ms, updated_at_ms
            ) VALUES (?, 'alice@example.com', 'Mine', NULL, 10, 10)
            """,
            (CLAIM_ID,),
        )
        connection.commit()
    finally:
        connection.close()


def test_head_is_0030() -> None:
    from kronika.infrastructure.persistence.migrations import _alembic_config

    with _alembic_config(
        "kronika.infrastructure.persistence.alembic_environment"
    ) as config:
        from alembic.script import ScriptDirectory

        scripts = ScriptDirectory.from_config(config)
        assert scripts.get_current_head() == "0035"


def test_populated_0029_upgrade_preserves_rows_indexes_and_null_legacy(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0029")
    _seed_populated_0029(database_path)
    before = _connect(database_path)
    try:
        before_indexes = _index_names(before, "x_post_claims")
        before_fk = before.execute("PRAGMA foreign_key_list(x_post_claims)").fetchall()
        before_asset_count = before.execute("SELECT COUNT(*) FROM x_assets").fetchone()
        before_alias_count = before.execute(
            "SELECT COUNT(*) FROM x_claim_pending_aliases"
        ).fetchone()
    finally:
        before.close()

    _migrate(database_path, "0030")
    connection = _connect(database_path)
    try:
        columns = {
            item[1] for item in connection.execute("PRAGMA table_info(x_post_claims)")
        }
        assert "requested_content_category" in columns
        row = connection.execute(
            "SELECT requested_content_category, x_post_id, created_by_login_key "
            "FROM x_post_claims WHERE id = ?",
            (CLAIM_ID,),
        ).fetchone()
        assert row == (None, "1", "alice@example.com")
        assert connection.execute("SELECT COUNT(*) FROM x_assets").fetchone() == before_asset_count
        assert (
            connection.execute("SELECT COUNT(*) FROM x_claim_pending_aliases").fetchone()
            == before_alias_count
        )
        assert _index_names(connection, "x_post_claims") >= before_indexes
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        after_fk = connection.execute("PRAGMA foreign_key_list(x_post_claims)").fetchall()
        assert {(item[2], item[3], item[4]) for item in after_fk} == {
            (item[2], item[3], item[4]) for item in before_fk
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE x_post_claims SET requested_content_category = 'tiktok' "
                "WHERE id = ?",
                (CLAIM_ID,),
            )
            connection.commit()
        connection.rollback()
        connection.execute(
            "UPDATE x_post_claims SET requested_content_category = 'movie' "
            "WHERE id = ?",
            (CLAIM_ID,),
        )
        connection.commit()
        stored = connection.execute(
            "SELECT requested_content_category FROM x_post_claims WHERE id = ?",
            (CLAIM_ID,),
        ).fetchone()
        assert stored == ("movie",)
    finally:
        connection.close()


def test_repository_hydrates_requested_category(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0030")
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    try:
        claim = XPostClaim.new(
            submitted_url="https://x.com/a/status/42",
            now_ms=10,
            created_by_login_key="alice@example.com",
            requested_content_category=ContentCategory.GENERAL,
        )
        repository = SqliteXAcquisitionClaimRepository(engine)
        repository.create_post(claim)
        loaded = repository.get_post(claim.id)
        assert loaded is not None
        assert loaded.requested_content_category is ContentCategory.GENERAL
        omitted = XPostClaim.new(
            submitted_url="https://x.com/a/status/43",
            now_ms=11,
            created_by_login_key="bob@example.com",
        )
        repository.create_post(omitted)
        loaded_omitted = repository.get_post(omitted.id)
        assert loaded_omitted is not None
        assert loaded_omitted.requested_content_category is None
    finally:
        connection.close()
        engine.dispose()


def test_downgrade_refuses_non_null_category(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0029")
    _seed_populated_0029(database_path)
    _migrate(database_path, "0030")
    connection = _connect(database_path)
    try:
        connection.execute(
            "UPDATE x_post_claims SET requested_content_category = 'meme' WHERE id = ?",
            (CLAIM_ID,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception):
        _migrate(database_path, "0029", downgrade=True)


def test_all_null_downgrade_restores_0029_contract(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0029")
    _seed_populated_0029(database_path)
    before = _connect(database_path)
    try:
        before_sql = _table_sql(before, "x_post_claims")
        before_indexes = _index_names(before, "x_post_claims")
        before_columns = [
            item[1] for item in before.execute("PRAGMA table_info(x_post_claims)")
        ]
    finally:
        before.close()

    _migrate(database_path, "0030")
    _migrate(database_path, "0029", downgrade=True)
    connection = _connect(database_path)
    try:
        after_columns = [
            item[1] for item in connection.execute("PRAGMA table_info(x_post_claims)")
        ]
        assert "requested_content_category" not in after_columns
        assert after_columns == before_columns
        assert _index_names(connection, "x_post_claims") == before_indexes
        row = connection.execute(
            "SELECT id, x_post_id FROM x_post_claims"
        ).fetchone()
        assert row == (CLAIM_ID, "1")
        assert connection.execute("SELECT COUNT(*) FROM x_assets").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM x_claim_pending_aliases"
        ).fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        restored_sql = _table_sql(connection, "x_post_claims")
        assert "requested_content_category" not in restored_sql
        assert "requested_content_category" not in before_sql
    finally:
        connection.close()
