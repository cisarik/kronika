"""Migration 0035 evidence: research runtime tables, constraints, downgrade refusal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

FINGERPRINT = "a" * 64
OPERATION_ID = "op-request-0001"
SECOND_OPERATION_ID = "op-request-0002"
OPERATION_ROW_ID = "11111111-1111-4111-8111-111111111111"
SECOND_OPERATION_ROW_ID = "22222222-2222-4222-8222-222222222222"


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


def _insert_request(
    connection: sqlite3.Connection,
    *,
    operation_id: str = OPERATION_ID,
    client_request_id: str = "client-0001",
    fingerprint: str = FINGERPRINT,
) -> None:
    connection.execute(
        """
        INSERT INTO research_requests (
            operation_id, owner_login_key, client_request_id, request_fingerprint,
            kind, prompt_text, prompt_utf8_bytes, lifecycle_state,
            provider_id, model_id, configuration_version, reasoning_effort,
            max_tool_calls, max_output_tokens, deadline_seconds,
            reservation_micro_usd, tool_allowlist_json, background,
            prompt_max_utf8_bytes, answer_max_utf8_bytes, citation_count_max,
            cleanup_state, accounting_state,
            created_at_ms, admitted_at_ms, updated_at_ms
        ) VALUES (
            ?, 'alice@example.com', ?, ?, 'search', 'question?', 9, 'admitted',
            'openai-responses', 'gpt-5.5-2026-04-23', '3', 'low',
            3, 4096, 180, 500000, '["web_search"]', 1,
            16384, 2097152, 200,
            'not_required', 'reserved', 1, 2, 3
        )
        """,
        (operation_id, client_request_id, fingerprint),
    )


def test_head_is_0035() -> None:
    from alembic.script import ScriptDirectory

    from kronika.infrastructure.persistence.migrations import _alembic_config

    with _alembic_config(
        "kronika.infrastructure.persistence.alembic_environment"
    ) as config:
        scripts = ScriptDirectory.from_config(config)
        assert scripts.get_current_head() == "0035"


def test_empty_upgrade_creates_research_tables_and_downgrade_is_reversible(
    tmp_path: Path,
) -> None:
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
        for name in (
            "research_requests",
            "research_active_slot",
            "research_operations",
            "research_budget_holds",
        ):
            assert name in tables
        assert connection.execute("SELECT COUNT(*) FROM research_requests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM research_operations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM research_budget_holds").fetchone()[0] == 0
    finally:
        connection.close()
    _migrate(database, "0034", downgrade=True)
    connection = _connect(database)
    try:
        assert _revision(connection) == "0034"
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='research_requests'"
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


def test_populated_downgrade_refuses_without_dropping_rows(tmp_path: Path) -> None:
    database = tmp_path / "kept.sqlite3"
    _migrate(database, "head")
    connection = _connect(database)
    try:
        _insert_request(connection)
        connection.execute(
            """
            INSERT INTO research_operations (
                operation_row_id, request_id, purpose, operation, attempt_number,
                started_at_ms, accounting_state
            ) VALUES (?, ?, 'search', 'create', 1, 10, 'reserved')
            """,
            (OPERATION_ROW_ID, OPERATION_ID),
        )
        connection.execute(
            """
            INSERT INTO research_budget_holds (
                operation_id, day_key, month_key, reserved_usd_micros, state, created_at_ms
            ) VALUES (?, '2026-09-29', '2026-09', 500000, 'reserved', 10)
            """,
            (OPERATION_ID,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(Exception) as caught:
        _migrate(database, "0034", downgrade=True)
    assert "refused" in str(caught.value).lower()
    connection = _connect(database)
    try:
        assert _revision(connection) == "0035"
        assert connection.execute("SELECT COUNT(*) FROM research_requests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM research_operations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM research_budget_holds").fetchone()[0] == 1
    finally:
        connection.close()


def test_request_constraints_reject_malformed_rows(tmp_path: Path) -> None:
    database = tmp_path / "checks.sqlite3"
    _migrate(database, "head")
    connection = _connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_request(connection, fingerprint="a" * 63)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO research_requests (
                    operation_id, owner_login_key, client_request_id, request_fingerprint,
                    kind, prompt_text, prompt_utf8_bytes, lifecycle_state,
                    provider_id, model_id, configuration_version, reasoning_effort,
                    max_tool_calls, max_output_tokens, deadline_seconds,
                    reservation_micro_usd, tool_allowlist_json, background,
                    prompt_max_utf8_bytes, answer_max_utf8_bytes, citation_count_max,
                    cleanup_state, accounting_state,
                    created_at_ms, admitted_at_ms, updated_at_ms
                ) VALUES (
                    'op-byte-mismatch', 'alice@example.com', 'client-0002', ?,
                    'search', 'question?', 8, 'admitted',
                    'openai-responses', 'gpt-5.5-2026-04-23', '3', 'low',
                    3, 4096, 180, 500000, '["web_search"]', 1,
                    16384, 2097152, 200,
                    'not_required', 'reserved', 1, 2, 3
                )
                """,
                (FINGERPRINT,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_request(
                connection,
                operation_id="op-bad-state",
                client_request_id="client-bad-state",
            )
            connection.execute(
                "UPDATE research_requests SET lifecycle_state='paused' "
                "WHERE operation_id='op-bad-state'"
            )
        _insert_request(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_request(connection, operation_id=SECOND_OPERATION_ID)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_request(
                connection,
                operation_id="op-uuid-shaped",
                client_request_id="client-uuid",
            )
            connection.execute(
                "UPDATE research_requests SET operation_id='dddddddd-dddd-4ddd-8ddd-dddddddddddd' "
                "WHERE operation_id='op-uuid-shaped'"
            )
    finally:
        connection.close()


def test_operation_and_budget_constraints(tmp_path: Path) -> None:
    database = tmp_path / "ops.sqlite3"
    _migrate(database, "head")
    connection = _connect(database)
    try:
        _insert_request(connection)
        connection.execute(
            """
            INSERT INTO research_operations (
                operation_row_id, request_id, purpose, operation, attempt_number,
                started_at_ms, output_tokens, reasoning_tokens_as_subset, accounting_state
            ) VALUES (?, ?, 'search', 'create', 1, 10, 5, 3, 'reserved')
            """,
            (OPERATION_ROW_ID, OPERATION_ID),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        pytest.fail(f"valid operation row rejected: {exc}")
    finally:
        connection.close()
    # Reopen to prove the failed statement above rolled back cleanly.
    connection = _connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO research_operations (
                    operation_row_id, request_id, purpose, operation, attempt_number,
                    started_at_ms, accounting_state
                ) VALUES (?, ?, 'search', 'create', 1, 10, 'paused')
                """,
                (SECOND_OPERATION_ROW_ID, OPERATION_ID),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO research_budget_holds (
                    operation_id, day_key, month_key, reserved_usd_micros,
                    accounted_usd_micros, state, created_at_ms
                ) VALUES (?, '20260929', '2026-09', 500000, 1, 'reconciled', 10)
                """,
                (OPERATION_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO research_budget_holds (
                    operation_id, day_key, month_key, reserved_usd_micros,
                    accounted_usd_micros, state, created_at_ms
                ) VALUES (?, '2026-09-29', '2026-09', 500000, 1, 'reserved', 10)
                """,
                (OPERATION_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO research_active_slot (id) VALUES (2)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO research_active_slot (id, operation_id) VALUES (1, 'op-missing')"
            )
        connection.execute("INSERT INTO research_active_slot (id) VALUES (1)")
        connection.execute(
            "UPDATE research_active_slot SET operation_id=?, held_since_ms=11 WHERE id=1",
            (OPERATION_ID,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT operation_id FROM research_active_slot WHERE id=1"
        ).fetchone()[0] == OPERATION_ID
    finally:
        connection.close()
