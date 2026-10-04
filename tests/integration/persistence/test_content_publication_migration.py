"""Migration evidence for durable content-publication state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings

MEDIA_A = "11111111-1111-4111-8111-111111111111"
MEDIA_B = "22222222-2222-4222-8222-222222222222"


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


def _insert_media(
    connection: sqlite3.Connection,
    media_id: str,
    *,
    created_at_ms: int,
) -> None:
    connection.execute(
        "INSERT INTO logical_media "
        "(id, media_kind, created_at_ms, updated_at_ms) VALUES (?, 'video', ?, ?)",
        (media_id, created_at_ms, created_at_ms),
    )


def test_empty_and_populated_0020_databases_upgrade_with_exact_backfill(
    tmp_path: Path,
) -> None:
    empty_path = tmp_path / "empty.sqlite3"
    populated_path = tmp_path / "populated.sqlite3"
    _migrate(empty_path, "0021")
    _migrate(populated_path, "0020")
    connection = _connect(populated_path)
    try:
        _insert_media(connection, MEDIA_A, created_at_ms=123)
        _insert_media(connection, MEDIA_B, created_at_ms=456)
        connection.commit()
    finally:
        connection.close()

    _migrate(populated_path, "0021")

    empty = _connect(empty_path)
    populated = _connect(populated_path)
    try:
        assert empty.execute(
            "SELECT COUNT(*) FROM media_content_publications"
        ).fetchone() == (0,)
        assert populated.execute(
            "SELECT media_id, published_at_ms, publication_origin "
            "FROM media_content_publications ORDER BY media_id"
        ).fetchall() == [
            (MEDIA_A, 123, "legacy_backfill"),
            (MEDIA_B, 456, "legacy_backfill"),
        ]
        assert populated.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert populated.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        empty.close()
        populated.close()


def test_new_media_is_unpublished_by_absence_and_schema_constraints_hold(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraints.sqlite3"
    _migrate(database_path, "0021")
    connection = _connect(database_path)
    try:
        _insert_media(connection, MEDIA_A, created_at_ms=10)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_content_publications"
        ).fetchone() == (0,)
        connection.execute(
            "INSERT INTO media_content_publications VALUES (?, ?, ?)",
            (MEDIA_A, 10, "admin_explicit"),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO media_content_publications VALUES (?, ?, ?)",
                (MEDIA_A, 11, "legacy_backfill"),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_content_publications "
                "SET publication_origin = 'automatic' WHERE media_id = ?",
                (MEDIA_A,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_content_publications "
                "SET published_at_ms = -1 WHERE media_id = ?",
                (MEDIA_A,),
            )
        connection.rollback()

        indexes = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA index_list(media_content_publications)"
            ).fetchall()
        }
        assert "ix_media_content_publications_published_at" in indexes
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(media_content_publications)"
        ).fetchall()
        assert {
            (row[2], row[3], row[4], row[6]) for row in foreign_keys
        } == {("logical_media", "media_id", "id", "CASCADE")}
    finally:
        connection.close()


def test_publication_row_cascades_when_logical_medium_is_deleted(tmp_path: Path) -> None:
    database_path = tmp_path / "cascade.sqlite3"
    _migrate(database_path, "0021")
    connection = _connect(database_path)
    try:
        _insert_media(connection, MEDIA_A, created_at_ms=10)
        connection.execute(
            "INSERT INTO media_content_publications VALUES (?, ?, ?)",
            (MEDIA_A, 10, "admin_explicit"),
        )
        connection.execute("DELETE FROM logical_media WHERE id = ?", (MEDIA_A,))
        assert connection.execute(
            "SELECT COUNT(*) FROM media_content_publications"
        ).fetchone() == (0,)
        connection.commit()
    finally:
        connection.close()


def test_downgrade_is_allowed_only_when_every_medium_is_published(
    tmp_path: Path,
) -> None:
    safe_path = tmp_path / "safe.sqlite3"
    unsafe_path = tmp_path / "unsafe.sqlite3"
    _migrate(safe_path, "0021")
    safe = _connect(safe_path)
    try:
        _insert_media(safe, MEDIA_A, created_at_ms=10)
        safe.execute(
            "INSERT INTO media_content_publications VALUES (?, ?, ?)",
            (MEDIA_A, 10, "admin_explicit"),
        )
        safe.commit()
    finally:
        safe.close()
    _migrate(safe_path, "0020", downgrade=True)
    safe = _connect(safe_path)
    try:
        assert safe.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'media_content_publications'"
        ).fetchone() is None
        assert safe.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0020",
        )
    finally:
        safe.close()

    _migrate(unsafe_path, "0021")
    unsafe = _connect(unsafe_path)
    try:
        _insert_media(unsafe, MEDIA_A, created_at_ms=10)
        unsafe.commit()
    finally:
        unsafe.close()
    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade content publication while unpublished",
    ):
        _migrate(unsafe_path, "0020", downgrade=True)
    unsafe = _connect(unsafe_path)
    try:
        assert unsafe.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0021",
        )
        assert unsafe.execute(
            "SELECT COUNT(*) FROM media_content_publications"
        ).fetchone() == (0,)
    finally:
        unsafe.close()


def test_packaged_head_is_0022(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = KronikaSettings(
        database_path=tmp_path / "head.sqlite3",
        _env_file=None,
    )
    status = upgrade_database_to_head(settings)

    assert status.current_revision == status.head_revision == "0035"
    assert inspect_database_migration_status(settings) == status
