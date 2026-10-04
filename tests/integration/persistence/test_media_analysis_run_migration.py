"""Migration evidence for durable automatic media analysis runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings


def _settings(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _migrate(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.migrations import _alembic_config
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine

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


def _seed_cataloged_media(connection: sqlite3.Connection) -> tuple[str, str]:
    device_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    library_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    media_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    location_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    connection.execute(
        "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic device')",
        (device_id,),
    )
    connection.execute(
        """
        INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path)
        VALUES (?, ?, 'Synthetic library', 'posix', '/synthetic/library')
        """,
        (library_id, device_id),
    )
    connection.execute(
        """
        INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
        VALUES (?, 'video', 40, 40)
        """,
        (media_id,),
    )
    connection.execute(
        """
        INSERT INTO physical_media_locations (
            id, media_id, library_id, relative_path, availability,
            observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, 'synthetic.mp4', 'available', 8, NULL, 40, 40)
        """,
        (location_id, media_id, library_id),
    )
    return media_id, location_id


def test_empty_database_upgrades_to_0015(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0015")
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "media_analysis_runs" in tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0015",)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_analysis_runs"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_upgrade_from_0014_preserves_catalog_without_analysis_backfill(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "populated.sqlite3"
    _migrate(database_path, "0014")
    connection = _connect(database_path)
    try:
        media_id, location_id = _seed_cataloged_media(connection)
        connection.commit()
    finally:
        connection.close()

    _migrate(database_path, "0015")
    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT id FROM logical_media"
        ).fetchone() == (media_id,)
        assert connection.execute(
            "SELECT id FROM physical_media_locations"
        ).fetchone() == (location_id,)
        assert connection.execute(
            "SELECT COUNT(*) FROM media_analysis_runs"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_0015_state_constraints_and_duplicate_definition_rejection(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraints.sqlite3"
    _migrate(database_path, "0015")
    connection = _connect(database_path)
    try:
        media_id, location_id = _seed_cataloged_media(connection)
        connection.commit()
        run_id = "11111111-1111-4111-8111-111111111111"
        connection.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, provider_id, model_id, prompt_version,
                result_schema_version, result_json, error_code, error_message,
                created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (?, ?, ?, 'automatic_post_catalog', 'pending',
                      0, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                      50, NULL, NULL, 1)
            """,
            (run_id, media_id, location_id),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, provider_id, model_id, prompt_version,
                    result_schema_version, result_json, error_code, error_message,
                    created_at_ms, started_at_ms, completed_at_ms, version
                ) VALUES (?, ?, ?, 'automatic_post_catalog', 'pending',
                          0, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                          51, NULL, NULL, 1)
                """,
                (
                    "22222222-2222-4222-8222-222222222222",
                    media_id,
                    location_id,
                ),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE media_analysis_runs
                SET state = 'analyzed', result_json = '{"title":"x"}'
                WHERE id = ?
                """,
                (run_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, provider_id, model_id, prompt_version,
                    result_schema_version, result_json, error_code, error_message,
                    created_at_ms, started_at_ms, completed_at_ms, version
                ) VALUES (?, ?, ?, 'other_definition', 'pending',
                          0, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                          52, NULL, NULL, 1)
                """,
                (
                    "33333333-3333-4333-8333-333333333333",
                    "99999999-9999-4999-8999-999999999999",
                    location_id,
                ),
            )
    finally:
        connection.close()


def test_0015_downgrade_removes_analysis_table_and_preserves_catalog(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "downgrade.sqlite3"
    _migrate(database_path, "0015")
    connection = _connect(database_path)
    try:
        media_id, _location_id = _seed_cataloged_media(connection)
        connection.commit()
    finally:
        connection.close()
    _migrate(database_path, "0014", downgrade=True)
    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "media_analysis_runs" not in tables
        assert connection.execute(
            "SELECT id FROM logical_media"
        ).fetchone() == (media_id,)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0014",)
    finally:
        connection.close()


def test_fresh_and_upgraded_0015_schemas_are_semantically_equivalent(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite3"
    upgraded = tmp_path / "upgraded.sqlite3"
    _migrate(fresh, "0015")
    _migrate(upgraded, "0014")
    _migrate(upgraded, "0015")

    def schema_signature(database_path: Path) -> tuple[str, ...]:
        connection = _connect(database_path)
        try:
            return tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE tbl_name = 'media_analysis_runs'
                    ORDER BY type, name, sql
                    """
                ).fetchall()
                if row[0] is not None
            )
        finally:
            connection.close()

    assert schema_signature(fresh) == schema_signature(upgraded)
