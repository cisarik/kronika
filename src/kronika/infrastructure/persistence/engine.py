"""Synchronous SQLAlchemy Core engine and transaction helpers."""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, URL, create_engine

from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.private_state import (
    prepare_readonly_catalog,
    prepare_writable_catalog,
    verify_private_catalog,
)

DEFAULT_BUSY_TIMEOUT_SECONDS = 5.0
MAX_BUSY_TIMEOUT_SECONDS = 60.0

T = TypeVar("T")


def create_sqlite_engine(
    database_path: Path | str,
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> Engine:
    """Create a synchronous file-backed SQLite engine without opening it."""
    normalized_timeout = _validate_busy_timeout(busy_timeout_seconds)
    normalized_path = _validate_database_path(database_path)
    url = URL.create("sqlite+pysqlite", database=str(normalized_path))

    def _connect() -> sqlite3.Connection:
        parent = normalized_path.parent
        if parent.is_dir() and not parent.is_symlink():
            prepare_writable_catalog(normalized_path)
        previous = os.umask(0o077)
        try:
            return sqlite3.connect(
                normalized_path,
                timeout=normalized_timeout,
                check_same_thread=False,
            )
        finally:
            os.umask(previous)

    engine = create_engine(
        url,
        creator=_connect,
        echo=False,
        hide_parameters=True,
    )
    busy_timeout_milliseconds = max(1, int(normalized_timeout * 1000))

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_milliseconds}")
        finally:
            cursor.close()
        verify_private_catalog(normalized_path)

    def _private_transaction_begin(connection: Connection) -> None:
        if "kronika_umask" not in connection.info:
            connection.info["kronika_umask"] = os.umask(0o077)

    def _private_transaction_end(connection: Connection) -> None:
        previous = connection.info.pop("kronika_umask", None)
        if previous is not None:
            os.umask(previous)
        verify_private_catalog(normalized_path)

    event.listen(engine, "begin", _private_transaction_begin)
    event.listen(engine, "commit", _private_transaction_end)
    event.listen(engine, "rollback", _private_transaction_end)

    return engine


def create_sqlite_readonly_engine(
    database_path: Path | str,
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> Engine:
    """Create a file-backed SQLite engine that cannot write or create the file."""
    normalized_timeout = _validate_busy_timeout(busy_timeout_seconds)
    normalized_path = _validate_database_path(database_path)
    if not normalized_path.is_file():
        raise FrameNestPersistenceError(
            "Database path must exist for read-only access.",
            error_code="DATABASE_NOT_FOUND",
            retryable=False,
        )
    encoded_path = quote(normalized_path.as_posix(), safe="/")
    uri = f"file:{encoded_path}?mode=ro"

    def _connect_readonly() -> sqlite3.Connection:
        prepare_readonly_catalog(normalized_path)
        return sqlite3.connect(
            f"file:{encoded_path}?mode=ro",
            uri=True,
            timeout=normalized_timeout,
            check_same_thread=False,
        )

    engine = create_engine(
        URL.create("sqlite+pysqlite", database=uri, query={"uri": "true"}),
        creator=_connect_readonly,
        echo=False,
        hide_parameters=True,
    )
    busy_timeout_milliseconds = max(1, int(normalized_timeout * 1000))

    @event.listens_for(engine, "connect")
    def _configure_readonly_sqlite_connection(
        dbapi_connection: object, _: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_milliseconds}")
            cursor.execute("PRAGMA query_only=ON")
        finally:
            cursor.close()

    try:
        _require_readonly_engine(engine)
    except FrameNestPersistenceError:
        dispose_engine(engine)
        raise
    except Exception as exc:
        dispose_engine(engine)
        raise FrameNestPersistenceError(
            "Read-only database configuration is not available.",
            error_code="DATABASE_NOT_READONLY",
            retryable=False,
            cause=exc,
        ) from exc
    return engine


def _require_readonly_engine(engine: Engine) -> None:
    with engine.connect() as connection:
        query_only = connection.exec_driver_sql("PRAGMA query_only").scalar()
        if int(query_only or 0) != 1:
            raise FrameNestPersistenceError(
                "Read-only database configuration is writable.",
                error_code="DATABASE_NOT_READONLY",
                retryable=False,
            )
        try:
            connection.exec_driver_sql(
                "INSERT INTO alembic_version(version_num) VALUES ('writable-probe')"
            )
        except Exception:
            connection.rollback()
            return
        connection.rollback()
        raise FrameNestPersistenceError(
            "Read-only database configuration is writable.",
            error_code="DATABASE_NOT_READONLY",
            retryable=False,
        )


def run_in_transaction(
    engine: Engine,
    operation: Callable[[Connection], T],
) -> T:
    """Run an operation in an explicit transaction and close the connection."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            result = operation(connection)
        except BaseException:
            transaction.rollback()
            raise
        transaction.commit()
        return result


def run_in_immediate_transaction(
    engine: Engine,
    operation: Callable[[Connection], T],
) -> T:
    """Run one SQLite write decision after acquiring the writer lock first."""
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            result = operation(connection)
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
        return result


def dispose_engine(engine: Engine) -> None:
    """Dispose an engine at an explicit cleanup boundary."""
    engine.dispose()


def _validate_database_path(database_path: Path | str) -> Path:
    try:
        path = Path(database_path).expanduser()
    except (RuntimeError, TypeError, ValueError) as exc:
        raise FrameNestPersistenceError(
            "Database path must be absolute.",
            error_code="INVALID_DATABASE_PATH",
            retryable=False,
            cause=exc,
        ) from exc
    if not path.is_absolute():
        raise FrameNestPersistenceError(
            "Database path must be absolute.",
            error_code="INVALID_DATABASE_PATH",
            retryable=False,
        )
    return path.resolve(strict=False)


def _validate_busy_timeout(value: float) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
        or value > MAX_BUSY_TIMEOUT_SECONDS
    ):
        raise FrameNestPersistenceError(
            "Database busy timeout must be a positive bounded value.",
            error_code="INVALID_BUSY_TIMEOUT",
            retryable=False,
        )
    return float(value)
