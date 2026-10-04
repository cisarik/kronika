"""Unit tests for the SQLite upload-session repository."""

from __future__ import annotations

import ast
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest
import sqlalchemy as sa

from kronika.application.ports.upload_sessions import (
    FrameNestUploadSessionRepositoryError,
    IncompleteUploadSessionError,
    InvalidUploadChecksumError,
    InvalidUploadSessionTransitionError,
    InvalidUploadValidationEvidenceError,
    UploadOffsetConflictError,
    UploadSessionAlreadyExistsError,
    UploadSessionConcurrencyConflictError,
    UploadSessionNotFoundError,
    UploadSizeLimitExceededError,
    UploadStorageKeyAlreadyExistsError,
)
from kronika.configuration import KronikaSettings
from kronika.domain import MediaByteIdentity, MediaByteIdentityId
from kronika.domain.uploads import (
    UploadDuplicateDisposition,
    UploadDuplicateResolutionMode,
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
    VALIDATED_UPLOAD_SESSION_STATES,
)
from kronika.infrastructure.persistence.catalog_schema import (
    media_byte_identities,
    upload_sessions,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
UPLOAD_REPOSITORY_MODULE = (
    REPOSITORY_ROOT
    / "src"
    / "kronika"
    / "infrastructure"
    / "persistence"
    / "upload_session_repository.py"
)


def _engine(tmp_path: Path) -> sa.Engine:
    database_path = tmp_path / "upload-sessions.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    return create_sqlite_engine(database_path)


def _repository(tmp_path: Path) -> tuple[SqliteUploadSessionRepository, sa.Engine]:
    engine = _engine(tmp_path)
    return SqliteUploadSessionRepository(engine), engine


def _session(
    *,
    session_id: UploadSessionId | None = None,
    storage_key: str = "upload-session-0001",
    state: UploadSessionState = UploadSessionState.CREATED,
    received_size_bytes: int = 0,
    declared_size_bytes: int = 100,
    checksum_hex: str | None = None,
    display_filename: str = "example.mp4",
    version: int = 0,
    validated_media_kind: UploadValidatedMediaKind | None = None,
    validated_format: UploadValidatedFormat | None = None,
    byte_identity_id: MediaByteIdentityId | None = None,
    duplicate_disposition: UploadDuplicateDisposition | None = None,
    created_by_login_key: str | None = None,
    duplicate_resolution_mode: UploadDuplicateResolutionMode = (
        UploadDuplicateResolutionMode.EXPLICIT
    ),
) -> UploadSession:
    if state in {
        UploadSessionState.RECEIVED,
        UploadSessionState.VALIDATING,
        UploadSessionState.DUPLICATE_PENDING,
        UploadSessionState.PUBLISH_PENDING,
        UploadSessionState.PUBLISHED,
        UploadSessionState.CATALOGED,
        UploadSessionState.REJECTED,
    } and received_size_bytes == 0:
        received_size_bytes = declared_size_bytes
    if state in VALIDATED_UPLOAD_SESSION_STATES:
        checksum_hex = checksum_hex or "a" * 64
        validated_media_kind = validated_media_kind or UploadValidatedMediaKind.VIDEO
        validated_format = validated_format or UploadValidatedFormat.MP4
        byte_identity_id = byte_identity_id or MediaByteIdentityId.new()
    return UploadSession(
        id=session_id or UploadSessionId.new(),
        state=state,
        storage_key=UploadStorageKey(storage_key),
        display_filename=UploadDisplayFilename(display_filename),
        declared_size_bytes=declared_size_bytes,
        received_size_bytes=received_size_bytes,
        checksum_algorithm=None if checksum_hex is None else "sha256",
        checksum_hex=checksum_hex,
        created_at_ms=10,
        updated_at_ms=10,
        expires_at_ms=1000,
        failure_code=None,
        version=version,
        validated_media_kind=validated_media_kind,
        validated_format=validated_format,
        byte_identity_id=byte_identity_id,
        duplicate_disposition=duplicate_disposition,
        created_by_login_key=created_by_login_key,
        duplicate_resolution_mode=duplicate_resolution_mode,
    )


def test_create_and_get_round_trip(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session()
    try:
        repository.create(session)

        loaded = repository.get(session.id)
    finally:
        engine.dispose()

    assert loaded == session
    assert loaded.duplicate_disposition is None


@pytest.mark.parametrize(
    ("disposition", "target_state"),
    [
        (UploadDuplicateDisposition.KEEP_SEPARATE, UploadSessionState.PUBLISH_PENDING),
        (UploadDuplicateDisposition.DISCARD, UploadSessionState.CANCELLED),
    ],
)
def test_record_duplicate_disposition_atomically_persists_state_and_provenance(
    tmp_path: Path,
    disposition: UploadDuplicateDisposition,
    target_state: UploadSessionState,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.DUPLICATE_PENDING)
    try:
        repository.create(session)
        resolved = repository.record_duplicate_disposition(
            session.id,
            expected_version=0,
            disposition=disposition,
            updated_at_ms=20,
        )
        reconstructed = SqliteUploadSessionRepository(engine).get(session.id)
    finally:
        engine.dispose()

    assert resolved.state is target_state
    assert resolved.duplicate_disposition is disposition
    assert resolved.version == 1
    assert resolved.updated_at_ms == 20
    assert reconstructed == resolved


def test_duplicate_disposition_update_rolls_back_state_and_provenance_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kronika.infrastructure.persistence.upload_session_repository as module

    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.DUPLICATE_PENDING)
    original_require_session = module._require_session

    def fail_after_update(connection, session_id):
        loaded = original_require_session(connection, session_id)
        assert loaded.state is UploadSessionState.PUBLISH_PENDING
        assert (
            loaded.duplicate_disposition
            is UploadDuplicateDisposition.KEEP_SEPARATE
        )
        raise RuntimeError("synthetic post-update failure")

    try:
        repository.create(session)
        monkeypatch.setattr(module, "_require_session", fail_after_update)
        with pytest.raises(RuntimeError):
            repository.record_duplicate_disposition(
                session.id,
                expected_version=0,
                disposition=UploadDuplicateDisposition.KEEP_SEPARATE,
                updated_at_ms=20,
            )
        monkeypatch.setattr(module, "_require_session", original_require_session)
        stored = repository.get(session.id)
    finally:
        engine.dispose()

    assert stored == session
    assert stored.state is UploadSessionState.DUPLICATE_PENDING
    assert stored.duplicate_disposition is None


def test_generic_state_transition_cannot_publish_kept_duplicate(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.DUPLICATE_PENDING)
    try:
        repository.create(session)
        kept = repository.record_duplicate_disposition(
            session.id,
            expected_version=0,
            disposition=UploadDuplicateDisposition.KEEP_SEPARATE,
            updated_at_ms=20,
        )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.transition_state(
                kept.id,
                expected_state=UploadSessionState.PUBLISH_PENDING,
                target_state=UploadSessionState.PUBLISHED,
                expected_version=kept.version,
                updated_at_ms=21,
            )
        stored = repository.get(kept.id)
    finally:
        engine.dispose()

    assert kept.duplicate_disposition is UploadDuplicateDisposition.KEEP_SEPARATE
    assert stored == kept


def test_invalid_persisted_duplicate_disposition_fails_safely(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.PUBLISH_PENDING)
    try:
        repository.create(session)
        with engine.begin() as connection:
            connection.execute(sa.text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                sa.text(
                    "UPDATE upload_sessions SET duplicate_disposition = 'unknown' "
                    "WHERE id = :id"
                ),
                {"id": session.id.to_string()},
            )
            connection.execute(sa.text("PRAGMA ignore_check_constraints=OFF"))
        with pytest.raises(FrameNestUploadSessionRepositoryError) as error:
            repository.get(session.id)
    finally:
        engine.dispose()

    assert str(error.value) == "Upload session operation failed."


def test_create_rejects_duplicate_session_id_and_storage_key(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session()
    try:
        repository.create(session)
        with pytest.raises(UploadSessionAlreadyExistsError):
            repository.create(_session(session_id=session.id, storage_key="upload-session-0002"))
        with pytest.raises(UploadStorageKeyAlreadyExistsError):
            repository.create(_session(storage_key=session.storage_key.value))
    finally:
        engine.dispose()


def test_advance_received_offset_is_exact_guarded_and_versioned(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVING)
    try:
        repository.create(session)

        advanced = repository.advance_received_offset(
            session.id,
            expected_received_size_bytes=0,
            accepted_size_bytes=25,
            expected_version=0,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert advanced.received_size_bytes == 25
    assert advanced.version == 1
    assert advanced.updated_at_ms == 20
    assert advanced.state == UploadSessionState.RECEIVING


def test_advance_received_offset_distinguishes_guard_failures(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    missing_id = UploadSessionId.new()
    wrong_state = _session(storage_key="upload-session-0002", state=UploadSessionState.CREATED)
    receiving = _session(
        storage_key="upload-session-0003",
        state=UploadSessionState.RECEIVING,
        received_size_bytes=10,
        declared_size_bytes=20,
        version=2,
    )
    try:
        repository.create(wrong_state)
        repository.create(receiving)

        with pytest.raises(UploadSessionNotFoundError):
            repository.advance_received_offset(
                missing_id,
                expected_received_size_bytes=0,
                accepted_size_bytes=1,
                expected_version=0,
                updated_at_ms=11,
            )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.advance_received_offset(
                wrong_state.id,
                expected_received_size_bytes=0,
                accepted_size_bytes=1,
                expected_version=0,
                updated_at_ms=11,
            )
        with pytest.raises(UploadOffsetConflictError):
            repository.advance_received_offset(
                receiving.id,
                expected_received_size_bytes=9,
                accepted_size_bytes=1,
                expected_version=2,
                updated_at_ms=11,
            )
        with pytest.raises(UploadSessionConcurrencyConflictError):
            repository.advance_received_offset(
                receiving.id,
                expected_received_size_bytes=10,
                accepted_size_bytes=1,
                expected_version=1,
                updated_at_ms=11,
            )
        with pytest.raises(UploadSizeLimitExceededError):
            repository.advance_received_offset(
                receiving.id,
                expected_received_size_bytes=10,
                accepted_size_bytes=11,
                expected_version=2,
                updated_at_ms=11,
            )
    finally:
        engine.dispose()


def test_failed_guarded_offset_update_rolls_back(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVING, received_size_bytes=10, version=2)
    try:
        repository.create(session)
        with pytest.raises(UploadSessionConcurrencyConflictError):
            repository.advance_received_offset(
                session.id,
                expected_received_size_bytes=10,
                accepted_size_bytes=5,
                expected_version=99,
                updated_at_ms=30,
            )

        loaded = repository.get(session.id)
    finally:
        engine.dispose()

    assert loaded == session


def test_transition_state_persists_and_rejects_invalid_or_terminal_paths(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVING, received_size_bytes=100)
    try:
        repository.create(session)
        received = repository.transition_state(
            session.id,
            expected_state=UploadSessionState.RECEIVING,
            target_state=UploadSessionState.RECEIVED,
            expected_version=0,
            updated_at_ms=20,
        )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.transition_state(
                session.id,
                expected_state=UploadSessionState.RECEIVED,
                target_state=UploadSessionState.PUBLISHED,
                expected_version=1,
                updated_at_ms=21,
            )
        published = _create_at_state(
            repository,
            storage_key="upload-session-0004",
            state=UploadSessionState.PUBLISHED,
        )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.transition_state(
                published.id,
                expected_state=UploadSessionState.PUBLISHED,
                target_state=UploadSessionState.CATALOGED,
                expected_version=0,
                updated_at_ms=22,
            )
        stored_published = repository.get(published.id)
    finally:
        engine.dispose()

    assert received.state == UploadSessionState.RECEIVED
    assert received.version == 1
    assert stored_published == published


def test_transition_state_distinguishes_not_found_wrong_state_and_stale_version(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.CREATED)
    try:
        repository.create(session)
        with pytest.raises(UploadSessionNotFoundError):
            repository.transition_state(
                UploadSessionId.new(),
                expected_state=UploadSessionState.CREATED,
                target_state=UploadSessionState.RECEIVING,
                expected_version=0,
                updated_at_ms=20,
            )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.transition_state(
                session.id,
                expected_state=UploadSessionState.RECEIVING,
                target_state=UploadSessionState.RECEIVED,
                expected_version=0,
                updated_at_ms=20,
            )
        with pytest.raises(UploadSessionConcurrencyConflictError):
            repository.transition_state(
                session.id,
                expected_state=UploadSessionState.CREATED,
                target_state=UploadSessionState.RECEIVING,
                expected_version=99,
                updated_at_ms=20,
            )
    finally:
        engine.dispose()


def test_incomplete_receiving_to_received_fails_without_mutating_row(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVING, received_size_bytes=25, version=2)
    try:
        repository.create(session)
        with pytest.raises(IncompleteUploadSessionError) as error:
            repository.transition_state(
                session.id,
                expected_state=UploadSessionState.RECEIVING,
                target_state=UploadSessionState.RECEIVED,
                expected_version=2,
                updated_at_ms=99,
            )

        loaded = repository.get(session.id)
    finally:
        engine.dispose()

    assert loaded == session
    assert str(error.value) == "incomplete upload session"


def test_complete_receiving_to_received_succeeds(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVING, received_size_bytes=100)
    try:
        repository.create(session)
        received = repository.transition_state(
            session.id,
            expected_state=UploadSessionState.RECEIVING,
            target_state=UploadSessionState.RECEIVED,
            expected_version=0,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert received.state == UploadSessionState.RECEIVED
    assert received.received_size_bytes == received.declared_size_bytes
    assert received.version == 1


def test_stale_version_takes_precedence_over_incomplete_upload(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVING, received_size_bytes=25, version=2)
    try:
        repository.create(session)
        with pytest.raises(UploadSessionConcurrencyConflictError):
            repository.transition_state(
                session.id,
                expected_state=UploadSessionState.RECEIVING,
                target_state=UploadSessionState.RECEIVED,
                expected_version=1,
                updated_at_ms=99,
            )
    finally:
        engine.dispose()


def test_wrong_state_takes_precedence_over_incomplete_upload(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.CANCELLED, received_size_bytes=25)
    try:
        repository.create(session)
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.transition_state(
                session.id,
                expected_state=UploadSessionState.RECEIVING,
                target_state=UploadSessionState.RECEIVED,
                expected_version=0,
                updated_at_ms=99,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (UploadSessionState.RECEIVED, UploadSessionState.VALIDATING),
        (UploadSessionState.VALIDATING, UploadSessionState.DUPLICATE_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.PUBLISH_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.REJECTED),
        (UploadSessionState.DUPLICATE_PENDING, UploadSessionState.PUBLISH_PENDING),
    ],
)
def test_later_complete_target_transitions_reject_incomplete_persisted_rows(
    tmp_path: Path,
    source: UploadSessionState,
    target: UploadSessionState,
) -> None:
    repository, engine = _repository(tmp_path)
    session_id = UploadSessionId.new()
    try:
        _insert_raw_upload_session(
            engine,
            session_id=session_id,
            storage_key=f"upload-session-{source.value.replace('_', '-')}",
            state=source,
            declared_size_bytes=100,
            received_size_bytes=25,
            version=0,
        )
        with pytest.raises(IncompleteUploadSessionError):
            repository.transition_state(
                session_id,
                expected_state=source,
                target_state=target,
                expected_version=0,
                updated_at_ms=99,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (UploadSessionState.RECEIVED, UploadSessionState.VALIDATING),
        (UploadSessionState.VALIDATING, UploadSessionState.DUPLICATE_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.PUBLISH_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.REJECTED),
        (UploadSessionState.DUPLICATE_PENDING, UploadSessionState.PUBLISH_PENDING),
    ],
)
def test_valid_complete_rows_continue_through_transition_graph(
    tmp_path: Path,
    source: UploadSessionState,
    target: UploadSessionState,
) -> None:
    repository, engine = _repository(tmp_path)
    evidence_kwargs: dict[str, object] = {}
    if target in VALIDATED_UPLOAD_SESSION_STATES:
        evidence_kwargs = {
            "checksum_hex": "a" * 64,
            "validated_media_kind": UploadValidatedMediaKind.VIDEO,
            "validated_format": UploadValidatedFormat.MP4,
            "byte_identity_id": MediaByteIdentityId.new(),
        }
    session = _session(
        storage_key=f"upload-session-valid-{source.value.replace('_', '-')}",
        state=source,
        received_size_bytes=100,
        **evidence_kwargs,
    )
    try:
        repository.create(session)
        transitioned = repository.transition_state(
            session.id,
            expected_state=source,
            target_state=target,
            expected_version=0,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert transitioned.state == target
    assert transitioned.received_size_bytes == transitioned.declared_size_bytes
    assert transitioned.version == 1


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (UploadSessionState.VALIDATING, UploadSessionState.DUPLICATE_PENDING),
        (UploadSessionState.VALIDATING, UploadSessionState.PUBLISH_PENDING),
    ],
)
def test_transition_state_rejects_advanced_targets_without_validation_evidence(
    tmp_path: Path,
    source: UploadSessionState,
    target: UploadSessionState,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(
        storage_key=f"upload-session-missing-evidence-{target.value.replace('_', '-')}",
        state=source,
        received_size_bytes=100,
    )
    try:
        repository.create(session)
        with pytest.raises(InvalidUploadValidationEvidenceError):
            repository.transition_state(
                session.id,
                expected_state=source,
                target_state=target,
                expected_version=0,
                updated_at_ms=20,
            )
    finally:
        engine.dispose()


def test_record_completed_checksum_is_guarded_and_idempotent_for_same_digest(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVED)
    digest = "a" * 64
    try:
        repository.create(session)
        recorded = repository.record_completed_checksum(
            session.id,
            expected_state=UploadSessionState.RECEIVED,
            expected_version=0,
            checksum_hex=digest,
            updated_at_ms=20,
        )
        repeated = repository.record_completed_checksum(
            session.id,
            expected_state=UploadSessionState.RECEIVED,
            expected_version=1,
            checksum_hex=digest,
            updated_at_ms=21,
        )
        with pytest.raises(InvalidUploadChecksumError):
            repository.record_completed_checksum(
                session.id,
                expected_state=UploadSessionState.RECEIVED,
                expected_version=1,
                checksum_hex="b" * 64,
                updated_at_ms=22,
            )
    finally:
        engine.dispose()

    assert recorded.checksum_algorithm == "sha256"
    assert recorded.checksum_hex == digest
    assert recorded.version == 1
    assert repeated == recorded


def test_record_completed_checksum_rejects_invalid_state_version_and_digest(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVED)
    try:
        repository.create(session)
        with pytest.raises(InvalidUploadChecksumError):
            repository.record_completed_checksum(
                session.id,
                expected_state=UploadSessionState.RECEIVED,
                expected_version=0,
                checksum_hex="A" * 64,
                updated_at_ms=20,
            )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.record_completed_checksum(
                session.id,
                expected_state=UploadSessionState.VALIDATING,
                expected_version=0,
                checksum_hex="a" * 64,
                updated_at_ms=20,
            )
        with pytest.raises(UploadSessionConcurrencyConflictError):
            repository.record_completed_checksum(
                session.id,
                expected_state=UploadSessionState.RECEIVED,
                expected_version=99,
                checksum_hex="a" * 64,
                updated_at_ms=20,
            )
    finally:
        engine.dispose()


def test_start_validation_transitions_received_to_validating(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.RECEIVED)
    try:
        repository.create(session)
        validating = repository.start_validation(
            session.id,
            expected_version=0,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert validating.state is UploadSessionState.VALIDATING
    assert validating.version == 1


def test_list_startup_validation_candidates_is_bounded_ordered_and_read_only(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    received_old = _session(
        storage_key="upload-session-candidate-0001",
        state=UploadSessionState.RECEIVED,
    )
    validating = _session(
        storage_key="upload-session-candidate-0002",
        state=UploadSessionState.VALIDATING,
    )
    received_new = _session(
        storage_key="upload-session-candidate-0003",
        state=UploadSessionState.RECEIVED,
    )
    excluded_states = [
        UploadSessionState.CREATED,
        UploadSessionState.RECEIVING,
        UploadSessionState.DUPLICATE_PENDING,
        UploadSessionState.PUBLISH_PENDING,
        UploadSessionState.PUBLISHED,
        UploadSessionState.CATALOGED,
        UploadSessionState.REJECTED,
        UploadSessionState.CANCELLED,
        UploadSessionState.EXPIRED,
        UploadSessionState.FAILED,
    ]
    try:
        repository.create(received_new)
        repository.create(validating)
        repository.create(received_old)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE upload_sessions SET updated_at_ms = :updated_at_ms "
                    "WHERE id = :id"
                ),
                {"id": received_old.id.to_string(), "updated_at_ms": 10},
            )
            connection.execute(
                sa.text(
                    "UPDATE upload_sessions SET updated_at_ms = :updated_at_ms "
                    "WHERE id = :id"
                ),
                {"id": validating.id.to_string(), "updated_at_ms": 20},
            )
            connection.execute(
                sa.text(
                    "UPDATE upload_sessions SET updated_at_ms = :updated_at_ms "
                    "WHERE id = :id"
                ),
                {"id": received_new.id.to_string(), "updated_at_ms": 30},
            )
        for index, state in enumerate(excluded_states, start=4):
            repository.create(
                _session(
                    storage_key=f"upload-session-candidate-{index:04d}",
                    state=state,
                    received_size_bytes=0
                    if state is UploadSessionState.CREATED
                    else 100,
                )
            )

        before = {
            session.id: repository.get(session.id)
            for session in (received_old, validating, received_new)
        }
        first_two = repository.list_startup_validation_candidates(limit=2)
        all_candidates = repository.list_startup_validation_candidates(limit=10)
        after_first = repository.list_startup_validation_candidates(
            limit=10,
            after_updated_at_ms=10,
            after_id=received_old.id.to_string(),
        )
        after = {
            session.id: repository.get(session.id)
            for session in (received_old, validating, received_new)
        }
    finally:
        engine.dispose()

    assert [candidate.id for candidate in first_two] == [received_old.id, validating.id]
    assert [candidate.id for candidate in all_candidates] == [
        received_old.id,
        validating.id,
        received_new.id,
    ]
    assert [candidate.id for candidate in after_first] == [
        validating.id,
        received_new.id,
    ]
    assert after == before


def test_list_runtime_validation_candidates_excludes_validating_and_uses_cursor(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    received_old = _session(
        storage_key="upload-session-candidate-0001",
        state=UploadSessionState.RECEIVED,
    )
    validating = _session(
        storage_key="upload-session-candidate-0002",
        state=UploadSessionState.VALIDATING,
    )
    received_new = _session(
        storage_key="upload-session-candidate-0003",
        state=UploadSessionState.RECEIVED,
    )
    failed = _session(
        storage_key="upload-session-candidate-0004",
        state=UploadSessionState.FAILED,
    )
    try:
        repository.create(received_new)
        repository.create(validating)
        repository.create(failed)
        repository.create(received_old)
        with engine.begin() as connection:
            for session, updated_at_ms in (
                (received_old, 10),
                (validating, 20),
                (received_new, 30),
                (failed, 40),
            ):
                connection.execute(
                    sa.text(
                        "UPDATE upload_sessions SET updated_at_ms = :updated_at_ms "
                        "WHERE id = :id"
                    ),
                    {"id": session.id.to_string(), "updated_at_ms": updated_at_ms},
                )

        first = repository.list_runtime_validation_candidates(limit=1)
        after_first = repository.list_runtime_validation_candidates(
            limit=10,
            after_updated_at_ms=10,
            after_id=received_old.id.to_string(),
        )
    finally:
        engine.dispose()

    assert [candidate.id for candidate in first] == [received_old.id]
    assert [candidate.id for candidate in after_first] == [received_new.id]


def test_complete_validation_success_commits_evidence_and_publish_state_atomically(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.VALIDATING)
    digest = "b" * 64
    try:
        repository.create(session)
        completed = repository.complete_validation_success(
            session.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.ANIMATED_IMAGE,
            validated_format=UploadValidatedFormat.GIF,
            updated_at_ms=20,
        )
        repeated = repository.complete_validation_success(
            session.id,
            expected_version=1,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.ANIMATED_IMAGE,
            validated_format=UploadValidatedFormat.GIF,
            updated_at_ms=21,
        )
        with pytest.raises(InvalidUploadValidationEvidenceError):
            repository.complete_validation_success(
                session.id,
                expected_version=1,
                checksum_hex=digest,
                validated_media_kind=UploadValidatedMediaKind.VIDEO,
                validated_format=UploadValidatedFormat.MP4,
                updated_at_ms=22,
            )
    finally:
        engine.dispose()

    assert completed.state is UploadSessionState.PUBLISH_PENDING
    assert completed.checksum_algorithm == "sha256"
    assert completed.checksum_hex == digest
    assert completed.validated_media_kind is UploadValidatedMediaKind.ANIMATED_IMAGE
    assert completed.validated_format is UploadValidatedFormat.GIF
    assert completed.byte_identity_id is not None
    assert repeated == completed
    with engine.connect() as connection:
        identity_row = connection.execute(
            sa.select(media_byte_identities).where(
                media_byte_identities.c.id == completed.byte_identity_id.to_string()
            )
        ).mappings().one()
    assert identity_row["checksum_algorithm"] == "sha256"
    assert identity_row["size_bytes"] == completed.declared_size_bytes
    assert identity_row["checksum_hex"] == digest


def test_reject_validation_commits_sanitized_code_and_rejected_state(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.VALIDATING)
    try:
        repository.create(session)
        rejected = repository.reject_validation(
            session.id,
            expected_version=0,
            failure_code="UNSUPPORTED_MEDIA_TYPE",
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert rejected.state is UploadSessionState.REJECTED
    assert rejected.failure_code == "UNSUPPORTED_MEDIA_TYPE"
    assert rejected.checksum_hex is None
    assert rejected.validated_media_kind is None
    assert rejected.byte_identity_id is None


def test_get_or_create_byte_identity_reuses_existing_and_separates_tuple_parts(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    identity = MediaByteIdentity(
        id=MediaByteIdentityId.new(),
        checksum_algorithm="sha256",
        size_bytes=100,
        checksum_hex="a" * 64,
        created_at_ms=10,
    )
    same_tuple = MediaByteIdentity(
        id=MediaByteIdentityId.new(),
        checksum_algorithm="sha256",
        size_bytes=100,
        checksum_hex="a" * 64,
        created_at_ms=20,
    )
    different_digest = MediaByteIdentity(
        id=MediaByteIdentityId.new(),
        checksum_algorithm="sha256",
        size_bytes=100,
        checksum_hex="b" * 64,
        created_at_ms=30,
    )
    different_size = MediaByteIdentity(
        id=MediaByteIdentityId.new(),
        checksum_algorithm="sha256",
        size_bytes=101,
        checksum_hex="a" * 64,
        created_at_ms=40,
    )
    try:
        created = repository.get_or_create_byte_identity(identity)
        reused = repository.get_or_create_byte_identity(same_tuple)
        digest_identity = repository.get_or_create_byte_identity(different_digest)
        size_identity = repository.get_or_create_byte_identity(different_size)
        with engine.connect() as connection:
            count = connection.execute(
                sa.select(sa.func.count()).select_from(media_byte_identities)
            )
            identity_count = count.scalar_one()
    finally:
        engine.dispose()

    assert reused == created
    assert digest_identity.id != created.id
    assert size_identity.id != created.id
    assert identity_count == 3


def test_idempotent_success_rejects_mismatched_byte_identity_link(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.VALIDATING)
    digest = "f" * 64
    try:
        repository.create(session)
        completed = repository.complete_validation_success(
            session.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
        mismatched = repository.get_or_create_byte_identity(
            MediaByteIdentity(
                id=MediaByteIdentityId.new(),
                checksum_algorithm="sha256",
                size_bytes=completed.declared_size_bytes,
                checksum_hex="0" * 64,
                created_at_ms=21,
            )
        )
        with engine.begin() as connection:
            connection.execute(
                sa.update(upload_sessions)
                .where(upload_sessions.c.id == completed.id.to_string())
                .values(byte_identity_id=mismatched.id.to_string())
            )
        with pytest.raises(InvalidUploadValidationEvidenceError):
            repository.complete_validation_success(
                session.id,
                expected_version=1,
                checksum_hex=digest,
                validated_media_kind=UploadValidatedMediaKind.VIDEO,
                validated_format=UploadValidatedFormat.MP4,
                updated_at_ms=22,
            )
    finally:
        engine.dispose()


def test_two_successful_identical_uploads_link_to_one_byte_identity(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    first = _session(state=UploadSessionState.VALIDATING, storage_key="upload-session-0100")
    second = _session(state=UploadSessionState.VALIDATING, storage_key="upload-session-0101")
    digest = "e" * 64
    try:
        repository.create(first)
        repository.create(second)
        first_completed = repository.complete_validation_success(
            first.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
        second_completed = repository.complete_validation_success(
            second.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=21,
        )
        with engine.connect() as connection:
            identity_count = connection.execute(
                sa.select(sa.func.count()).select_from(media_byte_identities)
            ).scalar_one()
    finally:
        engine.dispose()

    assert first_completed.state is UploadSessionState.PUBLISH_PENDING
    assert second_completed.state is UploadSessionState.DUPLICATE_PENDING
    assert first_completed.duplicate_disposition is None
    assert second_completed.duplicate_disposition is None
    assert first_completed.byte_identity_id == second_completed.byte_identity_id
    assert identity_count == 1


def test_silent_keep_separate_mode_atomically_avoids_duplicate_pending(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    first = _session(
        state=UploadSessionState.VALIDATING,
        storage_key="upload-session-silent-a001",
        created_by_login_key="admin@example.com",
        duplicate_resolution_mode=UploadDuplicateResolutionMode.EXPLICIT,
    )
    second = _session(
        state=UploadSessionState.VALIDATING,
        storage_key="upload-session-silent-b001",
        created_by_login_key="user@example.com",
        duplicate_resolution_mode=UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE,
    )
    digest = "f" * 64
    try:
        repository.create(first)
        repository.create(second)
        first_completed = repository.complete_validation_success(
            first.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
        second_completed = repository.complete_validation_success(
            second.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=21,
        )
        reloaded = repository.get(second.id)
        repeated = repository.complete_validation_success(
            second.id,
            expected_version=second_completed.version,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=22,
        )
    finally:
        engine.dispose()

    assert first_completed.state is UploadSessionState.PUBLISH_PENDING
    assert second_completed.state is UploadSessionState.PUBLISH_PENDING
    assert second_completed.duplicate_disposition is UploadDuplicateDisposition.KEEP_SEPARATE
    assert second_completed.state is not UploadSessionState.DUPLICATE_PENDING
    assert reloaded == second_completed
    assert repeated == second_completed
    assert repeated.created_by_login_key == "user@example.com"
    assert (
        repeated.duplicate_resolution_mode
        is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
    )


def test_ownership_fields_round_trip_through_repository(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(
        created_by_login_key="owner@example.com",
        duplicate_resolution_mode=UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE,
    )
    try:
        repository.create(session)
        loaded = repository.get(session.id)
    finally:
        engine.dispose()

    assert loaded is not None
    assert loaded.created_by_login_key == "owner@example.com"
    assert (
        loaded.duplicate_resolution_mode
        is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
    )


def test_validation_completion_never_uses_the_current_session_as_its_own_duplicate(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    session = _session(
        storage_key="upload-session-self-duplicate-0001",
        state=UploadSessionState.VALIDATING,
    )
    try:
        repository.create(session)
        completed = repository.complete_validation_success(
            session.id,
            expected_version=0,
            checksum_hex="3" * 64,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
        repeated = repository.complete_validation_success(
            session.id,
            expected_version=completed.version,
            checksum_hex="3" * 64,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=21,
        )
    finally:
        engine.dispose()

    assert completed.state is UploadSessionState.PUBLISH_PENDING
    assert repeated == completed


@pytest.mark.parametrize(
    "qualifying_state",
    (
        UploadSessionState.PUBLISH_PENDING,
        UploadSessionState.PUBLISHED,
        UploadSessionState.CATALOGED,
    ),
)
def test_each_approved_canonical_state_gates_a_later_exact_upload(
    tmp_path: Path,
    qualifying_state: UploadSessionState,
) -> None:
    repository, engine = _repository(tmp_path)
    identity_id = MediaByteIdentityId.new()
    digest = "2" * 64
    prior = _session(
        storage_key="upload-session-qualifying-0001",
        state=qualifying_state,
        received_size_bytes=100,
        checksum_hex=digest,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=identity_id,
    )
    current = _session(
        storage_key="upload-session-qualifying-0002",
        state=UploadSessionState.VALIDATING,
    )
    try:
        repository.create(prior)
        if qualifying_state is UploadSessionState.CATALOGED:
            _seed_live_upload_publication_link(
                engine,
                upload_id=prior.id.to_string(),
                byte_identity_id=identity_id.to_string(),
                checksum_hex=digest,
            )
        repository.create(current)
        completed = repository.complete_validation_success(
            current.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.ANIMATED_IMAGE,
            validated_format=UploadValidatedFormat.GIF,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert completed.state is UploadSessionState.DUPLICATE_PENDING
    assert completed.byte_identity_id == identity_id


def test_cataloged_without_live_publication_link_does_not_gate_exact_upload(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    identity_id = MediaByteIdentityId.new()
    digest = "3" * 64
    prior = _session(
        storage_key="upload-session-detached-0001",
        state=UploadSessionState.CATALOGED,
        received_size_bytes=100,
        checksum_hex=digest,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=identity_id,
    )
    current = _session(
        storage_key="upload-session-detached-0002",
        state=UploadSessionState.VALIDATING,
    )
    try:
        repository.create(prior)
        repository.create(current)
        completed = repository.complete_validation_success(
            current.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert completed.state is UploadSessionState.PUBLISH_PENDING
    assert completed.byte_identity_id == identity_id


def _seed_live_upload_publication_link(
    engine: sa.Engine,
    *,
    upload_id: str,
    byte_identity_id: str,
    checksum_hex: str,
) -> None:
    device_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    library_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    media_id = "11111111-1111-4111-8111-111111111111"
    location_id = "22222222-2222-4222-8222-222222222222"
    publication_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO devices (id, display_name) VALUES (:id, 'Synthetic')"
            ),
            {"id": device_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO libraries "
                "(id, device_id, display_name, path_flavor, root_path) "
                "VALUES (:id, :device_id, 'Synthetic', 'posix', '/synthetic')"
            ),
            {"id": library_id, "device_id": device_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO logical_media "
                "(id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (:id, 'video', 1, 1)"
            ),
            {"id": media_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO physical_media_locations "
                "(id, media_id, library_id, relative_path, availability, "
                "observed_size_bytes, observed_mtime_ns, created_at_ms, "
                "updated_at_ms) VALUES "
                "(:id, :media_id, :library_id, 'safe/item.mp4', 'available', "
                "100, 1, 1, 1)"
            ),
            {
                "id": location_id,
                "media_id": media_id,
                "library_id": library_id,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO upload_publications "
                "(upload_id, publication_id, destination_id, relative_target, "
                "byte_identity_id, expected_size_bytes, checksum_algorithm, "
                "checksum_hex, validated_media_kind, validated_format, state, "
                "cleanup_state, created_at_ms, updated_at_ms, verified_at_ms, "
                "cleanup_completed_at_ms, version, media_id, media_location_id) "
                "VALUES (:upload_id, :publication_id, :destination_id, :relative, "
                ":byte_id, 100, 'sha256', :digest, 'video', 'mp4', 'verified', "
                "'complete', 1, 1, 1, 1, 1, :media_id, :location_id)"
            ),
            {
                "upload_id": upload_id,
                "publication_id": publication_id,
                "destination_id": library_id,
                "relative": f"{publication_id.replace('-', '')}.mp4",
                "byte_id": byte_identity_id,
                "digest": checksum_hex,
                "media_id": media_id,
                "location_id": location_id,
            },
        )


@pytest.mark.parametrize(
    "nonqualifying_state",
    (
        UploadSessionState.REJECTED,
        UploadSessionState.CANCELLED,
        UploadSessionState.EXPIRED,
        UploadSessionState.FAILED,
    ),
)
def test_terminal_identity_does_not_block_later_valid_upload(
    tmp_path: Path,
    nonqualifying_state: UploadSessionState,
) -> None:
    repository, engine = _repository(tmp_path)
    identity_id = MediaByteIdentityId.new()
    digest = "4" * 64
    prior = _session(
        storage_key="upload-session-terminal-0001",
        state=nonqualifying_state,
        received_size_bytes=100,
        checksum_hex=digest,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=identity_id,
    )
    current = _session(
        storage_key="upload-session-terminal-0002",
        state=UploadSessionState.VALIDATING,
    )
    try:
        repository.create(prior)
        repository.create(current)
        completed = repository.complete_validation_success(
            current.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert completed.state is UploadSessionState.PUBLISH_PENDING
    assert completed.byte_identity_id == identity_id


def test_duplicate_pending_identity_is_not_a_canonical_candidate(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    identity_id = MediaByteIdentityId.new()
    digest = "5" * 64
    prior_duplicate = _session(
        storage_key="upload-session-duplicate-0001",
        state=UploadSessionState.DUPLICATE_PENDING,
        checksum_hex=digest,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=identity_id,
    )
    current = _session(
        storage_key="upload-session-duplicate-0002",
        state=UploadSessionState.VALIDATING,
    )
    try:
        repository.create(prior_duplicate)
        repository.create(current)
        completed = repository.complete_validation_success(
            current.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
    finally:
        engine.dispose()

    assert completed.state is UploadSessionState.PUBLISH_PENDING
    assert completed.byte_identity_id == identity_id


def test_duplicate_classification_ignores_filename_and_preserves_committed_outcome(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    digest = "6" * 64
    first = _session(
        storage_key="upload-session-filename-0001",
        state=UploadSessionState.VALIDATING,
        display_filename="first-name.mp4",
    )
    second = _session(
        storage_key="upload-session-filename-0002",
        state=UploadSessionState.VALIDATING,
        display_filename="unrelated-name.gif",
    )
    try:
        repository.create(first)
        repository.create(second)
        first_result = repository.complete_validation_success(
            first.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20,
        )
        second_result = repository.complete_validation_success(
            second.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=21,
        )
        repeated = repository.complete_validation_success(
            second.id,
            expected_version=second_result.version,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=22,
        )
    finally:
        engine.dispose()

    assert first_result.state is UploadSessionState.PUBLISH_PENDING
    assert second_result.state is UploadSessionState.DUPLICATE_PENDING
    assert repeated == second_result


def test_concurrent_identical_validation_serializes_before_canonical_lookup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent-validation.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    setup_engine = create_sqlite_engine(database_path)
    setup_repository = SqliteUploadSessionRepository(setup_engine)
    first = _session(
        storage_key="upload-session-race-0001",
        state=UploadSessionState.VALIDATING,
    )
    second = _session(
        storage_key="upload-session-race-0002",
        state=UploadSessionState.VALIDATING,
    )
    setup_repository.create(first)
    setup_repository.create(second)
    setup_engine.dispose()

    engines = [
        create_sqlite_engine(database_path, busy_timeout_seconds=5.0),
        create_sqlite_engine(database_path, busy_timeout_seconds=5.0),
    ]
    begin_attempted = [Event(), Event()]
    for engine, attempted in zip(engines, begin_attempted, strict=True):
        def mark_begin(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
            *,
            marker=attempted,
        ) -> None:
            if statement.strip().upper() == "BEGIN IMMEDIATE":
                marker.set()

        sa.event.listen(engine, "before_cursor_execute", mark_begin)

    start = Barrier(2)
    digest = "7" * 64

    def worker(index: int, session: UploadSession) -> UploadSession:
        repository = SqliteUploadSessionRepository(engines[index])
        start.wait(timeout=5)
        return repository.complete_validation_success(
            session.id,
            expected_version=0,
            checksum_hex=digest,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            updated_at_ms=20 + index,
        )

    blocker = sqlite3.connect(database_path, isolation_level=None)
    blocker.execute("PRAGMA busy_timeout = 5000")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(worker, 0, first),
                executor.submit(worker, 1, second),
            ]
            assert begin_attempted[0].wait(timeout=5)
            assert begin_attempted[1].wait(timeout=5)
            blocker.commit()
            results = [future.result(timeout=5) for future in futures]
    finally:
        if blocker.in_transaction:
            blocker.rollback()
        blocker.close()
        for engine in engines:
            engine.dispose()

    verify_engine = create_sqlite_engine(database_path)
    try:
        with verify_engine.connect() as connection:
            identity_count = connection.execute(
                sa.select(sa.func.count()).select_from(media_byte_identities)
            ).scalar_one()
    finally:
        verify_engine.dispose()

    assert sorted(result.state.value for result in results) == [
        UploadSessionState.DUPLICATE_PENDING.value,
        UploadSessionState.PUBLISH_PENDING.value,
    ]
    assert results[0].byte_identity_id == results[1].byte_identity_id
    assert identity_count == 1


def test_concurrent_get_or_create_byte_identity_converges_on_one_row(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))

    def worker(created_at_ms: int) -> MediaByteIdentity:
        engine = create_sqlite_engine(database_path, busy_timeout_seconds=5.0)
        repository = SqliteUploadSessionRepository(engine)
        try:
            return repository.get_or_create_byte_identity(
                MediaByteIdentity(
                    id=MediaByteIdentityId.new(),
                    checksum_algorithm="sha256",
                    size_bytes=100,
                    checksum_hex="c" * 64,
                    created_at_ms=created_at_ms,
                )
            )
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(worker, (10, 20))

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                sa.select(sa.func.count()).select_from(media_byte_identities)
            )
            identity_count = count.scalar_one()
    finally:
        engine.dispose()

    assert first == second
    assert identity_count == 1


def test_validation_success_failure_rolls_back_new_identity_and_upload_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kronika.infrastructure.persistence.upload_session_repository as module

    repository, engine = _repository(tmp_path)
    session = _session(state=UploadSessionState.VALIDATING)
    digest = "d" * 64
    original_require_session = module._require_session

    def fail_after_update(connection, session_id):
        loaded = original_require_session(connection, session_id)
        assert loaded.state is UploadSessionState.PUBLISH_PENDING
        raise RuntimeError("synthetic post-update failure")

    try:
        repository.create(session)
        monkeypatch.setattr(module, "_require_session", fail_after_update)
        with pytest.raises(RuntimeError):
            repository.complete_validation_success(
                session.id,
                expected_version=0,
                checksum_hex=digest,
                validated_media_kind=UploadValidatedMediaKind.VIDEO,
                validated_format=UploadValidatedFormat.MP4,
                updated_at_ms=20,
            )
        monkeypatch.setattr(module, "_require_session", original_require_session)
        stored = repository.get(session.id)
        with engine.connect() as connection:
            identity_count = connection.execute(
                sa.select(sa.func.count()).select_from(media_byte_identities)
            ).scalar_one()
            raw_row = connection.execute(
                sa.select(upload_sessions).where(upload_sessions.c.id == session.id.to_string())
            ).mappings().one()
    finally:
        engine.dispose()

    assert stored == session
    assert raw_row["byte_identity_id"] is None
    assert identity_count == 0


def test_failure_code_is_sanitized_and_only_allowed_for_failure_states(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    failed = _session(storage_key="upload-session-0005", state=UploadSessionState.RECEIVING)
    invalid = _session(storage_key="upload-session-0006", state=UploadSessionState.CREATED)
    try:
        repository.create(failed)
        repository.create(invalid)
        stored = repository.transition_state(
            failed.id,
            expected_state=UploadSessionState.RECEIVING,
            target_state=UploadSessionState.FAILED,
            expected_version=0,
            updated_at_ms=20,
            failure_code="NETWORK_TIMEOUT",
        )
        with pytest.raises(InvalidUploadSessionTransitionError):
            repository.transition_state(
                invalid.id,
                expected_state=UploadSessionState.CREATED,
                target_state=UploadSessionState.RECEIVING,
                expected_version=0,
                updated_at_ms=20,
                failure_code="NOT_ALLOWED",
            )
    finally:
        engine.dispose()

    assert stored.failure_code == "NETWORK_TIMEOUT"


def test_no_absolute_path_can_be_persisted_in_upload_sessions(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with pytest.raises(Exception):
            repository.create(_session(storage_key="/absolute/file.mp4"))
        with pytest.raises(Exception):
            repository.create(
                _session(
                    storage_key="upload-session-0007",
                    state=UploadSessionState.CREATED,
                )
            )
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE upload_sessions "
                        "SET display_filename = '/absolute/file.mp4' "
                        "WHERE storage_key = 'upload-session-0007'"
                    )
                )
    finally:
        engine.dispose()


def test_upload_session_repository_does_not_access_media_files_or_providers() -> None:
    tree = ast.parse(UPLOAD_REPOSITORY_MODULE.read_text(encoding="utf-8"))
    forbidden_imports = {"openai", "requests", "httpx", "PIL", "subprocess"}
    forbidden_calls = {"open", "read_bytes", "write_bytes", "rename", "replace", "unlink"}
    imports: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
    assert sorted(set(imports) & forbidden_imports) == []
    assert sorted(set(calls) & forbidden_calls) == []
    assert "logical_media" not in UPLOAD_REPOSITORY_MODULE.read_text(encoding="utf-8")
    assert "media_metadata" not in UPLOAD_REPOSITORY_MODULE.read_text(encoding="utf-8")


def _create_at_state(
    repository: SqliteUploadSessionRepository,
    *,
    storage_key: str,
    state: UploadSessionState,
) -> UploadSession:
    session = _session(storage_key=storage_key, state=state)
    repository.create(session)
    return session


def _insert_raw_upload_session(
    engine: sa.Engine,
    *,
    session_id: UploadSessionId,
    storage_key: str,
    state: UploadSessionState,
    declared_size_bytes: int,
    received_size_bytes: int,
    version: int,
) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA ignore_check_constraints=ON"))
        connection.execute(
            sa.text(
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
                    'example.mp4',
                    :declared_size_bytes,
                    :received_size_bytes,
                    NULL,
                    NULL,
                    10,
                    10,
                    1000,
                    NULL,
                    :version
                )
                """
            ),
            {
                "id": session_id.to_string(),
                "state": state.value,
                "storage_key": storage_key,
                "declared_size_bytes": declared_size_bytes,
                "received_size_bytes": received_size_bytes,
                "version": version,
            },
        )
        connection.execute(sa.text("PRAGMA ignore_check_constraints=OFF"))
