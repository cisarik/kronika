"""Application port for durable upload sessions."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.media_byte_identities import MediaByteIdentity
from kronika.domain.uploads import (
    UploadDuplicateDisposition,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)


class FrameNestUploadSessionRepositoryError(RuntimeError):
    """Base sanitized upload-session repository error."""


class UploadSessionAlreadyExistsError(FrameNestUploadSessionRepositoryError):
    """Raised when an upload session identity already exists."""


class UploadStorageKeyAlreadyExistsError(FrameNestUploadSessionRepositoryError):
    """Raised when an upload storage key already exists."""


class UploadSessionNotFoundError(FrameNestUploadSessionRepositoryError):
    """Raised when an upload session does not exist."""


class InvalidUploadSessionTransitionError(FrameNestUploadSessionRepositoryError):
    """Raised when an upload session cannot enter the requested state."""


class IncompleteUploadSessionError(FrameNestUploadSessionRepositoryError):
    """Raised when a state transition requires complete received bytes."""


class UploadOffsetConflictError(FrameNestUploadSessionRepositoryError):
    """Raised when the expected byte offset is stale."""


class UploadSizeLimitExceededError(FrameNestUploadSessionRepositoryError):
    """Raised when accepted bytes would exceed the declared upload size."""


class UploadSessionConcurrencyConflictError(FrameNestUploadSessionRepositoryError):
    """Raised when an optimistic concurrency guard is stale."""


class InvalidUploadChecksumError(FrameNestUploadSessionRepositoryError):
    """Raised when upload checksum metadata is invalid or conflicting."""


class InvalidUploadValidationEvidenceError(FrameNestUploadSessionRepositoryError):
    """Raised when upload validation evidence is invalid or conflicting."""


class UploadSessionRepository(Protocol):
    """Persistence-independent durable upload-session contract."""

    def create(self, session: UploadSession) -> None:
        """Persist one new valid upload session."""

    def get(self, session_id: UploadSessionId) -> UploadSession | None:
        """Return one upload session by identity, or None when absent."""

    def list_startup_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        """Return bounded received or startup-abandoned validating uploads."""

    def list_runtime_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        """Return bounded received uploads for runtime validation notification."""

    def advance_received_offset(
        self,
        session_id: UploadSessionId,
        *,
        expected_received_size_bytes: int,
        accepted_size_bytes: int,
        expected_version: int,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically advance a receiving session's byte offset."""

    def record_completed_checksum(
        self,
        session_id: UploadSessionId,
        *,
        expected_state: UploadSessionState,
        expected_version: int,
        checksum_hex: str,
        updated_at_ms: int,
    ) -> UploadSession:
        """Record a validated completed checksum without calculating it."""

    def transition_state(
        self,
        session_id: UploadSessionId,
        *,
        expected_state: UploadSessionState,
        target_state: UploadSessionState,
        expected_version: int,
        updated_at_ms: int,
        failure_code: str | None = None,
    ) -> UploadSession:
        """Atomically apply one valid guarded state transition."""

    def start_validation(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically transition a received session to validating."""

    def complete_validation_success(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        checksum_hex: str,
        validated_media_kind: UploadValidatedMediaKind,
        validated_format: UploadValidatedFormat,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically persist validation evidence and exact-duplicate disposition."""

    def record_duplicate_disposition(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        disposition: UploadDuplicateDisposition,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically persist one explicit duplicate state and disposition."""

    def get_or_create_byte_identity(
        self,
        identity: MediaByteIdentity,
    ) -> MediaByteIdentity:
        """Race-safely return the canonical exact-byte identity for evidence."""

    def reject_validation(
        self,
        session_id: UploadSessionId,
        *,
        expected_version: int,
        failure_code: str,
        updated_at_ms: int,
    ) -> UploadSession:
        """Atomically persist sanitized validation rejection and rejected state."""
