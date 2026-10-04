"""Unit tests for internal upload-validation use case."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationEvidence,
    UploadMediaValidationRejectedError,
)
from kronika.application.upload_validation import (
    UPLOAD_VALIDATION_INTERNAL_ERROR,
    UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT,
    UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE,
    UploadValidationInterruptedError,
    UploadValidationQuarantineInconsistentError,
    UploadValidationUnavailableError,
    ValidateReceivedUpload,
)
from kronika.configuration import KronikaSettings
from kronika.domain.uploads import (
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.infrastructure.filesystem.quarantine_storage import FilesystemQuarantineStorage
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)
from kronika.application.ports.upload_sessions import FrameNestUploadSessionRepositoryError


class _Validator:
    def __init__(
        self,
        evidence: UploadMediaValidationEvidence | None = None,
        rejection_code: str | None = None,
        on_validate=None,
    ) -> None:
        self._evidence = evidence or UploadMediaValidationEvidence(
            UploadValidatedMediaKind.VIDEO,
            UploadValidatedFormat.MP4,
        )
        self._rejection_code = rejection_code
        self._on_validate = on_validate
        self.calls = 0

    def validate(self, reader):
        self.calls += 1
        if self._on_validate is not None:
            self._on_validate(reader)
        if self._rejection_code is not None:
            raise UploadMediaValidationRejectedError(self._rejection_code)
        return self._evidence


def _setup(
    tmp_path: Path,
    *,
    payload: bytes = b"uploaded-bytes",
    state: UploadSessionState = UploadSessionState.RECEIVED,
    validator: _Validator | None = None,
):
    database_path = tmp_path / "catalog.sqlite3"
    quarantine_root = tmp_path / "quarantine"
    quarantine_root.mkdir()
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    engine = create_sqlite_engine(database_path)
    repository = SqliteUploadSessionRepository(engine)
    storage = FilesystemQuarantineStorage(quarantine_root)
    session = UploadSession(
        id=UploadSessionId.new(),
        state=state,
        storage_key=UploadStorageKey("validationupload0001"),
        display_filename=UploadDisplayFilename("client-name.mp4"),
        declared_size_bytes=len(payload),
        received_size_bytes=len(payload),
        checksum_algorithm=None,
        checksum_hex=None,
        created_at_ms=10,
        updated_at_ms=10,
        expires_at_ms=10_000,
        failure_code=None,
        version=0,
    )
    repository.create(session)
    path = quarantine_root / f"{session.storage_key.value}.part"
    path.write_bytes(payload)
    service = ValidateReceivedUpload(
        repository,
        storage,
        validator or _Validator(),
        now_ms=lambda: 20,
    )
    return service, repository, engine, session, path


def test_success_persists_checksum_validation_evidence_and_no_catalog_visibility(
    tmp_path: Path,
) -> None:
    service, repository, engine, session, _path = _setup(tmp_path)
    try:
        result = asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
        with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
            logical_count = connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()
            location_count = connection.execute(
                "SELECT COUNT(*) FROM physical_media_locations"
            ).fetchone()
            identity_row = connection.execute(
                """
                SELECT
                    media_byte_identities.checksum_algorithm,
                    media_byte_identities.size_bytes,
                    media_byte_identities.checksum_hex
                FROM upload_sessions
                JOIN media_byte_identities
                  ON media_byte_identities.id = upload_sessions.byte_identity_id
                WHERE upload_sessions.id = ?
                """,
                (session.id.to_string(),),
            ).fetchone()
    finally:
        dispose_engine(engine)

    assert result.state == UploadSessionState.PUBLISH_PENDING.value
    assert result.checksum_algorithm == "sha256"
    assert result.checksum_hex is not None and len(result.checksum_hex) == 64
    assert result.validated_media_kind == "video"
    assert result.validated_format == "mp4"
    assert stored is not None
    assert stored.state is UploadSessionState.PUBLISH_PENDING
    assert stored.byte_identity_id is not None
    assert identity_row == ("sha256", len(b"uploaded-bytes"), result.checksum_hex)
    assert logical_count == (0,)
    assert location_count == (0,)


def test_successful_gif_validation_creates_byte_identity(tmp_path: Path) -> None:
    service, repository, engine, session, _path = _setup(
        tmp_path,
        payload=b"gif-bytes",
        validator=_Validator(
            UploadMediaValidationEvidence(
                UploadValidatedMediaKind.ANIMATED_IMAGE,
                UploadValidatedFormat.GIF,
            )
        ),
    )
    try:
        result = asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert result.state == UploadSessionState.PUBLISH_PENDING.value
    assert result.validated_media_kind == "animated_image"
    assert result.validated_format == "gif"
    assert stored is not None
    assert stored.byte_identity_id is not None


def test_abandoned_validating_recovery_completes_without_reclaiming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, engine, session, _path = _setup(
        tmp_path,
        state=UploadSessionState.VALIDATING,
    )

    def fail_start_validation(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("validating recovery must not reclaim received state")

    monkeypatch.setattr(repository, "start_validation", fail_start_validation)
    try:
        result = service.recover_abandoned_validating_owned_blocking(session.id)
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert result.state == UploadSessionState.PUBLISH_PENDING.value
    assert stored is not None
    assert stored.state is UploadSessionState.PUBLISH_PENDING


def test_abandoned_validating_recovery_persists_rejected_and_failed_outcomes(
    tmp_path: Path,
) -> None:
    rejected_root = tmp_path / "rejected"
    failed_root = tmp_path / "failed"
    rejected_root.mkdir()
    failed_root.mkdir()
    rejected_service, rejected_repository, rejected_engine, rejected_session, _path = _setup(
        rejected_root,
        state=UploadSessionState.VALIDATING,
        validator=_Validator(rejection_code=UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE),
    )

    def raise_unexpected(_reader) -> None:
        raise RuntimeError("raw validator detail")

    failed_service, failed_repository, failed_engine, failed_session, _path = _setup(
        failed_root,
        state=UploadSessionState.VALIDATING,
        validator=_Validator(on_validate=raise_unexpected),
    )
    try:
        rejected = rejected_service.recover_abandoned_validating_owned_blocking(
            rejected_session.id
        )
        rejected_stored = rejected_repository.get(rejected_session.id)
        with pytest.raises(UploadValidationUnavailableError):
            failed_service.recover_abandoned_validating_owned_blocking(failed_session.id)
        failed_stored = failed_repository.get(failed_session.id)
    finally:
        dispose_engine(rejected_engine)
        dispose_engine(failed_engine)

    assert rejected.state == UploadSessionState.REJECTED.value
    assert rejected_stored is not None
    assert rejected_stored.failure_code == UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE
    assert failed_stored is not None
    assert failed_stored.state is UploadSessionState.FAILED
    assert failed_stored.failure_code == UPLOAD_VALIDATION_INTERNAL_ERROR


def test_abandoned_validating_inconsistent_object_persists_no_validation_evidence(
    tmp_path: Path,
) -> None:
    def mutate_open_object(reader) -> None:
        path.write_bytes(b"mutated-bytes")
        reader.seek_start()

    service, repository, engine, session, path = _setup(
        tmp_path,
        state=UploadSessionState.VALIDATING,
        payload=b"original-bytes",
        validator=_Validator(on_validate=mutate_open_object),
    )
    try:
        with pytest.raises(UploadValidationQuarantineInconsistentError):
            service.recover_abandoned_validating_owned_blocking(session.id)
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert stored is not None
    assert stored.state is UploadSessionState.FAILED
    assert stored.failure_code == UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT
    assert stored.checksum_hex is None
    assert stored.validated_media_kind is None


def test_permanent_policy_rejection_records_sanitized_code(tmp_path: Path) -> None:
    service, repository, engine, session, _path = _setup(
        tmp_path,
        validator=_Validator(rejection_code=UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE),
    )
    try:
        result = asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert result.state == UploadSessionState.REJECTED.value
    assert result.failure_code == UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE
    assert stored is not None
    assert stored.checksum_hex is None
    assert stored.validated_media_kind is None


def test_matching_publish_pending_result_is_idempotent_without_reprobe(tmp_path: Path) -> None:
    validator = _Validator()
    service, repository, engine, session, _path = _setup(tmp_path, validator=validator)
    try:
        first = asyncio.run(service.validate(session.id))
        second = asyncio.run(service.validate(session.id))
    finally:
        dispose_engine(engine)

    assert first == second
    assert validator.calls == 1


def test_later_byte_identical_validation_waits_for_explicit_duplicate_resolution(
    tmp_path: Path,
) -> None:
    payload = b"same-authoritative-bytes"
    validator = _Validator()
    service, repository, engine, first, _path = _setup(
        tmp_path,
        payload=payload,
        validator=validator,
    )
    second = UploadSession(
        id=UploadSessionId.new(),
        state=UploadSessionState.RECEIVED,
        storage_key=UploadStorageKey("validationupload0002"),
        display_filename=UploadDisplayFilename("different-client-name.gif"),
        declared_size_bytes=len(payload),
        received_size_bytes=len(payload),
        checksum_algorithm=None,
        checksum_hex=None,
        created_at_ms=11,
        updated_at_ms=11,
        expires_at_ms=10_000,
        failure_code=None,
        version=0,
    )
    repository.create(second)
    (tmp_path / "quarantine" / f"{second.storage_key.value}.part").write_bytes(payload)
    try:
        first_result = asyncio.run(service.validate(first.id))
        second_result = asyncio.run(service.validate(second.id))
        repeated = asyncio.run(service.validate(second.id))
        first_stored = repository.get(first.id)
        second_stored = repository.get(second.id)
    finally:
        dispose_engine(engine)

    assert first_result.state == UploadSessionState.PUBLISH_PENDING.value
    assert second_result.state == UploadSessionState.DUPLICATE_PENDING.value
    assert repeated == second_result
    assert first_stored is not None
    assert second_stored is not None
    assert first_stored.byte_identity_id == second_stored.byte_identity_id
    assert validator.calls == 2


def test_path_replacement_after_open_does_not_split_hash_and_probe_object(
    tmp_path: Path,
) -> None:
    def replace_path(_reader) -> None:
        path.unlink()
        path.write_bytes(b"replacement!")

    service, repository, engine, session, path = _setup(
        tmp_path,
        payload=b"original!!!!",
        validator=_Validator(on_validate=replace_path),
    )
    try:
        result = asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert result.state == UploadSessionState.PUBLISH_PENDING.value
    assert stored is not None
    assert stored.checksum_hex == result.checksum_hex


def test_in_place_object_mutation_is_detected_before_success_persistence(
    tmp_path: Path,
) -> None:
    def mutate_open_object(reader) -> None:
        path.write_bytes(b"mutated-bytes")
        reader.seek_start()

    service, repository, engine, session, path = _setup(
        tmp_path,
        payload=b"original-bytes",
        validator=_Validator(on_validate=mutate_open_object),
    )
    try:
        with pytest.raises(UploadValidationQuarantineInconsistentError):
            asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert stored is not None
    assert stored.state is UploadSessionState.FAILED
    assert stored.failure_code == UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT


@pytest.mark.parametrize(
    "exception",
    [
        ValueError("raw-private-validator-detail /tmp/private"),
        OverflowError("raw overflow detail"),
        RuntimeError("raw arbitrary detail"),
    ],
)
def test_unexpected_validator_exception_records_sanitized_internal_failure(
    tmp_path: Path,
    exception: Exception,
) -> None:
    def raise_unexpected(_reader) -> None:
        raise exception

    service, repository, engine, session, _path = _setup(
        tmp_path,
        validator=_Validator(on_validate=raise_unexpected),
    )
    try:
        with pytest.raises(UploadValidationUnavailableError) as error:
            asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert str(error.value) == "upload validation unavailable"
    assert "raw" not in str(error.value)
    assert error.value.__cause__ is None
    assert stored is not None
    assert stored.state is UploadSessionState.FAILED
    assert stored.failure_code == UPLOAD_VALIDATION_INTERNAL_ERROR


def test_unexpected_validator_failure_recording_failure_stays_sanitized_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_unexpected(_reader) -> None:
        raise ValueError("raw-private-validator-detail")

    service, repository, engine, session, _path = _setup(
        tmp_path,
        validator=_Validator(on_validate=raise_unexpected),
    )

    def fail_transition(*_args: object, **_kwargs: object) -> object:
        raise FrameNestUploadSessionRepositoryError("raw repository detail")

    monkeypatch.setattr(repository, "transition_state", fail_transition)
    try:
        with pytest.raises(UploadValidationUnavailableError) as error:
            asyncio.run(service.validate(session.id))
        stored = repository.get(session.id)
    finally:
        dispose_engine(engine)

    assert str(error.value) == "upload validation unavailable"
    assert error.value.__cause__ is None
    assert stored is not None
    assert stored.state is UploadSessionState.VALIDATING
    assert stored.failure_code is None


def test_cooperative_interruption_leaves_validating_recoverable(
    tmp_path: Path,
) -> None:
    service, repository, engine, session, _path = _setup(tmp_path)
    try:
        service.request_stop()
        with pytest.raises(UploadValidationInterruptedError):
            service.validate_owned_blocking(session.id)
        stored = repository.get(session.id)
        assert stored is not None
        assert stored.state is UploadSessionState.VALIDATING
        assert stored.failure_code is None
        recovered = ValidateReceivedUpload(
            repository,
            FilesystemQuarantineStorage(tmp_path / "quarantine"),
            _Validator(),
            now_ms=lambda: 30,
        )
        result = recovered.recover_abandoned_validating_owned_blocking(session.id)
        restored = repository.get(session.id)
    finally:
        dispose_engine(engine)
    assert result.state == UploadSessionState.PUBLISH_PENDING.value
    assert restored is not None
    assert restored.state is UploadSessionState.PUBLISH_PENDING
