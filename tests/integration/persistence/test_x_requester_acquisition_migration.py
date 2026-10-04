"""Migration evidence for requester-private X acquisition (0028)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa

from kronika.infrastructure.persistence.catalog_schema import x_post_claims
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
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
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _seed_media_metadata_at_0027(database_path: Path) -> None:
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
                acquisition_source, creator_attribution_kind, creator_stable_id,
                collection_key, processed_at_ms, created_at_ms, updated_at_ms
            ) VALUES (
                'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'Existing Title', NULL,
                'youtube', 'youtube_manual_claim', 'youtube_channel', 'UC123',
                NULL, NULL, 10, 20
            )
            """
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


def test_upgrade_0027_to_0028_creates_x_tables_and_preserves_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0027")
    _seed_media_metadata_at_0027(database_path)
    _migrate(database_path, "0028")

    connection = _connect(database_path)
    try:
        tables = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "x_post_claims" in tables
        assert "x_assets" in tables
        row = connection.execute(
            "SELECT display_title, content_category, acquisition_source, "
            "creator_stable_id FROM media_metadata"
        ).fetchone()
        assert row == ("Existing Title", "youtube", "youtube_manual_claim", "UC123")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        connection.close()


def test_0028_accepts_x_manual_claim_source(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0027")
    _seed_media_metadata_at_0027(database_path)
    _migrate(database_path, "0028")
    connection = _connect(database_path)
    try:
        connection.execute(
            """
            UPDATE media_metadata
            SET acquisition_source = 'x_manual_claim',
                content_category = 'meme',
                creator_attribution_kind = 'x_author',
                creator_stable_id = 'user_123',
                creator_handle = 'author',
                creator_display_name = 'Author Name'
            WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
            """
        )
        connection.commit()
        row = connection.execute(
            "SELECT acquisition_source FROM media_metadata"
        ).fetchone()
        assert row == ("x_manual_claim",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_metadata SET acquisition_source='tiktok' "
                "WHERE media_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'"
            )
            connection.commit()
    finally:
        connection.close()


def test_0028_x_post_claim_constraints(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0028")
    connection = _connect(database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO x_post_claims (
                    id, state, acquisition_source, submitted_url, canonical_url,
                    x_post_id, extractor_key, created_by_login_key,
                    discovered_asset_count, success_count, failure_count,
                    created_at_ms, updated_at_ms, cleanup_state, version
                ) VALUES (
                    'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'submitted',
                    'youtube_manual_claim', 'https://x.com/a/status/1',
                    'https://x.com/a/status/1', '1', 'Youtube', 'alice',
                    1, 0, 0, 10, 10, 'pending', 0
                )
                """
            )
            connection.commit()
    finally:
        connection.close()


def test_x_post_claims_runtime_metadata_converges_with_migrated_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "x-claim-metadata-convergence.sqlite3"
    _migrate(database_path, "head")

    engine = create_sqlite_engine(database_path)
    try:
        reflected_metadata = sa.MetaData()
        reflected_metadata.reflect(bind=engine, only=["x_post_claims"])
        reflected_table = reflected_metadata.tables["x_post_claims"]
    finally:
        dispose_engine(engine)

    def _named_non_fk_constraint_names(table: sa.Table) -> set[str]:
        return {
            constraint.name
            for constraint in table.constraints
            if constraint.name is not None
            and not isinstance(constraint, sa.ForeignKeyConstraint)
        }

    runtime_names = _named_non_fk_constraint_names(x_post_claims)
    migrated_names = _named_non_fk_constraint_names(reflected_table)

    assert "uq_x_post_claims_id" not in runtime_names
    assert "uq_x_post_claims_id" not in migrated_names
    assert runtime_names == migrated_names
    assert {column.name for column in x_post_claims.primary_key} == {"id"}
    assert {column.name for column in reflected_table.primary_key} == {"id"}


def test_0028_downgrade_preserves_and_removes_x_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0027")
    _seed_media_metadata_at_0027(database_path)
    _migrate(database_path, "0028")
    _migrate(database_path, "0027", downgrade=True)
    connection = _connect(database_path)
    try:
        tables = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "x_post_claims" not in tables
        assert "x_assets" not in tables
        row = connection.execute(
            "SELECT display_title, content_category, acquisition_source "
            "FROM media_metadata"
        ).fetchone()
        assert row == ("Existing Title", "youtube", "youtube_manual_claim")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_0028_downgrade_refuses_when_x_media_exists(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0027")
    _seed_media_metadata_at_0027(database_path)
    _migrate(database_path, "0028")
    connection = _connect(database_path)
    try:
        connection.execute(
            "UPDATE media_metadata SET acquisition_source='x_manual_claim', "
            "content_category='meme' WHERE media_id='cccccccc-cccc-4ccc-8ccc-cccccccccccc'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception, match="Refusing 0028 downgrade"):
        _migrate(database_path, "0027", downgrade=True)
