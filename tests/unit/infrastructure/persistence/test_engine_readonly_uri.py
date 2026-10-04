"""Read-only SQLite engine URI composition evidence."""

from __future__ import annotations

import sqlite3

import pytest

from kronika.infrastructure.persistence.engine import (
    create_sqlite_readonly_engine,
    dispose_engine,
)


def _seed_minimal_catalog(database_path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        connection.execute("INSERT INTO alembic_version VALUES ('0033')")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "filename",
    [
        "catalog.sqlite3",
        "weird ?db#name (v1).sqlite3",
        "mode=rw&it-is-a-trick#.sqlite3",
        "percent%2Eencoded.sqlite3",
    ],
)
def test_readonly_engine_survives_reserved_characters_in_path(
    tmp_path, filename: str
) -> None:
    database_path = tmp_path / filename
    _seed_minimal_catalog(database_path)
    engine = create_sqlite_readonly_engine(database_path)
    try:
        with engine.connect() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
    finally:
        dispose_engine(engine)
    assert revision == "0033"


def test_readonly_engine_still_rejects_writes_on_encoded_path(tmp_path) -> None:
    database_path = tmp_path / "trick?name#.sqlite3"
    _seed_minimal_catalog(database_path)
    engine = create_sqlite_readonly_engine(database_path)
    try:
        with pytest.raises(Exception):
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version(version_num) VALUES ('nope')"
                )
                connection.commit()
    finally:
        dispose_engine(engine)
