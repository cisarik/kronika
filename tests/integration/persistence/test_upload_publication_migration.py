"""Migration evidence for durable atomic upload publication provenance."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.catalog_schema import metadata
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine


def _settings(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


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


def _seed_0012_advanced_rows(database_path: Path) -> list[tuple[object, ...]]:
    connection = _connect(database_path)
    identity_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    try:
        connection.execute(
            """
            INSERT INTO media_byte_identities (
                id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms
            ) VALUES (?, 'sha256', 8, ?, 10)
            """,
            (identity_id, "a" * 64),
        )
        for index, state in enumerate(
            ("publish_pending", "published", "cataloged"),
            start=1,
        ):
            upload_id = f"{index:08d}-1111-4111-8111-{index:012d}"
            connection.execute(
                """
                INSERT INTO upload_sessions (
                    id, state, storage_key, display_filename,
                    declared_size_bytes, received_size_bytes,
                    checksum_algorithm, checksum_hex,
                    validated_media_kind, validated_format, byte_identity_id,
                    duplicate_disposition,
                    created_at_ms, updated_at_ms, expires_at_ms,
                    failure_code, version
                ) VALUES (?, ?, ?, 'synthetic.mp4', 8, 8,
                          'sha256', ?, 'video', 'mp4', ?, NULL,
                          10, 20, 100, NULL, 4)
                """,
                (upload_id, state, f"synthetic-upload-{index:04d}", "a" * 64, identity_id),
            )
        connection.commit()
        return connection.execute(
            """
            SELECT id, state, storage_key, byte_identity_id, duplicate_disposition, version
            FROM upload_sessions ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()


def _table_names(database_path: Path) -> set[str]:
    connection = _connect(database_path)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()


def _schema_signature(database_path: Path, table: str) -> dict[str, object]:
    connection = _connect(database_path)
    try:
        columns = tuple(
            (row[1], row[2], row[3], row[5])
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
        foreign_keys = tuple(
            sorted(
                (row[2], row[3], row[4], row[6])
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            )
        )
        indexes: list[tuple[object, ...]] = []
        for row in connection.execute(f"PRAGMA index_list({table})"):
            name = str(row[1])
            if name.startswith("sqlite_autoindex"):
                continue
            index_columns = tuple(
                index_row[2]
                for index_row in connection.execute(f"PRAGMA index_info({name})")
            )
            indexes.append((name, row[2], index_columns))
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        assert sql_row is not None
        named_constraints = tuple(
            sorted(re.findall(r"CONSTRAINT ([A-Za-z0-9_]+)", str(sql_row[0])))
        )
        return {
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": tuple(sorted(indexes)),
            "constraints": named_constraints,
        }
    finally:
        connection.close()


def _seed_publication_dependencies(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic device')",
        ("dddddddd-dddd-4ddd-8ddd-dddddddddddd",),
    )
    connection.execute(
        """
        INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path)
        VALUES (?, ?, 'Synthetic published originals', 'posix', '/synthetic/published')
        """,
        (
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        ),
    )
    connection.execute(
        """
        INSERT INTO media_byte_identities (
            id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms
        ) VALUES (?, 'sha256', 8, ?, 10)
        """,
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "a" * 64),
    )
    connection.execute(
        """
        INSERT INTO upload_sessions (
            id, state, storage_key, display_filename,
            declared_size_bytes, received_size_bytes,
            checksum_algorithm, checksum_hex,
            validated_media_kind, validated_format, byte_identity_id,
            duplicate_disposition,
            created_at_ms, updated_at_ms, expires_at_ms,
            failure_code, version
        ) VALUES (?, 'publish_pending', 'synthetic-upload-0001', 'synthetic.mp4',
                  8, 8, 'sha256', ?, 'video', 'mp4', ?, NULL,
                  10, 20, 100, NULL, 4)
        """,
        (
            "11111111-1111-4111-8111-111111111111",
            "a" * 64,
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        ),
    )


def _valid_publication_values() -> dict[str, object]:
    publication_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    return {
        "upload_id": "11111111-1111-4111-8111-111111111111",
        "publication_id": publication_id,
        "destination_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "relative_target": f"{publication_id.replace('-', '')}.mp4",
        "byte_identity_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "expected_size_bytes": 8,
        "checksum_algorithm": "sha256",
        "checksum_hex": "a" * 64,
        "validated_media_kind": "video",
        "validated_format": "mp4",
        "state": "reserved",
        "cleanup_state": "pending",
        "created_at_ms": 20,
        "updated_at_ms": 20,
        "verified_at_ms": None,
        "cleanup_completed_at_ms": None,
        "version": 0,
    }


def _insert_publication(connection: sqlite3.Connection, values: dict[str, object]) -> None:
    connection.execute(
        """
        INSERT INTO upload_publications (
            upload_id, publication_id, destination_id, relative_target,
            byte_identity_id, expected_size_bytes, checksum_algorithm, checksum_hex,
            validated_media_kind, validated_format, state, cleanup_state,
            created_at_ms, updated_at_ms, verified_at_ms,
            cleanup_completed_at_ms, version
        ) VALUES (
            :upload_id, :publication_id, :destination_id, :relative_target,
            :byte_identity_id, :expected_size_bytes, :checksum_algorithm, :checksum_hex,
            :validated_media_kind, :validated_format, :state, :cleanup_state,
            :created_at_ms, :updated_at_ms, :verified_at_ms,
            :cleanup_completed_at_ms, :version
        )
        """,
        values,
    )


def test_fresh_database_upgrades_to_head_with_empty_publication_table(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings(tmp_path / "fresh" / "catalog.sqlite3")
    status = upgrade_database_to_head(settings)

    assert status.current_revision == status.head_revision == "0035"
    assert "upload_publications" in _table_names(settings.database_path)
    connection = _connect(settings.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM upload_publications").fetchone() == (
            0,
        )
    finally:
        connection.close()


def test_upgrade_from_0012_preserves_pending_and_legacy_advanced_rows_without_provenance(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "incremental" / "catalog.sqlite3"
    _migrate(database_path, "0012")
    before = _seed_0012_advanced_rows(database_path)

    _migrate(database_path, "0013")

    connection = _connect(database_path)
    try:
        after = connection.execute(
            """
            SELECT id, state, storage_key, byte_identity_id, duplicate_disposition, version
            FROM upload_sessions ORDER BY id
            """
        ).fetchall()
        assert after == before
        assert connection.execute("SELECT COUNT(*) FROM upload_publications").fetchone() == (
            0,
        )
        assert [row[1] for row in after] == ["publish_pending", "published", "cataloged"]
    finally:
        connection.close()


def test_0013_downgrade_and_reupgrade_preserve_uploads_without_inventing_provenance(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "roundtrip" / "catalog.sqlite3"
    _migrate(database_path, "0012")
    before = _seed_0012_advanced_rows(database_path)
    _migrate(database_path, "0013")

    _migrate(database_path, "0012", downgrade=True)
    assert "upload_publications" not in _table_names(database_path)
    _migrate(database_path, "0013")

    connection = _connect(database_path)
    try:
        assert connection.execute(
            """
            SELECT id, state, storage_key, byte_identity_id, duplicate_disposition, version
            FROM upload_sessions ORDER BY id
            """
        ).fetchall() == before
        assert connection.execute("SELECT COUNT(*) FROM upload_publications").fetchone() == (
            0,
        )
    finally:
        connection.close()


def test_fresh_metadata_and_incremental_head_publication_schema_are_equivalent(
    tmp_path: Path,
) -> None:
    migrated = tmp_path / "equivalence" / "migrated.sqlite3"
    metadata_created = tmp_path / "equivalence" / "metadata.sqlite3"
    _migrate(migrated, "0014")
    metadata_created.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(metadata_created)
    try:
        metadata.create_all(engine)
    finally:
        dispose_engine(engine)

    assert _schema_signature(migrated, "upload_publications") == _schema_signature(
        metadata_created,
        "upload_publications",
    )


def test_0013_constraints_enforce_one_upload_one_opaque_target_and_restrict_deletes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraints" / "catalog.sqlite3"
    _migrate(database_path, "0013")
    connection = _connect(database_path)
    try:
        _seed_publication_dependencies(connection)
        values = _valid_publication_values()
        _insert_publication(connection, values)
        connection.commit()
        connection.execute("DELETE FROM upload_publications")
        connection.commit()
        for invalid in (
            values | {"publication_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"},
            values | {"relative_target": "client-name.mp4"},
            values
            | {
                "state": "reserved",
                "cleanup_state": "complete",
                "cleanup_completed_at_ms": 30,
            },
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_publication(connection, invalid)
            connection.rollback()
        _insert_publication(connection, values)
        connection.commit()
        for statement, parameter in (
            ("DELETE FROM upload_sessions WHERE id = ?", values["upload_id"]),
            ("DELETE FROM libraries WHERE id = ?", values["destination_id"]),
            (
                "DELETE FROM media_byte_identities WHERE id = ?",
                values["byte_identity_id"],
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, (parameter,))
            connection.rollback()
    finally:
        connection.close()
