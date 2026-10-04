"""Unit tests for the FrameNest SQLite engine and transaction boundary."""

from __future__ import annotations

import importlib
import math
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select, text
from sqlalchemy.exc import IntegrityError, OperationalError


def test_importing_engine_module_does_not_create_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "import-only.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    importlib.import_module("kronika.infrastructure.persistence.engine")

    assert not database_path.exists()


def test_create_sqlite_engine_is_file_backed_and_does_not_connect_immediately(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.engine import create_sqlite_engine

    database_path = tmp_path / "catalog.sqlite3"
    engine = create_sqlite_engine(database_path)
    try:
        assert engine.url.drivername == "sqlite+pysqlite"
        assert str(database_path) in engine.url.database
        assert not database_path.exists()
    finally:
        engine.dispose()


def test_foreign_keys_are_enabled_on_every_new_connection(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import create_sqlite_engine

    database_path = tmp_path / "foreign-keys.sqlite3"
    engine = create_sqlite_engine(database_path)
    try:
        for _ in range(2):
            with engine.connect() as connection:
                enabled = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
                assert enabled == 1
    finally:
        engine.dispose()


def test_real_foreign_key_violation_is_rejected(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        run_in_transaction,
    )

    engine = create_sqlite_engine(tmp_path / "fk-violation.sqlite3")
    try:
        metadata = MetaData()
        parent = Table("parent", metadata, Column("id", Integer, primary_key=True))
        child = Table(
            "child",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("parent_id", Integer, sa.ForeignKey("parent.id"), nullable=False),
        )

        def operation(connection: sa.Connection) -> None:
            metadata.create_all(connection)
            connection.execute(insert(child).values(id=1, parent_id=99))

        with pytest.raises(IntegrityError):
            run_in_transaction(engine, operation)
    finally:
        engine.dispose()


def test_parameterized_core_insert_and_select_round_trip(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        run_in_transaction,
    )

    engine = create_sqlite_engine(tmp_path / "parameter-binding.sqlite3")
    injected_value = "'; DROP TABLE probe; --"
    metadata = MetaData()
    probe = Table(
        "probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", String, nullable=False),
    )
    try:
        def write(connection: sa.Connection) -> None:
            metadata.create_all(connection)
            connection.execute(insert(probe).values(id=1, value=injected_value))

        def read(connection: sa.Connection) -> str:
            return connection.execute(
                select(probe.c.value).where(probe.c.id == sa.bindparam("probe_id")),
                {"probe_id": 1},
            ).scalar_one()

        run_in_transaction(engine, write)
        assert run_in_transaction(engine, read) == injected_value
    finally:
        engine.dispose()


def test_successful_transaction_commits_and_persists_after_reopen(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        run_in_transaction,
    )

    database_path = tmp_path / "commit.sqlite3"
    metadata = MetaData()
    probe = Table("probe", metadata, Column("id", Integer, primary_key=True))

    engine = create_sqlite_engine(database_path)
    try:
        def write(connection: sa.Connection) -> None:
            metadata.create_all(connection)
            connection.execute(insert(probe).values(id=1))

        run_in_transaction(engine, write)
    finally:
        engine.dispose()

    reopened = create_sqlite_engine(database_path)
    try:
        with reopened.connect() as connection:
            assert connection.execute(select(probe.c.id)).scalar_one() == 1
    finally:
        reopened.dispose()


def test_failed_transaction_rolls_back_and_closes_connection(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        run_in_transaction,
    )

    engine = create_sqlite_engine(tmp_path / "rollback.sqlite3")
    metadata = MetaData()
    probe = Table("probe", metadata, Column("id", Integer, primary_key=True))
    observed_connection: sa.Connection | None = None

    try:
        with engine.begin() as connection:
            metadata.create_all(connection)

        def failing_write(connection: sa.Connection) -> None:
            nonlocal observed_connection
            observed_connection = connection
            connection.execute(insert(probe).values(id=1))
            raise RuntimeError("operation failed")

        with pytest.raises(RuntimeError, match="operation failed"):
            run_in_transaction(engine, failing_write)

        assert observed_connection is not None
        assert observed_connection.closed
        with engine.connect() as connection:
            rows = connection.execute(select(probe.c.id)).all()
        assert rows == []
    finally:
        engine.dispose()


def test_engine_disposal_is_explicit(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine

    engine = create_sqlite_engine(tmp_path / "dispose.sqlite3")
    with patch.object(engine, "dispose", wraps=engine.dispose) as dispose:
        dispose_engine(engine)
    dispose.assert_called_once_with()


@pytest.mark.parametrize("timeout", [0.0, -0.1, math.inf, math.nan, 120.0])
def test_invalid_busy_timeout_is_rejected_with_sanitized_error(
    tmp_path: Path,
    timeout: float,
) -> None:
    from kronika.infrastructure.persistence.engine import create_sqlite_engine
    from kronika.infrastructure.persistence.errors import FrameNestPersistenceError

    with pytest.raises(FrameNestPersistenceError) as exc_info:
        create_sqlite_engine(tmp_path / "invalid-timeout.sqlite3", busy_timeout_seconds=timeout)

    error_text = str(exc_info.value)
    assert str(timeout) not in error_text
    assert "timeout" in error_text.lower()


def test_lock_conflict_uses_bounded_busy_timeout(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        run_in_transaction,
    )

    database_path = tmp_path / "busy.sqlite3"
    first_engine = create_sqlite_engine(database_path, busy_timeout_seconds=0.05)
    second_engine = create_sqlite_engine(database_path, busy_timeout_seconds=0.05)
    metadata = MetaData()
    probe = Table("probe", metadata, Column("id", Integer, primary_key=True))

    try:
        with first_engine.begin() as connection:
            metadata.create_all(connection)

        with first_engine.connect() as first_connection:
            first_transaction = first_connection.begin()
            first_connection.execute(insert(probe).values(id=1))

            start = time.monotonic()
            with pytest.raises(OperationalError):
                run_in_transaction(
                    second_engine,
                    lambda connection: connection.execute(insert(probe).values(id=2)),
                )
            elapsed = time.monotonic() - start

            first_transaction.rollback()

        assert elapsed < 1.0
    finally:
        first_engine.dispose()
        second_engine.dispose()
