"""Requester-private companion meme picker query boundary."""

from __future__ import annotations

from dataclasses import dataclass

from kronika.application.media_catalog import (
    MEDIA_CATALOG_QUERY_INVALID_MESSAGE,
    MediaCatalogValidationError,
    _normalize_tag_keys,
    _normalize_title_query,
)
from kronika.application.media_content import supported_media_type
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaItem,
    MediaCatalogQuery,
    MediaCatalogRepository,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identities import MediaId
from kronika.domain.media import (
    FrameNestMediaRelativePathError,
    MediaKind,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.media_classification import ContentCategory
from kronika.domain.record_access import AccessScopeKind, RecordAccessScope

COMPANION_API_VERSION = "framenest-companion.v1"
COMPANION_PICKER_QUERY_INVALID_MESSAGE = MEDIA_CATALOG_QUERY_INVALID_MESSAGE
DEFAULT_COMPANION_PICKER_LIMIT = 24
MAX_COMPANION_PICKER_LIMIT = 50
COMPANION_KIND_VALUES = frozenset(
    {MediaKind.IMAGE.value, MediaKind.ANIMATED_IMAGE.value, MediaKind.VIDEO.value}
)


@dataclass(frozen=True, slots=True)
class CompanionPickerLocation:
    """One attachable catalog location exposed to the companion picker."""

    location_id: str
    media_type: str
    observed_size_bytes: int | None


@dataclass(frozen=True, slots=True)
class CompanionPickerItem:
    """One meme the authenticated requester may attach or preview."""

    media_id: str
    media_kind: str
    created_at_ms: int
    display_title: str | None
    tags: tuple[tuple[str, str], ...]
    location: CompanionPickerLocation


@dataclass(frozen=True, slots=True)
class CompanionPickerPage:
    """Cursor page of companion picker items."""

    companion_api_version: str
    items: tuple[CompanionPickerItem, ...]
    next_cursor: str | None
    q: str | None
    tag_keys: tuple[str, ...]
    kind: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class ListCompanionPickerMedia:
    """List published memes plus the caller's own live successful X media."""

    repository: MediaCatalogRepository

    def execute(
        self,
        *,
        login_key: str,
        q: str | None = None,
        tag_keys: list[str] | tuple[str, ...] | None = None,
        kind: str | None = None,
        limit: int = DEFAULT_COMPANION_PICKER_LIMIT,
        cursor: str | None = None,
    ) -> CompanionPickerPage:
        if not isinstance(login_key, str) or not login_key:
            raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE)
        normalized_kind = _normalize_companion_kind(kind)
        cursor_created_at_ms, cursor_media_id = _parse_companion_cursor(cursor)
        query = MediaCatalogQuery(
            q=_normalize_title_query(q),
            tag_keys=_normalize_tag_keys(tag_keys or []),
            limit=_validate_companion_limit(limit),
            offset=0,
            collection_key=None,
            content_category=ContentCategory.MEME.value,
            published_only=False,
            companion_audience_login_key=login_key,
            companion_kinds=(normalized_kind,) if normalized_kind is not None else (),
            cursor_created_at_ms=cursor_created_at_ms,
            cursor_media_id=cursor_media_id,
            access_scope=RecordAccessScope(
                kind=AccessScopeKind.MEMBER,
                owner_login_key=login_key,
            ),
        )
        page = self.repository.list_media(query)
        items = tuple(
            item
            for item in (_to_picker_item(raw) for raw in page.items)
            if item is not None
        )
        return CompanionPickerPage(
            companion_api_version=COMPANION_API_VERSION,
            items=items,
            next_cursor=page.next_cursor,
            q=page.q,
            tag_keys=tuple(key.value for key in page.tag_keys),
            kind=normalized_kind,
            limit=page.limit,
        )


def _to_picker_item(item: CatalogMediaItem) -> CompanionPickerItem | None:
    location = _attachable_location(item)
    if location is None:
        return None
    return CompanionPickerItem(
        media_id=item.media_id,
        media_kind=item.media_kind,
        created_at_ms=item.created_at_ms,
        display_title=item.display_title,
        tags=tuple((tag.key, tag.display_name) for tag in item.tags),
        location=location,
    )


def _attachable_location(item: CatalogMediaItem) -> CompanionPickerLocation | None:
    try:
        kind = MediaKind(item.media_kind)
    except ValueError:
        return None
    for location in item.locations:
        if location.availability != MediaLocationAvailability.AVAILABLE.value:
            continue
        try:
            relative = MediaRelativePath(location.relative_path)
        except FrameNestMediaRelativePathError:
            continue
        filename = relative.filename
        if "." not in filename:
            continue
        extension = "." + filename.rsplit(".", 1)[-1].lower()
        media_type = supported_media_type(kind, extension)
        if media_type is None:
            continue
        return CompanionPickerLocation(
            location_id=location.location_id,
            media_type=media_type,
            observed_size_bytes=location.observed_size_bytes,
        )
    return None


def _normalize_companion_kind(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in COMPANION_KIND_VALUES:
        raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE)
    return value


def _validate_companion_limit(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_COMPANION_PICKER_LIMIT
    ):
        raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE)
    return value


def _parse_companion_cursor(value: str | None) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or ":" not in value:
        raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE)
    created_text, media_id = value.split(":", 1)
    try:
        created_at_ms = int(created_text)
    except (TypeError, ValueError) as exc:
        raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE) from exc
    if created_at_ms < 0 or created_text != str(created_at_ms):
        raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE)
    try:
        MediaId.from_string(media_id)
    except FrameNestIdentityError as exc:
        raise MediaCatalogValidationError(COMPANION_PICKER_QUERY_INVALID_MESSAGE) from exc
    return created_at_ms, media_id
