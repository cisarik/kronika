"""Integration tests for upload-session migrations."""

from __future__ import annotations

import importlib.resources
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.catalog_schema import upload_sessions

PRODUCTION_VERSIONS_PACKAGE = (
    "kronika.infrastructure.persistence.alembic_environment.versions"
)
CURRENT_HEAD_REVISION = "0035"
TARGET_DUPLICATE_DISPOSITION_REVISION = "0012"
TARGET_BYTE_IDENTITY_REVISION = "0011"
TARGET_VALIDATION_REVISION = "0010"
TARGET_COMPLETENESS_REVISION = "0009"
TARGET_UPLOAD_SESSION_REVISION = "0008"
TARGET_PREVIOUS_REVISION = "0007"
COMPLETE_UPLOAD_SESSION_STATES = (
    "received",
    "validating",
    "duplicate_pending",
    "publish_pending",
    "published",
    "cataloged",
    "rejected",
)
VALIDATION_SUCCESS_UPLOAD_SESSION_STATES = (
    "duplicate_pending",
    "publish_pending",
    "published",
    "cataloged",
)


def _settings_for(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _upgrade_to_revision(database_path: Path, revision: str) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    settings = _settings_for(database_path)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, revision)
    finally:
        dispose_engine(engine)


def _downgrade_to_revision(database_path: Path, revision: str) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.downgrade(config, revision)
    finally:
        dispose_engine(engine)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_names(database_path: Path) -> set[str]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        connection.close()


def _columns(database_path: Path, table_name: str) -> dict[str, tuple[object, ...]]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]): row for row in rows}
    finally:
        connection.close()


def _indexes(database_path: Path, table_name: str) -> dict[str, tuple[object, ...]]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(f"PRAGMA index_list({table_name})").fetchall()
        return {str(row[1]): row for row in rows}
    finally:
        connection.close()


def _foreign_keys(database_path: Path, table_name: str) -> list[tuple[object, ...]]:
    connection = _connect(database_path)
    try:
        return connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
    finally:
        connection.close()


def _table_sql(database_path: Path, table_name: str) -> str:
    connection = _connect(database_path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def test_packaged_migration_resources_include_upload_session_revisions() -> None:
    versions = importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
    assert versions.joinpath("0008_upload_sessions.py").is_file()
    assert versions.joinpath("0009_upload_session_completeness.py").is_file()
    assert versions.joinpath("0010_upload_validation_evidence.py").is_file()
    assert versions.joinpath("0011_upload_byte_identities.py").is_file()
    assert versions.joinpath("0012_upload_duplicate_disposition.py").is_file()
    assert versions.joinpath("0016_still_image_media_kinds.py").is_file()

    from kronika.infrastructure.persistence.migrations import load_script_directory

    script = load_script_directory()
    upload_revision = script.get_revision(TARGET_UPLOAD_SESSION_REVISION)
    assert upload_revision is not None
    assert upload_revision.down_revision == TARGET_PREVIOUS_REVISION
    completeness_revision = script.get_revision(TARGET_COMPLETENESS_REVISION)
    assert completeness_revision is not None
    assert completeness_revision.down_revision == TARGET_UPLOAD_SESSION_REVISION
    validation_revision = script.get_revision(TARGET_VALIDATION_REVISION)
    assert validation_revision is not None
    assert validation_revision.down_revision == TARGET_COMPLETENESS_REVISION
    identity_revision = script.get_revision(TARGET_BYTE_IDENTITY_REVISION)
    assert identity_revision is not None
    assert identity_revision.down_revision == TARGET_VALIDATION_REVISION
    disposition_revision = script.get_revision(TARGET_DUPLICATE_DISPOSITION_REVISION)
    assert disposition_revision is not None
    assert disposition_revision.down_revision == TARGET_BYTE_IDENTITY_REVISION
    assert script.get_heads() == [CURRENT_HEAD_REVISION]


def test_empty_database_upgrades_to_current_head_revision_0012(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "head-0012.sqlite3")
    status = upgrade_database_to_head(settings)

    assert status.state == "at_head"
    assert status.current_revision == CURRENT_HEAD_REVISION
    assert status.head_revision == CURRENT_HEAD_REVISION
    assert inspect_database_migration_status(settings) == status
    assert "upload_sessions" in _table_names(settings.database_path)


def test_upload_sessions_table_has_required_columns_constraints_and_indexes(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "schema.sqlite3")
    upgrade_database_to_head(settings)

    assert set(_columns(settings.database_path, "upload_sessions")) == {
        "id",
        "state",
        "storage_key",
        "display_filename",
        "declared_size_bytes",
        "received_size_bytes",
        "checksum_algorithm",
        "checksum_hex",
        "validated_media_kind",
        "validated_format",
        "byte_identity_id",
        "duplicate_disposition",
        "created_by_login_key",
        "duplicate_resolution_mode",
        "created_at_ms",
        "updated_at_ms",
        "expires_at_ms",
        "failure_code",
        "version",
    }
    table_sql = _table_sql(settings.database_path, "upload_sessions")
    for name in (
        "ck_upload_sessions_state",
        "ck_upload_sessions_declared_size_positive",
        "ck_upload_sessions_received_size_non_negative",
        "ck_upload_sessions_received_size_not_over_declared",
        "ck_upload_sessions_created_received_size_zero",
        "ck_upload_sessions_complete_states_received_size_exact",
        "ck_upload_sessions_checksum_pair",
        "ck_upload_sessions_checksum_hex",
        "ck_upload_sessions_validation_evidence_pair",
        "ck_upload_sessions_validated_states_have_evidence",
        "ck_upload_sessions_duplicate_disposition",
        "ck_upload_sessions_created_by_login_key",
        "ck_upload_sessions_duplicate_resolution_mode",
        "ck_upload_sessions_expires_after_created",
        "ck_upload_sessions_failure_code_sanitized",
        "ck_upload_sessions_version_non_negative",
        "uq_upload_sessions_storage_key",
    ):
        assert name in table_sql
    indexes = _indexes(settings.database_path, "upload_sessions")
    assert "ix_upload_sessions_state" in indexes
    assert "ix_upload_sessions_expires_at_ms" in indexes
    assert "ix_upload_sessions_state_expires_at_ms" in indexes
    assert "ix_upload_sessions_byte_identity_id" in indexes
    assert "ix_upload_sessions_created_by_login_key" in indexes
    foreign_keys = _foreign_keys(settings.database_path, "upload_sessions")
    assert any(row[2] == "media_byte_identities" for row in foreign_keys)
    identity_columns = _columns(settings.database_path, "media_byte_identities")
    assert set(identity_columns) == {
        "id",
        "checksum_algorithm",
        "size_bytes",
        "checksum_hex",
        "created_at_ms",
    }


def test_upload_session_constraints_reject_invalid_rows(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "constraints.sqlite3")
    upgrade_database_to_head(settings)
    connection = _connect(settings.database_path)
    try:
        valid = {
            "id": "11111111-1111-4111-8111-111111111111",
            "state": "created",
            "storage_key": "upload-session-0001",
            "display_filename": "example.mp4",
            "declared_size_bytes": 100,
            "received_size_bytes": 0,
            "checksum_algorithm": None,
            "checksum_hex": None,
            "created_at_ms": 10,
            "updated_at_ms": 10,
            "expires_at_ms": 20,
            "failure_code": None,
            "version": 0,
        }
        _insert_upload_session(connection, valid)
        for overrides in (
            {"id": "22222222-2222-4222-8222-222222222222", "state": "unknown"},
            {"id": "22222222-2222-4222-8222-222222222222", "declared_size_bytes": 0},
            {"id": "22222222-2222-4222-8222-222222222222", "received_size_bytes": -1},
            {"id": "22222222-2222-4222-8222-222222222222", "received_size_bytes": 101},
            {"id": "22222222-2222-4222-8222-222222222222", "received_size_bytes": 1},
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "state": "received",
                "received_size_bytes": 99,
            },
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "checksum_algorithm": "sha256",
                "checksum_hex": "A" * 64,
            },
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "checksum_algorithm": "md5",
                "checksum_hex": "a" * 64,
            },
            {"id": "22222222-2222-4222-8222-222222222222", "expires_at_ms": 10},
            {"id": "22222222-2222-4222-8222-222222222222", "version": -1},
            {"id": "22222222-2222-4222-8222-222222222222", "storage_key": "/tmp/file"},
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "display_filename": "nested/file.mp4",
            },
        ):
            row = valid | overrides | {"storage_key": f"upload-session-{len(str(overrides)):04d}"}
            if "storage_key" in overrides:
                row["storage_key"] = overrides["storage_key"]
            try:
                _insert_upload_session(connection, row)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(f"invalid row accepted: {overrides}")
        invalid_validation_pair = valid | {
            "id": "33333333-3333-4333-8333-333333333333",
            "storage_key": "upload-session-invalid-validation",
            "state": "received",
            "validated_media_kind": "video",
            "validated_format": "gif",
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO upload_sessions (
                    id,
                    state,
                    storage_key,
                    display_filename,
                    declared_size_bytes,
                    received_size_bytes,
                    checksum_algorithm,
                    checksum_hex,
                    validated_media_kind,
                    validated_format,
                    created_at_ms,
                    updated_at_ms,
                    expires_at_ms,
                    failure_code,
                    version
                ) VALUES (
                    :id,
                    :state,
                    :storage_key,
                    :display_filename,
                    :declared_size_bytes,
                    :received_size_bytes,
                    :checksum_algorithm,
                    :checksum_hex,
                    :validated_media_kind,
                    :validated_format,
                    :created_at_ms,
                    :updated_at_ms,
                    :expires_at_ms,
                    :failure_code,
                    :version
                )
                """,
                invalid_validation_pair,
            )
    finally:
        connection.close()


def test_upgrade_from_0007_preserves_existing_catalog_rows_and_adds_empty_upload_sessions(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "preserve.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)
    before_tables = _table_names(settings.database_path)

    status = upgrade_database_to_head(settings)

    assert status.current_revision == CURRENT_HEAD_REVISION
    assert before_tables | {
        "upload_sessions",
        "media_byte_identities",
        "upload_publications",
        "media_analysis_runs",
        "media_genres",
        "youtube_acquisition_claims",
        "security_audit_events",
        "media_content_publications",
        "media_covers",
        "media_catalog_removal_receipts",
        "x_post_claims",
        "x_assets",
        "media_user_aliases",
        "media_user_alias_tags",
        "x_claim_pending_aliases",
        "x_claim_pending_alias_tags",
        "companion_review_open_states",
        "companion_review_field_sources",
        "companion_review_tag_sources",
        "media_analysis_proposals",
        "research_requests",
        "research_active_slot",
        "research_operations",
        "research_budget_holds",
        "kronika_documents",
        "kronika_records",
        "kronika_approved_media",
        "kronika_approved_media_tags",
        "kronika_approved_media_locations",
    } == _table_names(settings.database_path)
    connection = _connect(settings.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM upload_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM upload_publications").fetchone()[0] == 0
    finally:
        connection.close()


def test_upgrade_from_0008_to_0009_preserves_valid_upload_session_rows(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "valid-completeness.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_UPLOAD_SESSION_REVISION)
    rows = [
        _valid_upload_session_values(
            "11111111-1111-4111-8111-111111111111",
            "upload-session-0001",
            state="created",
            received_size_bytes=0,
        ),
        _valid_upload_session_values(
            "22222222-2222-4222-8222-222222222222",
            "upload-session-0002",
            state="receiving",
            received_size_bytes=25,
        ),
        _valid_upload_session_values(
            "33333333-3333-4333-8333-333333333333",
            "upload-session-0003",
            state="cancelled",
            received_size_bytes=25,
        ),
        _valid_upload_session_values(
            "44444444-4444-4444-8444-444444444444",
            "upload-session-0004",
            state="failed",
            received_size_bytes=100,
        ),
        _valid_upload_session_values(
            "55555555-5555-4555-8555-555555555555",
            "upload-session-0005",
            state="received",
            received_size_bytes=100,
        ),
        _valid_upload_session_values(
            "66666666-6666-4666-8666-666666666666",
            "upload-session-0006",
            state="published",
            received_size_bytes=100,
        ),
    ]
    connection = _connect(settings.database_path)
    try:
        for row in rows:
            _insert_upload_session(connection, row)
        connection.commit()
    finally:
        connection.close()

    _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)

    assert _upload_session_rows(settings.database_path) == rows


def test_upgrade_from_0008_to_0009_fails_closed_for_invalid_created_row(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "invalid-created.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_UPLOAD_SESSION_REVISION)
    invalid = _valid_upload_session_values(
        "11111111-1111-4111-8111-111111111111",
        "private-storage-key-0001",
        state="created",
        received_size_bytes=1,
        display_filename="Private Clip.mp4",
    )
    connection = _connect(settings.database_path)
    try:
        _insert_upload_session(connection, invalid)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception) as error:
        _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)

    message = str(error.value)
    assert "private-storage-key-0001" not in message
    assert "Private Clip.mp4" not in message


@pytest.mark.parametrize("state", COMPLETE_UPLOAD_SESSION_STATES)
def test_upgrade_from_0008_to_0009_fails_closed_for_incomplete_complete_state(
    tmp_path: Path,
    state: str,
) -> None:
    settings = _settings_for(tmp_path / f"invalid-{state}.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_UPLOAD_SESSION_REVISION)
    invalid = _valid_upload_session_values(
        "11111111-1111-4111-8111-111111111111",
        "private-storage-key-0001",
        state=state,
        received_size_bytes=99,
        display_filename="Private Clip.mp4",
    )
    connection = _connect(settings.database_path)
    try:
        _insert_upload_session(connection, invalid)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception) as error:
        _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)

    message = str(error.value)
    assert "private-storage-key-0001" not in message
    assert "Private Clip.mp4" not in message


def test_upgrade_from_0009_to_0010_preserves_rows_that_do_not_require_validation_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "valid-validation-evidence.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)
    rows = [
        _valid_upload_session_values(
            "11111111-1111-4111-8111-111111111111",
            "upload-session-0001",
            state="created",
            received_size_bytes=0,
        ),
        _valid_upload_session_values(
            "22222222-2222-4222-8222-222222222222",
            "upload-session-0002",
            state="received",
            received_size_bytes=100,
        ),
        _valid_upload_session_values(
            "33333333-3333-4333-8333-333333333333",
            "upload-session-0003",
            state="validating",
            received_size_bytes=100,
        ),
        _valid_upload_session_values(
            "44444444-4444-4444-8444-444444444444",
            "upload-session-0004",
            state="rejected",
            received_size_bytes=100,
        ),
    ]
    connection = _connect(settings.database_path)
    try:
        for row in rows:
            _insert_upload_session(connection, row)
        connection.commit()
    finally:
        connection.close()

    _upgrade_to_revision(settings.database_path, TARGET_VALIDATION_REVISION)

    assert _upload_session_rows(settings.database_path) == rows
    columns = _columns(settings.database_path, "upload_sessions")
    assert "validated_media_kind" in columns
    assert "validated_format" in columns


@pytest.mark.parametrize(
    "state",
    ("duplicate_pending", "publish_pending", "published", "cataloged"),
)
def test_upgrade_from_0009_to_0010_fails_closed_for_advanced_rows_without_evidence(
    tmp_path: Path,
    state: str,
) -> None:
    settings = _settings_for(tmp_path / f"invalid-validation-{state}.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)
    invalid = _valid_upload_session_values(
        "11111111-1111-4111-8111-111111111111",
        "private-storage-key-0001",
        state=state,
        received_size_bytes=100,
        display_filename="Private Clip.mp4",
    )
    connection = _connect(settings.database_path)
    try:
        _insert_upload_session(connection, invalid)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception) as error:
        _upgrade_to_revision(settings.database_path, TARGET_VALIDATION_REVISION)

    message = str(error.value)
    assert "private-storage-key-0001" not in message
    assert "Private Clip.mp4" not in message


@pytest.mark.parametrize("state", ("publish_pending", "published", "cataloged"))
def test_upgrade_wrapper_reports_legacy_advanced_rows_as_migration_failure(
    tmp_path: Path,
    state: str,
) -> None:
    from kronika.infrastructure.persistence.errors import FrameNestMigrationError
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / f"wrapper-invalid-validation-{state}.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)
    invalid = _valid_upload_session_values(
        "11111111-1111-4111-8111-111111111111",
        "private-storage-key-0001",
        state=state,
        received_size_bytes=100,
        display_filename="Private Clip.mp4",
    )
    connection = _connect(settings.database_path)
    try:
        _insert_upload_session(connection, invalid)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(FrameNestMigrationError) as error:
        upgrade_database_to_head(settings)

    assert error.value.error_code == "MIGRATION_FAILED"
    message = str(error.value)
    assert message == "Database migration failed."
    assert "private-storage-key-0001" not in message
    assert "Private Clip.mp4" not in message
    status = inspect_database_migration_status(settings)
    assert status.current_revision == TARGET_COMPLETENESS_REVISION
    assert status.head_revision == CURRENT_HEAD_REVISION


def test_media_byte_identity_constraints_reject_invalid_rows(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "byte-identity-constraints.sqlite3")
    upgrade_database_to_head(settings)
    connection = _connect(settings.database_path)
    try:
        valid = {
            "id": "11111111-1111-4111-8111-111111111111",
            "checksum_algorithm": "sha256",
            "size_bytes": 100,
            "checksum_hex": "a" * 64,
            "created_at_ms": 10,
        }
        _insert_byte_identity(connection, valid)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_byte_identity(
                connection,
                valid
                | {
                    "id": "22222222-2222-4222-8222-222222222222",
                },
            )
        for overrides in (
            {"id": "33333333-3333-4333-8333-333333333333", "checksum_algorithm": "md5"},
            {"id": "33333333-3333-4333-8333-333333333333", "size_bytes": 0},
            {"id": "33333333-3333-4333-8333-333333333333", "size_bytes": -1},
            {"id": "33333333-3333-4333-8333-333333333333", "checksum_hex": "A" * 64},
            {"id": "33333333-3333-4333-8333-333333333333", "checksum_hex": "a" * 63},
            {"id": "33333333-3333-4333-8333-333333333333", "checksum_hex": "g" * 64},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_byte_identity(connection, valid | overrides)
    finally:
        connection.close()


def test_upgrade_0011_to_0012_preserves_rows_and_constrains_disposition(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "duplicate-disposition.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_BYTE_IDENTITY_REVISION)
    identity_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    rows = (
        (
            "11111111-1111-4111-8111-111111111111",
            "publish_pending",
            "upload-session-1201",
        ),
        (
            "22222222-2222-4222-8222-222222222222",
            "duplicate_pending",
            "upload-session-1202",
        ),
    )
    connection = _connect(settings.database_path)
    try:
        _insert_byte_identity(
            connection,
            {
                "id": identity_id,
                "checksum_algorithm": "sha256",
                "size_bytes": 100,
                "checksum_hex": "a" * 64,
                "created_at_ms": 10,
            },
        )
        connection.executemany(
            """
            INSERT INTO upload_sessions (
                id, state, storage_key, display_filename,
                declared_size_bytes, received_size_bytes,
                checksum_algorithm, checksum_hex,
                validated_media_kind, validated_format, byte_identity_id,
                created_at_ms, updated_at_ms, expires_at_ms,
                failure_code, version
            ) VALUES (?, ?, ?, 'example.mp4', 100, 100,
                      'sha256', ?, 'video', 'mp4', ?,
                      10, 10, 1000, NULL, 0)
            """,
            tuple(
                (row_id, state, storage_key, "a" * 64, identity_id)
                for row_id, state, storage_key in rows
            ),
        )
        connection.commit()
    finally:
        connection.close()

    _upgrade_to_revision(settings.database_path, TARGET_DUPLICATE_DISPOSITION_REVISION)

    connection = _connect(settings.database_path)
    try:
        migrated = connection.execute(
            """
            SELECT id, state, checksum_hex, byte_identity_id, duplicate_disposition
            FROM upload_sessions ORDER BY id
            """
        ).fetchall()
        assert migrated == [
            (rows[0][0], rows[0][1], "a" * 64, identity_id, None),
            (rows[1][0], rows[1][1], "a" * 64, identity_id, None),
        ]
        connection.execute(
            "UPDATE upload_sessions SET duplicate_disposition = 'keep_separate' WHERE id = ?",
            (rows[0][0],),
        )
        connection.execute(
            "UPDATE upload_sessions SET state = 'cancelled', duplicate_disposition = 'discard' WHERE id = ?",
            (rows[1][0],),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE upload_sessions SET duplicate_disposition = 'merge' WHERE id = ?",
                (rows[0][0],),
            )
    finally:
        connection.close()

    assert _alembic_revision(settings.database_path) == TARGET_DUPLICATE_DISPOSITION_REVISION
    _downgrade_to_revision(settings.database_path, TARGET_BYTE_IDENTITY_REVISION)

    assert "duplicate_disposition" not in _columns(settings.database_path, "upload_sessions")
    connection = _connect(settings.database_path)
    try:
        downgraded = connection.execute(
            "SELECT id, state, checksum_hex, byte_identity_id FROM upload_sessions ORDER BY id"
        ).fetchall()
        identities = connection.execute(
            "SELECT id, checksum_hex FROM media_byte_identities"
        ).fetchall()
    finally:
        connection.close()
    assert downgraded == [
        (rows[0][0], "publish_pending", "a" * 64, identity_id),
        (rows[1][0], "cancelled", "a" * 64, identity_id),
    ]
    assert identities == [(identity_id, "a" * 64)]

    _upgrade_to_revision(settings.database_path, TARGET_DUPLICATE_DISPOSITION_REVISION)
    connection = _connect(settings.database_path)
    try:
        dispositions = connection.execute(
            "SELECT duplicate_disposition FROM upload_sessions ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    assert dispositions == [(None,), (None,)]


def test_upgrade_from_0010_backfills_coherent_successful_byte_identities(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "backfill-byte-identities.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_VALIDATION_REVISION)
    shared_digest = "a" * 64
    other_digest = "b" * 64
    connection = _connect(settings.database_path)
    try:
        _insert_upload_session_0010(
            connection,
            _valid_upload_session_values(
                "11111111-1111-4111-8111-111111111111",
                "upload-session-1001",
                state="publish_pending",
                received_size_bytes=100,
            ),
            checksum_hex=shared_digest,
            validated_media_kind="video",
            validated_format="mp4",
        )
        _insert_upload_session_0010(
            connection,
            _valid_upload_session_values(
                "22222222-2222-4222-8222-222222222222",
                "upload-session-1002",
                state="published",
                received_size_bytes=100,
            ),
            checksum_hex=shared_digest,
            validated_media_kind="video",
            validated_format="mp4",
        )
        _insert_upload_session_0010(
            connection,
            _valid_upload_session_values(
                "33333333-3333-4333-8333-333333333333",
                "upload-session-1003",
                state="cataloged",
                received_size_bytes=100,
            ),
            checksum_hex=other_digest,
            validated_media_kind="animated_image",
            validated_format="gif",
        )
        _insert_upload_session_0010(
            connection,
            _valid_upload_session_values(
                "44444444-4444-4444-8444-444444444444",
                "upload-session-1004",
                state="received",
                received_size_bytes=100,
            ),
            checksum_hex=shared_digest,
            validated_media_kind=None,
            validated_format=None,
        )
        connection.commit()
    finally:
        connection.close()

    _upgrade_to_revision(settings.database_path, TARGET_BYTE_IDENTITY_REVISION)

    connection = _connect(settings.database_path)
    try:
        identity_rows = connection.execute(
            """
            SELECT checksum_algorithm, size_bytes, checksum_hex
            FROM media_byte_identities
            ORDER BY checksum_hex
            """
        ).fetchall()
        links = connection.execute(
            """
            SELECT id, byte_identity_id
            FROM upload_sessions
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()

    assert identity_rows == [
        ("sha256", 100, shared_digest),
        ("sha256", 100, other_digest),
    ]
    assert links[0][1] is not None
    assert links[1][1] == links[0][1]
    assert links[2][1] is not None
    assert links[2][1] != links[0][1]
    assert links[3][1] is None


@pytest.mark.parametrize(
    ("state", "overrides"),
    (
        pytest.param(
            "publish_pending",
            {"checksum_algorithm": None},
            id="publish-pending-null-algorithm",
        ),
        pytest.param(
            "duplicate_pending",
            {"checksum_algorithm": "md5"},
            id="duplicate-pending-wrong-algorithm",
        ),
        pytest.param(
            "published",
            {"checksum_hex": None},
            id="published-null-checksum",
        ),
        pytest.param(
            "cataloged",
            {"checksum_hex": "a" * 63},
            id="cataloged-short-checksum",
        ),
        pytest.param(
            "duplicate_pending",
            {"checksum_hex": "A" * 64},
            id="duplicate-pending-uppercase-checksum",
        ),
        pytest.param(
            "publish_pending",
            {"checksum_hex": "g" * 64},
            id="publish-pending-non-hex-checksum",
        ),
        pytest.param(
            "published",
            {"declared_size_bytes": 0, "received_size_bytes": 0},
            id="published-zero-declared-size",
        ),
        pytest.param(
            "cataloged",
            {"declared_size_bytes": -1, "received_size_bytes": -1},
            id="cataloged-negative-declared-size",
        ),
        pytest.param(
            "duplicate_pending",
            {"received_size_bytes": 0},
            id="duplicate-pending-zero-received-size",
        ),
        pytest.param(
            "publish_pending",
            {"received_size_bytes": -1},
            id="publish-pending-negative-received-size",
        ),
        pytest.param(
            "published",
            {"received_size_bytes": 99},
            id="published-size-mismatch",
        ),
        pytest.param(
            "publish_pending",
            {"validated_media_kind": None},
            id="publish-pending-null-media-kind",
        ),
        pytest.param(
            "publish_pending",
            {"validated_format": None},
            id="publish-pending-null-format",
        ),
        pytest.param(
            "cataloged",
            {"validated_media_kind": "image"},
            id="cataloged-unsupported-media-kind",
        ),
        pytest.param(
            "duplicate_pending",
            {"validated_format": "avi"},
            id="duplicate-pending-unsupported-format",
        ),
        pytest.param(
            "published",
            {"validated_media_kind": "video", "validated_format": "gif"},
            id="published-mismatched-media-pair",
        ),
    ),
)
def test_upgrade_from_0010_rejects_incoherent_successful_byte_identity_matrix(
    tmp_path: Path,
    state: str,
    overrides: dict[str, object],
) -> None:
    assert state in VALIDATION_SUCCESS_UPLOAD_SESSION_STATES
    database_name = f"invalid-byte-identity-{state}-{len(str(overrides))}.sqlite3"
    settings = _settings_for(tmp_path / database_name)
    _upgrade_to_revision(settings.database_path, TARGET_VALIDATION_REVISION)
    base_row = _valid_upload_session_values(
        "11111111-1111-4111-8111-111111111111",
        "private-storage-key-0001",
        state=state,
        received_size_bytes=100,
        display_filename="Private Clip.mp4",
    ) | {
        "checksum_algorithm": "sha256",
        "checksum_hex": "a" * 64,
        "validated_media_kind": "video",
        "validated_format": "mp4",
    }
    invalid_row = base_row | overrides
    connection = _connect(settings.database_path)
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        _insert_upload_session_0010_raw(connection, invalid_row)
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        connection.commit()
    finally:
        connection.close()
    original_row = _upload_session_0010_row(settings.database_path, str(invalid_row["id"]))
    original_table_sql = _table_sql(settings.database_path, "upload_sessions")

    with pytest.raises(RuntimeError) as error:
        _upgrade_to_revision(settings.database_path, TARGET_BYTE_IDENTITY_REVISION)

    _assert_0011_preflight_rejection_left_0010_database(
        settings.database_path,
        error.value,
        original_row=original_row,
        original_table_sql=original_table_sql,
    )


def test_upgrade_from_0010_rejects_incoherent_successful_byte_identity_and_retries(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "retry-byte-identity-backfill.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_VALIDATION_REVISION)
    upload_id = "11111111-1111-4111-8111-111111111111"
    invalid_row = _valid_upload_session_values(
        upload_id,
        "private-storage-key-0001",
        state="publish_pending",
        received_size_bytes=100,
        display_filename="Private Clip.mp4",
    ) | {
        "checksum_algorithm": None,
        "checksum_hex": "a" * 64,
        "validated_media_kind": "video",
        "validated_format": "mp4",
    }
    connection = _connect(settings.database_path)
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        _insert_upload_session_0010_raw(connection, invalid_row)
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        connection.commit()
    finally:
        connection.close()
    original_row = _upload_session_0010_row(settings.database_path, upload_id)
    original_table_sql = _table_sql(settings.database_path, "upload_sessions")

    with pytest.raises(RuntimeError) as error:
        _upgrade_to_revision(settings.database_path, TARGET_BYTE_IDENTITY_REVISION)

    _assert_0011_preflight_rejection_left_0010_database(
        settings.database_path,
        error.value,
        original_row=original_row,
        original_table_sql=original_table_sql,
    )

    connection = _connect(settings.database_path)
    try:
        connection.execute(
            """
            UPDATE upload_sessions
            SET checksum_algorithm = 'sha256'
            WHERE id = ?
            """,
            (upload_id,),
        )
        connection.commit()
    finally:
        connection.close()

    _upgrade_to_revision(settings.database_path, TARGET_BYTE_IDENTITY_REVISION)

    connection = _connect(settings.database_path)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        identity_rows = connection.execute(
            """
            SELECT checksum_algorithm, size_bytes, checksum_hex
            FROM media_byte_identities
            """
        ).fetchall()
        upload_row = connection.execute(
            """
            SELECT byte_identity_id
            FROM upload_sessions
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()
    finally:
        connection.close()

    assert revision == (TARGET_BYTE_IDENTITY_REVISION,)
    assert identity_rows == [("sha256", 100, "a" * 64)]
    assert upload_row is not None
    assert upload_row[0] is not None


def test_0010_schema_keeps_upload_sizes_not_null_before_byte_identity_migration(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "size-nullability-0010.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_VALIDATION_REVISION)

    columns = _columns(settings.database_path, "upload_sessions")

    assert columns["declared_size_bytes"][3] == 1
    assert columns["received_size_bytes"][3] == 1


def test_downgrade_from_0009_to_0008_preserves_rows_and_upgrade_can_run_again(
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path / "downgrade-completeness.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)
    rows = [
        _valid_upload_session_values(
            "11111111-1111-4111-8111-111111111111",
            "upload-session-0001",
            state="created",
            received_size_bytes=0,
        ),
        _valid_upload_session_values(
            "22222222-2222-4222-8222-222222222222",
            "upload-session-0002",
            state="received",
            received_size_bytes=100,
        ),
    ]
    connection = _connect(settings.database_path)
    try:
        for row in rows:
            _insert_upload_session(connection, row)
        connection.commit()
    finally:
        connection.close()

    _downgrade_to_revision(settings.database_path, TARGET_UPLOAD_SESSION_REVISION)
    assert _upload_session_rows(settings.database_path) == rows
    assert _table_sql(
        settings.database_path,
        "upload_sessions",
    ).find("ck_upload_sessions_complete_states_received_size_exact") == -1

    _upgrade_to_revision(settings.database_path, TARGET_COMPLETENESS_REVISION)

    assert _upload_session_rows(settings.database_path) == rows
    connection = _connect(settings.database_path)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert row == (TARGET_COMPLETENESS_REVISION,)


def test_shared_metadata_and_migrated_schema_have_equivalent_upload_constraints_and_indexes(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "metadata-equivalence.sqlite3")
    upgrade_database_to_head(settings)

    metadata_constraint_names = {
        constraint.name
        for constraint in upload_sessions.constraints
        if constraint.name is not None
    }
    table_sql = _table_sql(settings.database_path, "upload_sessions")
    for name in metadata_constraint_names:
        assert name in table_sql

    metadata_index_names = {index.name for index in upload_sessions.indexes}
    assert metadata_index_names <= set(_indexes(settings.database_path, "upload_sessions"))


def test_downgrade_from_0008_to_0007_removes_only_upload_sessions(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path / "downgrade.sqlite3")
    _upgrade_to_revision(settings.database_path, TARGET_UPLOAD_SESSION_REVISION)
    assert "upload_sessions" in _table_names(settings.database_path)

    _downgrade_to_revision(settings.database_path, TARGET_PREVIOUS_REVISION)

    assert "upload_sessions" not in _table_names(settings.database_path)
    connection = _connect(settings.database_path)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert row == (TARGET_PREVIOUS_REVISION,)


def _valid_upload_session_values(
    session_id: str,
    storage_key: str,
    *,
    state: str,
    received_size_bytes: int,
    display_filename: str = "example.mp4",
) -> dict[str, object]:
    return {
        "id": session_id,
        "state": state,
        "storage_key": storage_key,
        "display_filename": display_filename,
        "declared_size_bytes": 100,
        "received_size_bytes": received_size_bytes,
        "checksum_algorithm": None,
        "checksum_hex": None,
        "created_at_ms": 10,
        "updated_at_ms": 10,
        "expires_at_ms": 20,
        "failure_code": None,
        "version": 0,
    }


def _upload_session_rows(database_path: Path) -> list[dict[str, object]]:
    connection = _connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT
                id,
                state,
                storage_key,
                display_filename,
                declared_size_bytes,
                received_size_bytes,
                checksum_algorithm,
                checksum_hex,
                created_at_ms,
                updated_at_ms,
                expires_at_ms,
                failure_code,
                version
            FROM upload_sessions
            ORDER BY id
            """
        ).fetchall()
        columns = [
            "id",
            "state",
            "storage_key",
            "display_filename",
            "declared_size_bytes",
            "received_size_bytes",
            "checksum_algorithm",
            "checksum_hex",
            "created_at_ms",
            "updated_at_ms",
            "expires_at_ms",
            "failure_code",
            "version",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        connection.close()


def _upload_session_0010_row(database_path: Path, upload_id: str) -> dict[str, object]:
    connection = _connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT
                id,
                state,
                storage_key,
                display_filename,
                declared_size_bytes,
                received_size_bytes,
                checksum_algorithm,
                checksum_hex,
                validated_media_kind,
                validated_format,
                created_at_ms,
                updated_at_ms,
                expires_at_ms,
                failure_code,
                version
            FROM upload_sessions
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()
        assert row is not None
        columns = [
            "id",
            "state",
            "storage_key",
            "display_filename",
            "declared_size_bytes",
            "received_size_bytes",
            "checksum_algorithm",
            "checksum_hex",
            "validated_media_kind",
            "validated_format",
            "created_at_ms",
            "updated_at_ms",
            "expires_at_ms",
            "failure_code",
            "version",
        ]
        return dict(zip(columns, row, strict=True))
    finally:
        connection.close()


def _alembic_revision(database_path: Path) -> str:
    connection = _connect(database_path)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def _assert_0011_preflight_rejection_left_0010_database(
    database_path: Path,
    error: RuntimeError,
    *,
    original_row: dict[str, object],
    original_table_sql: str,
) -> None:
    message = str(error)
    assert type(error) is RuntimeError
    assert message == "Upload byte identity migration failed."
    assert "SELECT" not in message
    assert "upload_sessions" not in message
    assert "media_byte_identities" not in message
    assert str(database_path) not in message
    assert str(original_row["id"]) not in message
    assert str(original_row["storage_key"]) not in message
    assert str(original_row["display_filename"]) not in message

    table_names = _table_names(database_path)
    assert _alembic_revision(database_path) == TARGET_VALIDATION_REVISION
    assert "media_byte_identities" not in table_names
    assert not any(table_name.startswith("_alembic_tmp_") for table_name in table_names)
    assert "byte_identity_id" not in _columns(database_path, "upload_sessions")
    assert "ix_upload_sessions_byte_identity_id" not in _indexes(
        database_path,
        "upload_sessions",
    )
    assert not any(
        row[2] == "media_byte_identities"
        for row in _foreign_keys(database_path, "upload_sessions")
    )
    assert _table_sql(database_path, "upload_sessions") == original_table_sql
    assert _upload_session_0010_row(database_path, str(original_row["id"])) == original_row


def _insert_byte_identity(connection: sqlite3.Connection, values: dict[str, object]) -> None:
    connection.execute(
        """
        INSERT INTO media_byte_identities (
            id,
            checksum_algorithm,
            size_bytes,
            checksum_hex,
            created_at_ms
        ) VALUES (
            :id,
            :checksum_algorithm,
            :size_bytes,
            :checksum_hex,
            :created_at_ms
        )
        """,
        values,
    )


def _insert_upload_session_0010_raw(
    connection: sqlite3.Connection,
    values: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT INTO upload_sessions (
            id,
            state,
            storage_key,
            display_filename,
            declared_size_bytes,
            received_size_bytes,
            checksum_algorithm,
            checksum_hex,
            validated_media_kind,
            validated_format,
            created_at_ms,
            updated_at_ms,
            expires_at_ms,
            failure_code,
            version
        ) VALUES (
            :id,
            :state,
            :storage_key,
            :display_filename,
            :declared_size_bytes,
            :received_size_bytes,
            :checksum_algorithm,
            :checksum_hex,
            :validated_media_kind,
            :validated_format,
            :created_at_ms,
            :updated_at_ms,
            :expires_at_ms,
            :failure_code,
            :version
        )
        """,
        values,
    )


def _insert_upload_session_0010(
    connection: sqlite3.Connection,
    values: dict[str, object],
    *,
    checksum_hex: str | None,
    validated_media_kind: str | None,
    validated_format: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO upload_sessions (
            id,
            state,
            storage_key,
            display_filename,
            declared_size_bytes,
            received_size_bytes,
            checksum_algorithm,
            checksum_hex,
            validated_media_kind,
            validated_format,
            created_at_ms,
            updated_at_ms,
            expires_at_ms,
            failure_code,
            version
        ) VALUES (
            :id,
            :state,
            :storage_key,
            :display_filename,
            :declared_size_bytes,
            :received_size_bytes,
            :checksum_algorithm,
            :checksum_hex,
            :validated_media_kind,
            :validated_format,
            :created_at_ms,
            :updated_at_ms,
            :expires_at_ms,
            :failure_code,
            :version
        )
        """,
        values
        | {
            "checksum_algorithm": None if checksum_hex is None else "sha256",
            "checksum_hex": checksum_hex,
            "validated_media_kind": validated_media_kind,
            "validated_format": validated_format,
        },
    )


def _insert_upload_session(connection: sqlite3.Connection, values: dict[str, object]) -> None:
    connection.execute(
        """
        INSERT INTO upload_sessions (
            id,
            state,
            storage_key,
            display_filename,
            declared_size_bytes,
            received_size_bytes,
            checksum_algorithm,
            checksum_hex,
            created_at_ms,
            updated_at_ms,
            expires_at_ms,
            failure_code,
            version
        ) VALUES (
            :id,
            :state,
            :storage_key,
            :display_filename,
            :declared_size_bytes,
            :received_size_bytes,
            :checksum_algorithm,
            :checksum_hex,
            :created_at_ms,
            :updated_at_ms,
            :expires_at_ms,
            :failure_code,
            :version
        )
        """,
        values,
    )


def test_migration_0025_ownership_and_duplicate_mode_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
    )

    database_path = tmp_path / "ownership-0025.sqlite3"
    _upgrade_to_revision(database_path, "0024")
    assert inspect_database_migration_status(
        _settings_for(database_path)
    ).current_revision == "0024"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO upload_sessions ("
            "id, state, storage_key, display_filename, declared_size_bytes, "
            "received_size_bytes, created_at_ms, updated_at_ms, expires_at_ms, version"
            ") VALUES ("
            "'11111111-1111-4111-8111-111111111111', 'created', "
            "'upload-session-legacy01', 'legacy.mp4', 10, 0, 1, 1, 100, 0)"
        )
        connection.commit()
    _upgrade_to_revision(database_path, "0025")
    status = inspect_database_migration_status(_settings_for(database_path))
    assert status.current_revision == "0025"
    assert status.head_revision == "0035"
    columns = _columns(database_path, "upload_sessions")
    assert "created_by_login_key" in columns
    assert "duplicate_resolution_mode" in columns
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT created_by_login_key, duplicate_resolution_mode "
            "FROM upload_sessions WHERE id = ?",
            ("11111111-1111-4111-8111-111111111111",),
        ).fetchone()
    assert row == (None, "explicit")
    _downgrade_to_revision(database_path, "0024")
    assert inspect_database_migration_status(
        _settings_for(database_path)
    ).current_revision == "0024"
    columns_after = _columns(database_path, "upload_sessions")
    assert "created_by_login_key" not in columns_after
    assert "duplicate_resolution_mode" not in columns_after
