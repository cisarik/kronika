"""Application query boundary for searchable media catalog browsing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import unicodedata

from kronika.application.ports.media_catalog_repository import (
    CatalogMediaItem,
    MediaCatalogPage,
    MediaCatalogQuery,
    MediaCatalogRepository,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identities import MediaId
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from kronika.domain.record_access import AccessScopeKind, RecordAccessScope
from kronika.domain.media_metadata import (
    CanonicalTagKey,
    FrameNestMediaMetadataError,
    MediaCollectionKey,
    normalize_creator_handle,
    normalize_creator_stable_id,
)

MEDIA_CATALOG_QUERY_INVALID_MESSAGE = "Invalid media catalog query."
MAX_MEDIA_CATALOG_QUERY_CODE_POINTS = 240
DEFAULT_MEDIA_CATALOG_LIMIT = 24
MAX_MEDIA_CATALOG_LIMIT = 100


class MediaCatalogValidationError(ValueError):
    """Raised when catalog query input is invalid."""


@dataclass(frozen=True, slots=True)
class ListMediaCatalog:
    """List persisted logical media through a normalized read query."""

    repository: MediaCatalogRepository
    cover_states: Callable[[tuple[str, ...]], dict[str, bool]] | None = None

    def execute(
        self,
        *,
        q: str | None = None,
        tag_keys: list[str] | tuple[str, ...] | None = None,
        limit: int = DEFAULT_MEDIA_CATALOG_LIMIT,
        offset: int = 0,
        collection_key: MediaCollectionKey | None = None,
        content_category: str | None = None,
        acquisition_source: str | None = None,
        creator_attribution_kind: str | None = None,
        creator_stable_id: str | None = None,
        creator_handle: str | None = None,
        access_scope: RecordAccessScope | None = None,
    ) -> MediaCatalogPage:
        creator_kind, creator_sid, creator_h = _normalize_creator_filter(
            creator_attribution_kind,
            creator_stable_id,
            creator_handle,
        )
        query = MediaCatalogQuery(
            q=_normalize_title_query(q),
            tag_keys=_normalize_tag_keys(tag_keys or []),
            limit=_validate_limit(limit),
            offset=_validate_offset(offset),
            collection_key=collection_key,
            content_category=_normalize_content_category(content_category),
            acquisition_source=_normalize_acquisition_source(acquisition_source),
            creator_attribution_kind=creator_kind,
            creator_stable_id=creator_sid,
            creator_handle=creator_h,
            published_only=True,
            access_scope=access_scope,
        )
        if access_scope is None or not access_scope.allows_queries:
            return MediaCatalogPage(
                items=(),
                total=0,
                limit=query.limit,
                offset=query.offset,
                q=query.q,
                tag_keys=query.tag_keys,
                content_category=query.content_category,
                acquisition_source=query.acquisition_source,
                creator_attribution_kind=query.creator_attribution_kind,
                creator_stable_id=query.creator_stable_id,
                creator_handle=query.creator_handle,
            )
        page = self.repository.list_media(query)
        if self.cover_states is None or not page.items:
            return page
        media_ids = tuple(
            item.media_id for item in page.items if item.read_projection != "approved"
        )
        states = self.cover_states(media_ids) if media_ids else {}
        items = tuple(
            item
            if item.read_projection == "approved"
            else replace(item, cover_ready=states.get(item.media_id, False))
            for item in page.items
        )
        return replace(page, items=items)


@dataclass(frozen=True, slots=True)
class GetMediaCatalogItem:
    """Load one catalog-shaped media item for audience-gated Details hydration."""

    repository: MediaCatalogRepository
    cover_states: Callable[[tuple[str, ...]], dict[str, bool]] | None = None

    def execute(self, media_id: str) -> CatalogMediaItem | None:
        try:
            MediaId.from_string(media_id)
        except FrameNestIdentityError as exc:
            raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc
        item = self.repository.get_media_item(media_id)
        if item is None:
            return None
        if self.cover_states is None:
            return item
        states = self.cover_states((item.media_id,))
        return replace(item, cover_ready=states.get(item.media_id, False))

    def execute_approved(self, media_id: str) -> CatalogMediaItem | None:
        """Load the approved snapshot without consulting the current cover."""
        try:
            MediaId.from_string(media_id)
        except FrameNestIdentityError as exc:
            raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc
        loader = getattr(self.repository, "approved_catalog_item", None)
        if not callable(loader):
            return None
        return loader(media_id)


def _normalize_title_query(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE)
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_MEDIA_CATALOG_QUERY_CODE_POINTS or _has_control_character(normalized):
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE)
    return normalized


def _normalize_tag_keys(values: list[str] | tuple[str, ...]) -> tuple[CanonicalTagKey, ...]:
    normalized: list[CanonicalTagKey] = []
    seen: set[CanonicalTagKey] = set()
    try:
        for value in values:
            key = CanonicalTagKey(value)
            if key not in seen:
                seen.add(key)
                normalized.append(key)
    except FrameNestMediaMetadataError as exc:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc
    return tuple(normalized)


def _normalize_content_category(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ContentCategory(value).value
    except ValueError as exc:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc


def _normalize_acquisition_source(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return AcquisitionSource(value).value
    except ValueError as exc:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc


def _normalize_creator_filter(
    kind: str | None,
    stable_id: str | None,
    handle: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Prefer kind+stable_id; otherwise kind+handle. Reject display-name-only."""
    if kind is None and stable_id is None and handle is None:
        return None, None, None
    if kind is None:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE)
    try:
        parsed_kind = CreatorAttributionKind(kind).value
    except ValueError as exc:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc
    try:
        normalized_stable = normalize_creator_stable_id(stable_id)
        normalized_handle = normalize_creator_handle(handle)
    except FrameNestMediaMetadataError as exc:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE) from exc
    if normalized_stable is not None:
        return parsed_kind, normalized_stable, None
    if normalized_handle is not None:
        return parsed_kind, None, normalized_handle
    raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE)


def _validate_limit(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_MEDIA_CATALOG_LIMIT
    ):
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE)
    return value


def _validate_offset(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MediaCatalogValidationError(MEDIA_CATALOG_QUERY_INVALID_MESSAGE)
    return value


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)
