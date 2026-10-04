"""Application service for lifecycle-owned published-to-cataloged transition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kronika.application.ports.upload_publications import (
    FrameNestUploadPublicationRepositoryError,
    UploadCatalogInconsistencyError,
    UploadCatalogStateConflictError,
    UploadPublicationCandidate,
    UploadPublicationConcurrencyConflictError,
    UploadPublicationNotFoundError,
    UploadPublicationRepository,
)
from kronika.application.upload_transport import default_now_ms
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.upload_publications import (
    UploadPublicationCleanupState,
    UploadPublicationState,
)
from kronika.domain.media_metadata import (
    CanonicalTagKey,
    MediaDescription,
    MediaDisplayTitle,
    MediaMetadata,
)
from kronika.domain.uploads import UploadSessionId, UploadSessionState


class UploadCatalogError(RuntimeError):
    """Sanitized catalog-from-publication failure."""


@dataclass(frozen=True, slots=True)
class CatalogUploadClassification:
    """Sparse catalog defaults applied when an upload is first cataloged."""

    content_category: ContentCategory
    acquisition_source: AcquisitionSource
    display_title: MediaDisplayTitle | None = None
    creator_attribution_kind: CreatorAttributionKind | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None
    creator_display_name: str | None = None
    description: MediaDescription | None = None
    tag_keys: tuple[CanonicalTagKey, ...] = ()


class UploadCatalogNotFoundError(UploadCatalogError):
    """Raised when durable publication provenance is absent."""


class UploadCatalogStateError(UploadCatalogError):
    """Raised when upload or publication state cannot be cataloged."""


class UploadCatalogInconsistencyServiceError(UploadCatalogError):
    """Raised when durable state and catalog linkage disagree."""


class UploadCatalogInfrastructureError(UploadCatalogError):
    """Raised for sanitized persistence failures during cataloging."""


@dataclass(frozen=True, slots=True)
class UploadCatalogResult:
    """Public-safe outcome of one catalog transition attempt."""

    upload_id: str
    state: str
    media_id: str | None
    media_location_id: str | None = None


class CatalogPublishedUpload:
    """Create catalog rows from verified published provenance under caller lock."""

    def __init__(
        self,
        repository: UploadPublicationRepository,
        *,
        media_id_factory: Callable[[], MediaId] | None = None,
        location_id_factory: Callable[[], MediaLocationId] | None = None,
        classification_for_upload: Callable[
            [UploadSessionId],
            CatalogUploadClassification | None,
        ]
        | None = None,
        now_ms: Callable[[], int] = default_now_ms,
    ) -> None:
        self._repository = repository
        self._media_id_factory = media_id_factory or MediaId.new
        self._location_id_factory = location_id_factory or MediaLocationId.new
        self._classification_for_upload = classification_for_upload
        self._now_ms = now_ms

    def catalog_owned_blocking(
        self,
        upload_id: UploadSessionId,
    ) -> UploadCatalogResult:
        """Catalog one published upload while the caller owns its process-local lock."""
        try:
            candidate = self._repository.get_candidate(upload_id)
            if candidate is None:
                raise UploadCatalogNotFoundError("upload catalog candidate not found")
            publication = candidate.publication
            if publication is None:
                raise UploadCatalogNotFoundError("upload catalog candidate not found")
            if (publication.media_id is None) != (publication.media_location_id is None):
                raise UploadCatalogInconsistencyServiceError(
                    "upload catalog linkage inconsistent"
                )
            if (
                candidate.upload.state is UploadSessionState.CATALOGED
                and publication.media_id is not None
                and publication.media_location_id is not None
            ):
                return _result(candidate)
            if (
                candidate.upload.state is UploadSessionState.CATALOGED
                or publication.media_id is not None
                or publication.media_location_id is not None
            ):
                raise UploadCatalogInconsistencyServiceError(
                    "upload catalog linkage inconsistent"
                )
            if (
                candidate.upload.state is not UploadSessionState.PUBLISHED
                or publication.state is not UploadPublicationState.VERIFIED
                or publication.cleanup_state
                is not UploadPublicationCleanupState.COMPLETE
                or candidate.upload.validated_media_kind is None
            ):
                raise UploadCatalogStateError("upload catalog state conflict")
            now_ms = self._now_ms()
            media = LogicalMedia(
                id=self._media_id_factory(),
                kind=MediaKind(candidate.upload.validated_media_kind.value),
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            location = MediaLocation(
                id=self._location_id_factory(),
                media_id=media.id,
                library_id=publication.destination_id,
                relative_path=MediaRelativePath(publication.relative_path.value),
                availability=MediaLocationAvailability.AVAILABLE,
                observed_size_bytes=publication.expected_size_bytes,
                observed_mtime_ns=None,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            metadata = None
            if self._classification_for_upload is not None:
                classification = self._classification_for_upload(upload_id)
                if classification is not None:
                    metadata = MediaMetadata(
                        media_id=media.id,
                        display_title=classification.display_title,
                        description=classification.description,
                        tag_keys=classification.tag_keys,
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                        content_category=classification.content_category,
                        acquisition_source=classification.acquisition_source,
                        genre_keys=(),
                        creator_attribution_kind=classification.creator_attribution_kind,
                        creator_stable_id=classification.creator_stable_id,
                        creator_handle=classification.creator_handle,
                        creator_display_name=classification.creator_display_name,
                    )
            try:
                commit_arguments = {
                    "media": media,
                    "location": location,
                    "expected_upload_version": candidate.upload.version,
                    "expected_publication_version": publication.version,
                    "updated_at_ms": now_ms,
                }
                if metadata is not None:
                    commit_arguments["metadata"] = metadata
                committed = self._repository.commit_cataloged_publication(
                    upload_id,
                    **commit_arguments,
                )
            except UploadPublicationConcurrencyConflictError:
                current = self._repository.get_candidate(upload_id)
                if (
                    current is not None
                    and current.upload.state is UploadSessionState.CATALOGED
                    and current.publication is not None
                    and current.publication.media_id is not None
                    and current.publication.media_location_id is not None
                ):
                    return _result(current)
                raise
            return _result(committed)
        except UploadCatalogError:
            raise
        except UploadCatalogInconsistencyError as exc:
            raise UploadCatalogInconsistencyServiceError(
                "upload catalog linkage inconsistent"
            ) from exc
        except UploadCatalogStateConflictError as exc:
            raise UploadCatalogStateError("upload catalog state conflict") from exc
        except UploadPublicationNotFoundError as exc:
            raise UploadCatalogNotFoundError(
                "upload catalog candidate not found"
            ) from exc
        except FrameNestUploadPublicationRepositoryError as exc:
            raise UploadCatalogInfrastructureError(
                "upload catalog operation failed"
            ) from exc


def _result(candidate: UploadPublicationCandidate) -> UploadCatalogResult:
    publication = candidate.publication
    media_id = None
    media_location_id = None
    if (
        publication is not None
        and publication.media_id is not None
        and publication.media_location_id is not None
        and candidate.upload.state is UploadSessionState.CATALOGED
    ):
        media_id = publication.media_id.to_string()
        media_location_id = publication.media_location_id.to_string()
    return UploadCatalogResult(
        upload_id=candidate.upload.id.to_string(),
        state=candidate.upload.state.value,
        media_id=media_id,
        media_location_id=media_location_id,
    )
