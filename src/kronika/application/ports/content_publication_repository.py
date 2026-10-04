"""Persistence port for content publication and the admin workflow read model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from kronika.application.ports.media_attribution import MediaContributionAttribution
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaLocation,
    CatalogMediaTag,
)
from kronika.domain.content_publication import (
    ContentPublication,
    ContentPublicationReadiness,
)
from kronika.domain.identities import MediaId

PublicationFilter = Literal["unpublished", "published", "all"]
ReadinessFilter = Literal["all", "ready", "incomplete"]
AnalysisFilter = Literal[
    "all",
    "not_requested",
    "pending",
    "analyzing",
    "analyzed",
    "failed",
]
MetadataState = Literal["incomplete", "complete"]
PublicationState = Literal["unpublished", "published"]


class FrameNestContentPublicationRepositoryError(RuntimeError):
    """Sanitized content-publication persistence failure."""


class ContentPublicationMediaNotFoundError(
    FrameNestContentPublicationRepositoryError
):
    """Raised when the target logical medium is absent."""


class ContentPublicationBoundRecordError(FrameNestContentPublicationRepositoryError):
    """Legacy publication cannot change a bound Kronika record."""


@dataclass(frozen=True, slots=True)
class AdminMediaQuery:
    """Normalized bounded admin workflow query."""

    q: str | None
    tag_keys: tuple[str, ...]
    publication: PublicationFilter
    readiness: ReadinessFilter
    analysis: AnalysisFilter
    limit: int
    offset: int
    contributor: str | None = None


@dataclass(frozen=True, slots=True)
class AdminMediaItem:
    """One catalog-safe item in the administrator publication workflow."""

    media_id: str
    media_kind: str
    created_at_ms: int
    updated_at_ms: int
    display_title: str | None
    description: str | None
    collection_key: str | None
    processed_at_ms: int | None
    content_category: str
    acquisition_source: str
    tags: tuple[CatalogMediaTag, ...]
    locations: tuple[CatalogMediaLocation, ...]
    publication: ContentPublication | None
    readiness: ContentPublicationReadiness
    analysis_state: str
    contributors: tuple[MediaContributionAttribution, ...] = ()


@dataclass(frozen=True, slots=True)
class AdminMediaPage:
    """Deterministic bounded page for the publication workflow."""

    items: tuple[AdminMediaItem, ...]
    total: int
    limit: int
    offset: int
    q: str | None
    tag_keys: tuple[str, ...]
    publication: PublicationFilter
    readiness: ReadinessFilter
    analysis: AnalysisFilter
    contributor: str | None = None


@dataclass(frozen=True, slots=True)
class PublishContentResult:
    """Atomic publication or unpublication decision and current representation."""

    status: Literal[
        "published",
        "already_published",
        "not_ready",
        "unpublished",
        "already_unpublished",
    ]
    publication: ContentPublication | None
    readiness: ContentPublicationReadiness


@dataclass(frozen=True, slots=True)
class MediaWorkflowStatus:
    """Bounded status for one known logical medium."""

    metadata_state: MetadataState
    missing_metadata_fields: tuple[str, ...]
    publication_state: PublicationState


class ContentPublicationRepository(Protocol):
    """Persistence contract for audience reads and explicit publication."""

    def media_exists(self, media_id: MediaId) -> bool:
        """Return whether the logical medium exists."""

    def is_published(self, media_id: MediaId) -> bool:
        """Return whether a publication row exists."""

    def get_media_workflow_status(self, media_id: MediaId) -> MediaWorkflowStatus:
        """Return readiness and publication truth for one logical medium."""

    def list_admin_media(self, query: AdminMediaQuery) -> AdminMediaPage:
        """Return one filtered admin workflow page."""

    def publish(self, media_id: MediaId, published_at_ms: int) -> PublishContentResult:
        """Atomically publish a ready item or return the current decision."""

    def unpublish(self, media_id: MediaId) -> PublishContentResult:
        """Atomically remove the publication row or report already unpublished."""
