"""Application-owned crash recovery for one validated upload publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time

from kronika.application.ports.published_media_storage import (
    PublishedMediaInsufficientSpaceError,
    PublishedMediaStorage,
    PublishedMediaStorageError,
)
from kronika.application.ports.quarantine_storage import (
    QuarantineStateInconsistentError,
    QuarantineStorage,
    QuarantineStorageUnavailableError,
    QuarantineWriteFailedError,
)
from kronika.application.ports.upload_publications import (
    FrameNestUploadPublicationRepositoryError,
    UnsupportedLegacyUploadPublicationStateError,
    UploadPublicationCandidate,
    UploadPublicationConcurrencyConflictError,
    UploadPublicationRepository,
)
from kronika.domain.upload_publications import (
    UploadPublicationCleanupState,
    UploadPublicationState,
    new_upload_publication_reservation,
)
from kronika.domain.uploads import UploadSessionId, UploadSessionState


class UploadPublicationError(RuntimeError):
    """Base sanitized automatic-publication processing error."""


class UploadPublicationNotFoundError(UploadPublicationError):
    """Raised when the requested durable upload is absent."""


class UploadPublicationStateError(UploadPublicationError):
    """Raised when durable upload and provenance state cannot be reconciled."""


class UploadPublicationConfigurationError(UploadPublicationError):
    """Raised when reserved work does not match the explicit destination."""


class UploadPublicationSourceError(UploadPublicationError):
    """Raised when no verified target and no safe exact source are available."""


class UploadPublicationInfrastructureError(UploadPublicationError):
    """Raised when storage or persistence safely leaves work retryable."""


class UploadPublicationInsufficientSpaceError(UploadPublicationInfrastructureError):
    """Raised when destination free space is insufficient before materialization."""


class UploadPublicationCleanupPendingError(UploadPublicationInfrastructureError):
    """Raised after ownership committed while exact source cleanup remains pending."""


@dataclass(frozen=True, slots=True)
class UploadPublicationResult:
    """Sanitized durable outcome for coordinator classification."""

    upload_id: str
    state: str
    cleanup_state: str | None


def default_now_ms() -> int:
    return int(time.time() * 1000)


class PublishPendingUpload:
    """Reserve, publish, commit, and clean one upload under caller-owned locking."""

    def __init__(
        self,
        repository: UploadPublicationRepository,
        storage: PublishedMediaStorage,
        quarantine: QuarantineStorage,
        *,
        now_ms: Callable[[], int] = default_now_ms,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._quarantine = quarantine
        self._now_ms = now_ms
        self._stop = threading.Event()

    def request_stop(self) -> None:
        """Request cooperative interruption of lifecycle-owned publication work."""
        self._stop.set()
        stopper = getattr(self._storage, "request_stop", None)
        if callable(stopper):
            stopper()

    def publish_owned_blocking(
        self,
        upload_id: UploadSessionId,
    ) -> UploadPublicationResult:
        """Reconstruct one publication from durable database and filesystem truth."""
        if self._stop.is_set():
            raise UploadPublicationInfrastructureError(
                "upload publication operation failed"
            )
        try:
            candidate = self._repository.get_candidate(upload_id)
            if candidate is None:
                raise UploadPublicationNotFoundError("upload publication not found")
            candidate = self._ensure_reservation(candidate)
            publication = candidate.publication
            assert publication is not None
            if publication.destination_id != self._storage.destination_id:
                raise UploadPublicationConfigurationError(
                    "upload publication configuration conflict"
                )
            if candidate.upload.state is UploadSessionState.PUBLISH_PENDING:
                candidate = self._publish_pending(candidate)
            return self._finish_cleanup(candidate)
        except UploadPublicationError:
            raise
        except UnsupportedLegacyUploadPublicationStateError as exc:
            raise UploadPublicationStateError(
                "unsupported legacy upload publication state"
            ) from exc
        except FrameNestUploadPublicationRepositoryError as exc:
            raise UploadPublicationInfrastructureError(
                "upload publication operation failed"
            ) from exc
        except PublishedMediaInsufficientSpaceError as exc:
            raise UploadPublicationInsufficientSpaceError(
                "insufficient published media storage"
            ) from exc
        except PublishedMediaStorageError as exc:
            raise UploadPublicationInfrastructureError(
                "upload publication storage failed"
            ) from exc

    def _ensure_reservation(
        self,
        candidate: UploadPublicationCandidate,
    ) -> UploadPublicationCandidate:
        if candidate.publication is not None:
            return candidate
        if candidate.upload.state in {
            UploadSessionState.PUBLISHED,
            UploadSessionState.CATALOGED,
        }:
            raise UploadPublicationStateError(
                "unsupported legacy upload publication state"
            )
        if candidate.upload.state is not UploadSessionState.PUBLISH_PENDING:
            raise UploadPublicationStateError("upload publication state conflict")
        reservation = new_upload_publication_reservation(
            candidate.upload,
            destination_id=self._storage.destination_id,
            now_ms=self._now_ms(),
        )
        return self._repository.get_or_create_reservation(
            reservation,
            expected_upload_version=candidate.upload.version,
        )

    def _publish_pending(
        self,
        candidate: UploadPublicationCandidate,
    ) -> UploadPublicationCandidate:
        publication = candidate.publication
        assert publication is not None
        if publication.state is not UploadPublicationState.RESERVED:
            raise UploadPublicationStateError("upload publication state conflict")
        try:
            target_verified = self._storage.verify_target(publication)
            if not target_verified:
                reader = self._quarantine.open_reader(
                    candidate.upload.storage_key,
                    expected_size_bytes=publication.expected_size_bytes,
                )
                try:
                    self._storage.publish_from_reader(publication, reader)
                finally:
                    reader.close()
        except QuarantineStateInconsistentError as exc:
            raise UploadPublicationSourceError(
                "upload publication source unavailable"
            ) from exc
        except QuarantineStorageUnavailableError as exc:
            raise UploadPublicationInfrastructureError(
                "upload publication source unavailable"
            ) from exc
        try:
            return self._repository.commit_verified_publication(
                candidate.upload.id,
                publication_id=publication.publication_id,
                expected_upload_version=candidate.upload.version,
                expected_publication_version=publication.version,
                updated_at_ms=self._now_ms(),
            )
        except UploadPublicationConcurrencyConflictError:
            current = self._repository.get_candidate(candidate.upload.id)
            if (
                current is not None
                and current.upload.state is UploadSessionState.PUBLISHED
                and current.publication is not None
                and current.publication.publication_id == publication.publication_id
                and current.publication.state is UploadPublicationState.VERIFIED
            ):
                return current
            raise

    def _finish_cleanup(
        self,
        candidate: UploadPublicationCandidate,
    ) -> UploadPublicationResult:
        publication = candidate.publication
        if publication is None:
            raise UploadPublicationStateError("upload publication state conflict")
        if (
            candidate.upload.state is not UploadSessionState.PUBLISHED
            or publication.state is not UploadPublicationState.VERIFIED
        ):
            raise UploadPublicationStateError("upload publication state conflict")
        if publication.cleanup_state is UploadPublicationCleanupState.COMPLETE:
            return _result(candidate)
        try:
            self._quarantine.remove(candidate.upload.storage_key)
        except (QuarantineWriteFailedError, QuarantineStorageUnavailableError) as exc:
            raise UploadPublicationCleanupPendingError(
                "upload publication cleanup pending"
            ) from exc
        try:
            completed = self._repository.mark_cleanup_complete(
                candidate.upload.id,
                publication_id=publication.publication_id,
                expected_publication_version=publication.version,
                updated_at_ms=self._now_ms(),
            )
        except UploadPublicationConcurrencyConflictError:
            current = self._repository.get_candidate(candidate.upload.id)
            if (
                current is not None
                and current.publication is not None
                and current.publication.publication_id == publication.publication_id
                and current.publication.cleanup_state
                is UploadPublicationCleanupState.COMPLETE
            ):
                return _result(current)
            raise
        return _result(completed)


def _result(candidate: UploadPublicationCandidate) -> UploadPublicationResult:
    publication = candidate.publication
    return UploadPublicationResult(
        upload_id=candidate.upload.id.to_string(),
        state=candidate.upload.state.value,
        cleanup_state=None if publication is None else publication.cleanup_state.value,
    )
