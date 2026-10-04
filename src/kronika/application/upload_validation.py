"""Internal application use case for validating received quarantined uploads."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from kronika.application.ports.quarantine_storage import (
    QuarantineReader,
    QuarantineStateInconsistentError,
    QuarantineStorage,
    QuarantineStorageUnavailableError,
)
from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationInfrastructureError,
    UploadMediaValidationInterruptedError,
    UploadMediaValidationRejectedError,
    UploadMediaValidator,
)
from kronika.application.ports.upload_sessions import (
    FrameNestUploadSessionRepositoryError,
    IncompleteUploadSessionError,
    InvalidUploadChecksumError,
    InvalidUploadSessionTransitionError,
    InvalidUploadValidationEvidenceError,
    UploadSessionConcurrencyConflictError,
    UploadSessionNotFoundError,
    UploadSessionRepository,
)
from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.domain.uploads import (
    UploadSession,
    UploadSessionId,
    UploadSessionState,
)

UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
UPLOAD_VALIDATION_INVALID_MEDIA = "INVALID_MEDIA"
UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE = "AMBIGUOUS_MEDIA_TYPE"
UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT = "MEDIA_POLICY_LIMIT"
UPLOAD_VALIDATION_TIMEOUT = "VALIDATION_TIMEOUT"
UPLOAD_VALIDATION_OUTPUT_LIMIT = "VALIDATION_OUTPUT_LIMIT"

UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT = "QUARANTINE_INCONSISTENT"
UPLOAD_VALIDATION_TOOL_UNAVAILABLE = "VALIDATION_TOOL_UNAVAILABLE"
UPLOAD_VALIDATION_IO_ERROR = "VALIDATION_IO_ERROR"
UPLOAD_VALIDATION_INTERNAL_ERROR = "VALIDATION_INTERNAL_ERROR"

_HASH_CHUNK_SIZE = 1024 * 1024


class UploadValidationError(RuntimeError):
    """Base sanitized upload-validation use-case error."""


class UploadValidationNotFoundError(UploadValidationError):
    """Raised when the upload session is absent."""


class UploadValidationStateConflictError(UploadValidationError):
    """Raised when the upload session state is not valid for validation."""


class UploadValidationQuarantineInconsistentError(UploadValidationError):
    """Raised when quarantine and durable session state do not agree."""


class UploadValidationUnavailableError(UploadValidationError):
    """Raised when validation infrastructure is unavailable."""


class UploadValidationConcurrencyError(UploadValidationError):
    """Raised when optimistic persistence guards reject validation."""


class UploadValidationInterruptedError(UploadValidationError):
    """Raised when lifecycle shutdown interrupts retryable validation work."""


@dataclass(frozen=True, slots=True)
class UploadValidationResult:
    """Internal validation result for deterministic application tests."""

    upload_id: str
    state: str
    checksum_algorithm: str | None
    checksum_hex: str | None
    validated_media_kind: str | None
    validated_format: str | None
    failure_code: str | None


def default_now_ms() -> int:
    """Return current wall-clock milliseconds for durable validation timestamps."""
    return int(time.time() * 1000)


class ValidateReceivedUpload:
    """Validate one complete quarantined upload without publishing it."""

    def __init__(
        self,
        repository: UploadSessionRepository,
        storage: QuarantineStorage,
        validator: UploadMediaValidator,
        *,
        now_ms: Callable[[], int] = default_now_ms,
        locks: UploadSessionLockRegistry | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._validator = validator
        self._now_ms = now_ms
        self._locks = locks or UploadSessionLockRegistry()
        self._stop = threading.Event()

    def request_stop(self) -> None:
        """Request cooperative interruption at safe hash chunk boundaries."""
        self._stop.set()

    async def validate(self, session_id: UploadSessionId) -> UploadValidationResult:
        """Validate a received upload and return its internal durable state."""
        async with self._locks.lease(session_id):
            return self.validate_owned_blocking(session_id)

    def validate_owned_blocking(
        self,
        session_id: UploadSessionId,
    ) -> UploadValidationResult:
        """Validate a received upload while the caller owns the process-local lock."""
        session = self._load(session_id)
        if session.state in {
            UploadSessionState.DUPLICATE_PENDING,
            UploadSessionState.PUBLISH_PENDING,
        }:
            return _result(session)
        if session.state is UploadSessionState.REJECTED:
            return _result(session)
        if session.state is UploadSessionState.RECEIVED:
            session = self._start_validation(session)
        if session.state is not UploadSessionState.VALIDATING:
            raise UploadValidationStateConflictError("upload validation state conflict")
        return self._validate_stable_quarantine_object(session)

    def recover_abandoned_validating_owned_blocking(
        self,
        session_id: UploadSessionId,
    ) -> UploadValidationResult:
        """Recover a startup-discovered abandoned validating upload.

        This internal entry point deliberately does not run the received-to-validating
        claim. It is safe only at coordinator startup, where the previous process-local
        validation owner cannot still exist in this single-process orchestration model.
        """
        session = self._load(session_id)
        if session.state in {
            UploadSessionState.DUPLICATE_PENDING,
            UploadSessionState.PUBLISH_PENDING,
        }:
            return _result(session)
        if session.state is UploadSessionState.REJECTED:
            return _result(session)
        if session.state is not UploadSessionState.VALIDATING:
            raise UploadValidationStateConflictError("upload validation state conflict")
        return self._validate_stable_quarantine_object(session)

    def _load(self, session_id: UploadSessionId) -> UploadSession:
        try:
            session = self._repository.get(session_id)
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadValidationUnavailableError("upload validation unavailable") from exc
        if session is None:
            raise UploadValidationNotFoundError("upload session not found")
        return session

    def _start_validation(self, session: UploadSession) -> UploadSession:
        try:
            return self._repository.start_validation(
                session.id,
                expected_version=session.version,
                updated_at_ms=self._now_ms(),
            )
        except UploadSessionNotFoundError as exc:
            raise UploadValidationNotFoundError("upload session not found") from exc
        except IncompleteUploadSessionError as exc:
            self._fail_if_possible(session, UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT)
            raise UploadValidationQuarantineInconsistentError(
                "quarantine state inconsistent"
            ) from exc
        except InvalidUploadSessionTransitionError as exc:
            current = self._load(session.id)
            if current.state in {
                UploadSessionState.VALIDATING,
                UploadSessionState.DUPLICATE_PENDING,
                UploadSessionState.PUBLISH_PENDING,
                UploadSessionState.REJECTED,
            }:
                return current
            raise UploadValidationStateConflictError("upload validation state conflict") from exc
        except UploadSessionConcurrencyConflictError as exc:
            current = self._load(session.id)
            if current.state in {
                UploadSessionState.VALIDATING,
                UploadSessionState.DUPLICATE_PENDING,
                UploadSessionState.PUBLISH_PENDING,
                UploadSessionState.REJECTED,
            }:
                return current
            raise UploadValidationConcurrencyError("upload validation concurrency conflict") from exc
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadValidationUnavailableError("upload validation unavailable") from exc

    def _validate_stable_quarantine_object(
        self,
        session: UploadSession,
    ) -> UploadValidationResult:
        if session.received_size_bytes != session.declared_size_bytes:
            self._fail_if_possible(session, UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT)
            raise UploadValidationQuarantineInconsistentError("quarantine state inconsistent")
        reader = self._open_reader(session)
        try:
            checksum_hex = _sha256(reader, stop_event=self._stop)
            reader.verify_still_consistent()
            reader.seek_start()
            try:
                evidence = self._validator.validate(reader)
            except UploadMediaValidationInterruptedError as exc:
                raise UploadValidationInterruptedError(
                    "upload validation interrupted"
                ) from exc
            except UploadValidationInterruptedError:
                raise
            except UploadMediaValidationRejectedError as exc:
                rejected = self._reject(session, exc.failure_code)
                return _result(rejected)
            except UploadMediaValidationInfrastructureError as exc:
                self._fail_if_possible(session, exc.failure_code)
                raise _validation_infrastructure_error(exc.failure_code) from exc
            except Exception:
                self._fail_if_possible(session, UPLOAD_VALIDATION_INTERNAL_ERROR)
                raise UploadValidationUnavailableError("upload validation unavailable") from None
            reader.verify_still_consistent()
            reader.seek_start()
            second_checksum_hex = _sha256(reader, stop_event=self._stop)
            if second_checksum_hex != checksum_hex:
                self._fail_if_possible(session, UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT)
                raise UploadValidationQuarantineInconsistentError(
                    "quarantine state inconsistent"
                )
            reader.verify_still_consistent()
            completed = self._complete_success(
                session,
                checksum_hex=checksum_hex,
                evidence=evidence,
            )
            return _result(completed)
        except QuarantineStateInconsistentError as exc:
            self._fail_if_possible(session, UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT)
            raise UploadValidationQuarantineInconsistentError(
                "quarantine state inconsistent"
            ) from exc
        finally:
            reader.close()

    def _open_reader(self, session: UploadSession) -> QuarantineReader:
        try:
            return self._storage.open_reader(
                session.storage_key,
                expected_size_bytes=session.declared_size_bytes,
            )
        except QuarantineStateInconsistentError as exc:
            self._fail_if_possible(session, UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT)
            raise UploadValidationQuarantineInconsistentError(
                "quarantine state inconsistent"
            ) from exc
        except QuarantineStorageUnavailableError as exc:
            self._fail_if_possible(session, UPLOAD_VALIDATION_IO_ERROR)
            raise UploadValidationUnavailableError("upload validation unavailable") from exc

    def _complete_success(
        self,
        session: UploadSession,
        *,
        checksum_hex: str,
        evidence: object,
    ) -> UploadSession:
        media_kind = getattr(evidence, "media_kind")
        media_format = getattr(evidence, "media_format")
        try:
            return self._repository.complete_validation_success(
                session.id,
                expected_version=session.version,
                checksum_hex=checksum_hex,
                validated_media_kind=media_kind,
                validated_format=media_format,
                updated_at_ms=self._now_ms(),
            )
        except (
            InvalidUploadChecksumError,
            InvalidUploadValidationEvidenceError,
        ) as exc:
            self._fail_if_possible(session, UPLOAD_VALIDATION_INTERNAL_ERROR)
            raise UploadValidationUnavailableError("upload validation unavailable") from exc
        except InvalidUploadSessionTransitionError as exc:
            current = self._load(session.id)
            if _matches_success(current, checksum_hex, media_kind, media_format):
                return current
            raise UploadValidationStateConflictError("upload validation state conflict") from exc
        except UploadSessionConcurrencyConflictError as exc:
            current = self._load(session.id)
            if _matches_success(current, checksum_hex, media_kind, media_format):
                return current
            raise UploadValidationConcurrencyError("upload validation concurrency conflict") from exc
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadValidationUnavailableError("upload validation unavailable") from exc

    def _reject(self, session: UploadSession, failure_code: str) -> UploadSession:
        try:
            return self._repository.reject_validation(
                session.id,
                expected_version=session.version,
                failure_code=failure_code,
                updated_at_ms=self._now_ms(),
            )
        except UploadSessionNotFoundError as exc:
            raise UploadValidationNotFoundError("upload session not found") from exc
        except InvalidUploadSessionTransitionError as exc:
            current = self._load(session.id)
            if current.state is UploadSessionState.REJECTED:
                return current
            raise UploadValidationStateConflictError("upload validation state conflict") from exc
        except UploadSessionConcurrencyConflictError as exc:
            current = self._load(session.id)
            if current.state is UploadSessionState.REJECTED:
                return current
            raise UploadValidationConcurrencyError("upload validation concurrency conflict") from exc
        except FrameNestUploadSessionRepositoryError as exc:
            raise UploadValidationUnavailableError("upload validation unavailable") from exc

    def _fail_if_possible(self, session: UploadSession, failure_code: str) -> bool:
        if session.state is not UploadSessionState.VALIDATING:
            return False
        try:
            self._repository.transition_state(
                session.id,
                expected_state=UploadSessionState.VALIDATING,
                target_state=UploadSessionState.FAILED,
                expected_version=session.version,
                updated_at_ms=self._now_ms(),
                failure_code=failure_code,
            )
            return True
        except FrameNestUploadSessionRepositoryError:
            try:
                current = self._repository.get(session.id)
            except FrameNestUploadSessionRepositoryError:
                return False
            return (
                current is not None
                and current.state is UploadSessionState.FAILED
                and current.failure_code == failure_code
            )


def _sha256(
    reader: QuarantineReader,
    *,
    stop_event: threading.Event | None = None,
) -> str:
    reader.seek_start()
    digest = hashlib.sha256()
    while True:
        if stop_event is not None and stop_event.is_set():
            raise UploadValidationInterruptedError("upload validation interrupted")
        chunk = reader.read(_HASH_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _result(session: UploadSession) -> UploadValidationResult:
    return UploadValidationResult(
        upload_id=session.id.to_string(),
        state=session.state.value,
        checksum_algorithm=session.checksum_algorithm,
        checksum_hex=session.checksum_hex,
        validated_media_kind=None
        if session.validated_media_kind is None
        else session.validated_media_kind.value,
        validated_format=None if session.validated_format is None else session.validated_format.value,
        failure_code=session.failure_code,
    )


def _matches_success(
    session: UploadSession,
    checksum_hex: str,
    media_kind: object,
    media_format: object,
) -> bool:
    return (
        session.state
        in {
            UploadSessionState.DUPLICATE_PENDING,
            UploadSessionState.PUBLISH_PENDING,
        }
        and session.checksum_algorithm == "sha256"
        and session.checksum_hex == checksum_hex
        and session.validated_media_kind == media_kind
        and session.validated_format == media_format
    )


def _validation_infrastructure_error(failure_code: str) -> UploadValidationError:
    if failure_code == UPLOAD_VALIDATION_QUARANTINE_INCONSISTENT:
        return UploadValidationQuarantineInconsistentError("quarantine state inconsistent")
    return UploadValidationUnavailableError("upload validation unavailable")
