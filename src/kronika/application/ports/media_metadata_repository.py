"""Application port for persistent media metadata and canonical tags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from kronika.domain import MediaId
from kronika.domain.media_classification import (
    DEFAULT_ACQUISITION_SOURCE,
    DEFAULT_CONTENT_CATEGORY,
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
    CollectionState,
    MediaCollectionKey,
    MediaDescription,
    MediaDisplayTitle,
)


class CanonicalTagDefinitionConflictError(RuntimeError):
    """Raised when an existing tag key has a different display name."""


class CanonicalTagNotFoundError(RuntimeError):
    """Raised when metadata references an absent canonical tag."""


class MediaMetadataMediaNotFoundError(RuntimeError):
    """Raised when a logical media item is absent."""


class AcquisitionSourceImmutableError(RuntimeError):
    """Raised when metadata Save attempts to change acquisition provenance."""


class SourceDerivedMetadataImmutableError(RuntimeError):
    """Raised when metadata Save attempts to change X source-derived values."""


class _Omitted:
    """Marker distinguishing an omitted protected field from an explicit clear."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "OMITTED"


OMITTED = _Omitted()


class FrameNestMediaMetadataRepositoryError(RuntimeError):
    """Sanitized error raised when media metadata persistence fails."""


@dataclass(frozen=True, slots=True)
class CanonicalTagCreateResult:
    """Result from creating a canonical tag definition."""

    status: Literal["created", "already_exists"]
    tag: CanonicalTag


@dataclass(frozen=True, slots=True)
class MediaMetadataSnapshot:
    """Application-facing metadata state for one media item."""

    media_id: MediaId
    persisted: bool
    display_title: MediaDisplayTitle | None
    description: MediaDescription | None
    tag_keys: tuple[CanonicalTagKey, ...]
    collection_key: MediaCollectionKey | None
    processed_at_ms: int | None
    created_at_ms: int | None
    updated_at_ms: int | None
    content_category: ContentCategory = DEFAULT_CONTENT_CATEGORY
    acquisition_source: AcquisitionSource = DEFAULT_ACQUISITION_SOURCE
    genre_keys: tuple[MovieGenre, ...] = ()
    creator_attribution_kind: CreatorAttributionKind | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None
    creator_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class MediaMetadataSaveResult:
    """Result from saving metadata for one media item."""

    status: Literal["created", "updated", "unchanged"]
    metadata: MediaMetadataSnapshot


class MediaMetadataRepository(Protocol):
    """Persistence-independent media metadata contract."""

    def create_canonical_tag(
        self,
        key: CanonicalTagKey,
        display_name: CanonicalTagDisplayName,
        now_ms: int,
    ) -> CanonicalTagCreateResult:
        """Create one tag definition idempotently."""

    def list_canonical_tags(self) -> tuple[CanonicalTag, ...]:
        """Return canonical tags in deterministic order."""

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        """Return one canonical tag definition, or None."""

    def get_media_metadata(self, media_id: MediaId) -> MediaMetadataSnapshot:
        """Return persisted or empty metadata for an existing media item."""

    def save_media_metadata(
        self,
        media_id: MediaId,
        display_title: MediaDisplayTitle | None,
        description: MediaDescription | None,
        tag_keys: tuple[CanonicalTagKey, ...],
        now_ms: int,
        *,
        content_category: ContentCategory | None | object = OMITTED,
        acquisition_source: AcquisitionSource | None = None,
        genre_keys: tuple[MovieGenre, ...] = (),
        creator_attribution_kind: CreatorAttributionKind | None | object = OMITTED,
        creator_stable_id: str | None | object = OMITTED,
        creator_handle: str | None | object = OMITTED,
        creator_display_name: str | None | object = OMITTED,
    ) -> MediaMetadataSaveResult:
        """Persist a complete metadata replacement atomically, deriving collection state from tag list.

        When ``acquisition_source`` is ``None``, the existing stored provenance is
        preserved. A provided value equal to the stored value is accepted. A
        different provided value raises ``AcquisitionSourceImmutableError``.

        For X creator provenance (``x_manual_claim``), ``OMITTED`` preserves
        the existing value, an identical value is a compatible no-op, a
        different value raises ``SourceDerivedMetadataImmutableError``, and an
        explicit clear (``None`` over a present value) is rejected. Canonical
        ``content_category`` remains administrator-correctable for X media.
        """
