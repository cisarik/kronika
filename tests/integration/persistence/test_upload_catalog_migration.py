"""Migration evidence for upload publication catalog linkage."""

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


def _seed_0013_publication(database_path: Path) -> None:
    connection = _connect(database_path)
    try:
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
            ) VALUES (?, 'published', 'synthetic-upload-0001', 'synthetic.mp4',
                      8, 8, 'sha256', ?, 'video', 'mp4', ?, NULL,
                      10, 30, 100, NULL, 5)
            """,
            (
                "11111111-1111-4111-8111-111111111111",
                "a" * 64,
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            ),
        )
        publication_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        connection.execute(
            """
            INSERT INTO upload_publications (
                upload_id, publication_id, destination_id, relative_target,
                byte_identity_id, expected_size_bytes, checksum_algorithm, checksum_hex,
                validated_media_kind, validated_format, state, cleanup_state,
                created_at_ms, updated_at_ms, verified_at_ms,
                cleanup_completed_at_ms, version
            ) VALUES (?, ?, ?, ?, ?, 8, 'sha256', ?, 'video', 'mp4',
                      'verified', 'complete', 20, 31, 30, 31, 2)
            """,
            (
                "11111111-1111-4111-8111-111111111111",
                publication_id,
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                f"{publication_id.replace('-', '')}.mp4",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "a" * 64,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_upgrade_from_0013_preserves_publications_with_null_catalog_links(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0013")
    _seed_0013_publication(database_path)

    _migrate(database_path, "0014")

    connection = _connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT upload_id, state, cleanup_state, media_id, media_location_id, version
            FROM upload_publications
            """
        ).fetchone()
        assert row == (
            "11111111-1111-4111-8111-111111111111",
            "verified",
            "complete",
            None,
            None,
            2,
        )
        assert connection.execute("SELECT COUNT(*) FROM logical_media").fetchone() == (0,)
    finally:
        connection.close()


def _seed_valid_catalog_pair(connection: sqlite3.Connection) -> tuple[str, str]:
    media_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    location_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
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
        ) VALUES (?, ?, ?, 'cccccccccccc4ccc8cccccccccccccccc.mp4', 'available',
                  8, NULL, 40, 40)
        """,
        (
            location_id,
            media_id,
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ),
    )
    assert connection.execute(
        "SELECT COUNT(*) FROM logical_media WHERE id = ?",
        (media_id,),
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT COUNT(*) FROM physical_media_locations WHERE id = ?",
        (location_id,),
    ).fetchone() == (1,)
    return media_id, location_id


def _assert_catalog_linkage_check(error: sqlite3.IntegrityError) -> None:
    message = str(error).lower()
    if "ck_upload_publications_catalog_linkage" in message:
        return
    assert "check constraint" in message or "constraint failed" in message


def test_0014_linkage_constraints_and_restrictive_references(tmp_path: Path) -> None:
    database_path = tmp_path / "constraints" / "catalog.sqlite3"
    _migrate(database_path, "0014")
    _seed_0013_publication(database_path)
    connection = _connect(database_path)
    try:
        media_id, location_id = _seed_valid_catalog_pair(connection)
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError) as media_only:
            connection.execute(
                """
                UPDATE upload_publications
                SET media_id = ?, media_location_id = NULL
                WHERE upload_id = ?
                """,
                (
                    media_id,
                    "11111111-1111-4111-8111-111111111111",
                ),
            )
        _assert_catalog_linkage_check(media_only.value)
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError) as location_only:
            connection.execute(
                """
                UPDATE upload_publications
                SET media_id = NULL, media_location_id = ?
                WHERE upload_id = ?
                """,
                (
                    location_id,
                    "11111111-1111-4111-8111-111111111111",
                ),
            )
        _assert_catalog_linkage_check(location_only.value)
        connection.rollback()

        connection.execute(
            """
            UPDATE upload_publications
            SET media_id = ?, media_location_id = ?
            WHERE upload_id = ?
            """,
            (
                media_id,
                location_id,
                "11111111-1111-4111-8111-111111111111",
            ),
        )
        connection.commit()

        second_upload = "22222222-2222-4222-8222-222222222222"
        second_publication = "33333333-3333-4333-8333-333333333333"
        connection.execute(
            """
            INSERT INTO media_byte_identities (
                id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms
            ) VALUES (?, 'sha256', 8, ?, 10)
            """,
            ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab", "b" * 64),
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
            ) VALUES (?, 'published', 'synthetic-upload-0002', 'synthetic-2.mp4',
                      8, 8, 'sha256', ?, 'video', 'mp4', ?, NULL,
                      10, 30, 100, NULL, 5)
            """,
            (
                second_upload,
                "b" * 64,
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab",
            ),
        )
        connection.execute(
            """
            INSERT INTO upload_publications (
                upload_id, publication_id, destination_id, relative_target,
                byte_identity_id, expected_size_bytes, checksum_algorithm, checksum_hex,
                validated_media_kind, validated_format, state, cleanup_state,
                created_at_ms, updated_at_ms, verified_at_ms,
                cleanup_completed_at_ms, version, media_id, media_location_id
            ) VALUES (?, ?, ?, ?, ?, 8, 'sha256', ?, 'video', 'mp4',
                      'verified', 'complete', 20, 31, 30, 31, 2, NULL, NULL)
            """,
            (
                second_upload,
                second_publication,
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                f"{second_publication.replace('-', '')}.mp4",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab",
                "b" * 64,
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError) as duplicate_media:
            connection.execute(
                """
                UPDATE upload_publications
                SET media_id = ?, media_location_id = ?
                WHERE upload_id = ?
                """,
                (
                    media_id,
                    location_id,
                    second_upload,
                ),
            )
        assert "unique" in str(duplicate_media.value).lower()
        connection.rollback()

        other_media = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeef"
        other_location = "ffffffff-ffff-4fff-8fff-fffffffffffe"
        connection.execute(
            """
            INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
            VALUES (?, 'video', 50, 50)
            """,
            (other_media,),
        )
        connection.execute(
            """
            INSERT INTO physical_media_locations (
                id, media_id, library_id, relative_path, availability,
                observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, 'other-location.mp4', 'available',
                      8, NULL, 50, 50)
            """,
            (
                other_location,
                other_media,
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError) as duplicate_media_id:
            connection.execute(
                """
                UPDATE upload_publications
                SET media_id = ?, media_location_id = ?
                WHERE upload_id = ?
                """,
                (
                    media_id,
                    other_location,
                    second_upload,
                ),
            )
        assert "unique" in str(duplicate_media_id.value).lower()
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError) as duplicate_location_id:
            connection.execute(
                """
                UPDATE upload_publications
                SET media_id = ?, media_location_id = ?
                WHERE upload_id = ?
                """,
                (
                    other_media,
                    location_id,
                    second_upload,
                ),
            )
        assert "unique" in str(duplicate_location_id.value).lower()
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM logical_media WHERE id = ?",
                (media_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM physical_media_locations WHERE id = ?",
                (location_id,),
            )
        connection.rollback()
    finally:
        connection.close()


def test_0014_downgrade_removes_linkage_and_preserves_publication_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "roundtrip" / "catalog.sqlite3"
    _migrate(database_path, "0013")
    _seed_0013_publication(database_path)
    _migrate(database_path, "0014")
    _migrate(database_path, "0013", downgrade=True)

    connection = _connect(database_path)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(upload_publications)")
        }
        assert "media_id" not in columns
        assert "media_location_id" not in columns
        assert connection.execute(
            "SELECT upload_id, state, cleanup_state FROM upload_publications"
        ).fetchone() == (
            "11111111-1111-4111-8111-111111111111",
            "verified",
            "complete",
        )
    finally:
        connection.close()
