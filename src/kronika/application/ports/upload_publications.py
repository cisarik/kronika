"""Application port for durable upload publication provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.domain.identities import MediaByteIdentityId
from kronika.domain.media import LogicalMedia, MediaLocation
from kronika.domain.media_metadata import MediaMetadata
from kronika.domain.upload_publications import UploadPublication, UploadPublicationId
from kronika.domain.uploads import UploadSession, UploadSessionId


class FrameNestUploadPublicationRepositoryError(RuntimeError):
    """Base sanitized upload-publication repository failure."""


class UploadPublicationNotFoundError(FrameNestUploadPublicationRepositoryError):
    """Raised when durable publication provenance is absent."""


class UploadPublicationStateConflictError(FrameNestUploadPublicationRepositoryError):
    """Raised when upload or publication state is incompatible."""


class UploadPublicationConcurrencyConflictError(
    FrameNestUploadPublicationRepositoryError
):
    """Raised when an optimistic upload or publication guard is stale."""


class UploadPublicationEvidenceConflictError(FrameNestUploadPublicationRepositoryError):
    """Raised when provenance disagrees with authoritative upload evidence."""


class UploadPublicationTargetConflictError(FrameNestUploadPublicationRepositoryError):
    """Raised when a reserved opaque target cannot be persisted uniquely."""


class UnsupportedLegacyUploadPublicationStateError(
    FrameNestUploadPublicationRepositoryError
):
    """Raised for legacy advanced upload state without publication provenance."""


class UploadCatalogStateConflictError(FrameNestUploadPublicationRepositoryError):
    """Raised when upload or catalog linkage is incompatible with cataloging."""


class UploadCatalogInconsistencyError(FrameNestUploadPublicationRepositoryError):
    """Raised when durable catalog linkage disagrees with upload state."""


@dataclass(frozen=True, slots=True)
class UploadPublicationCandidate:
    """One bounded durable upload publication work item."""

    upload: UploadSession
    publication: UploadPublication | None


class UploadPublicationRepository(Protocol):
    """Persistence-independent atomic publication transaction contract."""

    def get_candidate(
        self,
        upload_id: UploadSessionId,
    ) -> UploadPublicationCandidate | None:
        """Return authoritative upload and optional provenance, or None when absent."""

    def list_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadPublicationCandidate, ...]:
        """Return bounded pending publication or cleanup work."""

    def get_or_create_reservation(
        self,
        reservation: UploadPublication,
        *,
        expected_upload_version: int,
    ) -> UploadPublicationCandidate:
        """Commit exactly one per-upload reservation before filesystem mutation."""

    def commit_verified_publication(
        self,
        upload_id: UploadSessionId,
        *,
        publication_id: UploadPublicationId,
        expected_upload_version: int,
        expected_publication_version: int,
        updated_at_ms: int,
    ) -> UploadPublicationCandidate:
        """Atomically persist verified provenance and transition to published."""

    def mark_cleanup_complete(
        self,
        upload_id: UploadSessionId,
        *,
        publication_id: UploadPublicationId,
        expected_publication_version: int,
        updated_at_ms: int,
    ) -> UploadPublicationCandidate:
        """Idempotently persist exact quarantine cleanup completion."""

    def list_catalog_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadPublicationCandidate, ...]:
        """Return bounded published uploads eligible for catalog creation."""

    def find_cataloged_by_byte_identity(
        self,
        byte_identity_id: MediaByteIdentityId,
        *,
        exclude_upload_id: UploadSessionId,
    ) -> UploadPublicationCandidate | None:
        """Return the canonical cataloged upload for exact-byte reuse."""

    def commit_cataloged_publication(
        self,
        upload_id: UploadSessionId,
        *,
        media: LogicalMedia,
        location: MediaLocation,
        expected_upload_version: int,
        expected_publication_version: int,
        updated_at_ms: int,
        metadata: MediaMetadata | None = None,
    ) -> UploadPublicationCandidate:
        """Atomically create catalog rows, link provenance, and set cataloged."""
