"""Migration 0034 evidence: preservation, constraints, and downgrade refusal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

MEDIA_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


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


def _revision(connection: sqlite3.Connection) -> str:
    return str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def test_head_is_0034() -> None:
    from alembic.script import ScriptDirectory

    from kronika.infrastructure.persistence.migrations import _alembic_config

    with _alembic_config(
        "kronika.infrastructure.persistence.alembic_environment"
    ) as config:
        scripts = ScriptDirectory.from_config(config)
        assert scripts.get_current_head() == "0035"


def test_empty_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "empty.sqlite3"
    _migrate(database, "head")
    connection = _connect(database)
    try:
        assert _revision(connection) == "0035"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "kronika_documents" in tables
        assert "kronika_records" in tables
        assert "kronika_approved_media" in tables
    finally:
        connection.close()
    _migrate(database, "0033", downgrade=True)
    connection = _connect(database)
    try:
        assert _revision(connection) == "0033"
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='kronika_records'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()
    _migrate(database, "head")
    connection = _connect(database)
    try:
        assert _revision(connection) == "0035"
    finally:
        connection.close()


def test_populated_0033_upgrade_preserves_rows_and_creates_no_records(
    tmp_path: Path,
) -> None:
    database = tmp_path / "populated.sqlite3"
    _migrate(database, "0033")
    connection = _connect(database)
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
            (MEDIA_ID,),
        )
        connection.commit()
        before = connection.execute("SELECT id, media_kind FROM logical_media").fetchall()
    finally:
        connection.close()
    _migrate(database, "head")
    connection = _connect(database)
    try:
        after = connection.execute("SELECT id, media_kind FROM logical_media").fetchall()
        assert after == before
        assert connection.execute("SELECT COUNT(*) FROM kronika_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM kronika_documents").fetchone()[0] == 0
    finally:
        connection.close()


def test_populated_downgrade_refuses_without_dropping_rows(tmp_path: Path) -> None:
    database = tmp_path / "kept.sqlite3"
    _migrate(database, "head")
    connection = _connect(database)
    try:
        connection.execute(
            """
            INSERT INTO kronika_documents (
                id, operation_id, kind, question_text, answer_text,
                citations_json, completion_evidence_json, created_at_ms, completed_at_ms
            ) VALUES (
                'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'op-token-1', 'search',
                'Question', 'Answer', '[]',
                '{"answer_complete":true,"incomplete_marker":false,"provider_terminal":true,"refusal_marker":false,"web_search_executed":true}',
                1, 2
            )
            """
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception) as caught:
        _migrate(database, "0033", downgrade=True)
    assert "refused" in str(caught.value).lower()
    connection = _connect(database)
    try:
        assert _revision(connection) == "0035"
        assert connection.execute("SELECT COUNT(*) FROM kronika_documents").fetchone()[0] == 1
    finally:
        connection.close()


def test_malformed_record_shape_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "checks.sqlite3"
    _migrate(database, "head")
    connection = _connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO kronika_records (
                    id, kind, owner_login_key, visibility, media_id,
                    document_id, final_operation_id, created_at_ms, version
                ) VALUES (
                    'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'media', 'alice',
                    'private', NULL, NULL, NULL, 1, 1
                )
                """
            )
    finally:
        connection.close()
