"""Pure-domain media metadata and canonical tag values."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from framenest.domain.identities import MediaId
from framenest.domain.media_classification import (
    DEFAULT_ACQUISITION_SOURCE,
    DEFAULT_CONTENT_CATEGORY,
    MAX_MEDIA_GENRES,
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)

INVALID_MEDIA_METADATA_MESSAGE = "Invalid Kronika media metadata."
PROCESSED_COLLECTION_KEY = "processed"
MAX_DISPLAY_TITLE_CODE_POINTS = 240
MAX_TAG_KEY_CODE_POINTS = 64
MAX_TAG_DISPLAY_NAME_CODE_POINTS = 80
MAX_MEDIA_TAGS = 32
MAX_DESCRIPTION_CODE_POINTS = 10_000
MAX_CREATOR_STABLE_ID_CODE_POINTS = 128
MAX_CREATOR_HANDLE_CODE_POINTS = 64
MAX_CREATOR_DISPLAY_NAME_CODE_POINTS = 200

_TAG_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class FrameNestMediaMetadataError(ValueError):
    """Sanitized error raised when media metadata construction is invalid."""


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validate_non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    return value


@dataclass(frozen=True, slots=True)
class CanonicalTagKey:
    """Stable English lowercase ASCII slug for a canonical tag."""

    value: str

    def __init__(self, value: object) -> None:
        if not isinstance(value, str):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if len(value) > MAX_TAG_KEY_CODE_POINTS or _TAG_KEY_PATTERN.fullmatch(value) is None:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CanonicalTagDisplayName:
    """Presentation text for one canonical tag definition."""

    value: str

    def __init__(self, value: object) -> None:
        if not isinstance(value, str):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if _has_control_character(value):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        trimmed = value.strip()
        if (
            not trimmed
            or len(trimmed) > MAX_TAG_DISPLAY_NAME_CODE_POINTS
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        object.__setattr__(self, "value", trimmed)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MediaDisplayTitle:
    """User-editable display title stored separately from physical filenames."""

    value: str

    def __init__(self, value: object) -> None:
        if not isinstance(value, str):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if (
            not value
            or value.strip() != value
            or len(value) > MAX_DISPLAY_TITLE_CODE_POINTS
            or _has_control_character(value)
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MediaDescription:
    """Plain-text description for a logical media item."""

    value: str

    def __init__(self, value: object) -> None:
        if not isinstance(value, str):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if not value:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if value.strip() != value:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if len(value) > MAX_DESCRIPTION_CODE_POINTS:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if _has_forbidden_control_char(value):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


def _has_forbidden_control_char(value: str) -> bool:
    for ch in value:
        if unicodedata.category(ch) == "Cc" and ch != "\n":
            return True
    return False


@dataclass(frozen=True, slots=True)
class MediaCollectionKey:
    """Stable internal key for a built-in system collection."""

    value: str

    def __init__(self, value: object) -> None:
        if not isinstance(value, str):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if value != PROCESSED_COLLECTION_KEY:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CollectionState:
    """Derived collection membership state for one logical media item."""

    collection_key: MediaCollectionKey | None
    processed_at_ms: int | None

    def __post_init__(self) -> None:
        if self.collection_key is None and self.processed_at_ms is not None:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if self.collection_key is not None and self.processed_at_ms is None:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if self.collection_key is not None and not isinstance(self.collection_key, MediaCollectionKey):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if self.processed_at_ms is not None:
            _validate_non_negative_int(self.processed_at_ms)


def derive_collection_state(
    current_collection_key: MediaCollectionKey | None,
    current_processed_at_ms: int | None,
    new_tag_keys: tuple[CanonicalTagKey, ...],
    now_ms: int,
) -> CollectionState:
    """Derive the next collection state from current state and new tag list."""
    has_tags = bool(new_tag_keys)
    if current_collection_key is not None and has_tags:
        return CollectionState(
            collection_key=current_collection_key,
            processed_at_ms=current_processed_at_ms,
        )
    if current_collection_key is None and has_tags:
        return CollectionState(
            collection_key=MediaCollectionKey(PROCESSED_COLLECTION_KEY),
            processed_at_ms=now_ms,
        )
    if current_collection_key is not None and not has_tags:
        return CollectionState(collection_key=None, processed_at_ms=None)
    return CollectionState(collection_key=None, processed_at_ms=None)


@dataclass(frozen=True, slots=True)
class CanonicalTag:
    """One canonical content/organization tag definition."""

    key: CanonicalTagKey
    display_name: CanonicalTagDisplayName
    created_at_ms: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.key, CanonicalTagKey):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if not isinstance(self.display_name, CanonicalTagDisplayName):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        _validate_non_negative_int(self.created_at_ms)
        _validate_non_negative_int(self.updated_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)


def normalize_creator_display_name(value: object | None) -> str | None:
    """Trim, NFC-normalize, and blank-to-NULL a human-facing creator name."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    if _has_control_character(value):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        return None
    if len(normalized) > MAX_CREATOR_DISPLAY_NAME_CODE_POINTS:
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    return normalized


def normalize_creator_stable_id(value: object | None) -> str | None:
    """Trim and blank-to-NULL a platform stable ID without rewriting case."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    if _has_control_character(value):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_CREATOR_STABLE_ID_CODE_POINTS:
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    return trimmed


def normalize_creator_handle(value: object | None) -> str | None:
    """Trim, strip leading ``@``, lowercase, and blank-to-NULL a creator handle."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    if _has_control_character(value):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    trimmed = value.strip()
    while trimmed.startswith("@"):
        trimmed = trimmed[1:].lstrip()
    normalized = trimmed.lower()
    if not normalized:
        return None
    if len(normalized) > MAX_CREATOR_HANDLE_CODE_POINTS:
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    return normalized


def validate_creator_attribution_fields(
    kind: CreatorAttributionKind | None,
    stable_id: str | None,
    handle: str | None,
    display_name: str | None,
) -> tuple[CreatorAttributionKind | None, str | None, str | None, str | None]:
    """Normalize and validate creator attribution field combinations."""
    if kind is not None and not isinstance(kind, CreatorAttributionKind):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    normalized_stable = normalize_creator_stable_id(stable_id)
    normalized_handle = normalize_creator_handle(handle)
    normalized_display = normalize_creator_display_name(display_name)
    if kind is None:
        if (
            normalized_stable is not None
            or normalized_handle is not None
            or normalized_display is not None
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        return None, None, None, None
    if (
        normalized_stable is None
        and normalized_handle is None
        and normalized_display is None
    ):
        raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
    return kind, normalized_stable, normalized_handle, normalized_display


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Persisted user metadata for one logical media item."""

    media_id: MediaId
    display_title: MediaDisplayTitle | None
    description: MediaDescription | None
    tag_keys: tuple[CanonicalTagKey, ...]
    created_at_ms: int
    updated_at_ms: int
    content_category: ContentCategory = DEFAULT_CONTENT_CATEGORY
    acquisition_source: AcquisitionSource = DEFAULT_ACQUISITION_SOURCE
    genre_keys: tuple[MovieGenre, ...] = ()
    creator_attribution_kind: CreatorAttributionKind | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None
    creator_display_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.media_id, MediaId):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if self.display_title is not None and not isinstance(
            self.display_title,
            MediaDisplayTitle,
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if self.description is not None and not isinstance(
            self.description,
            MediaDescription,
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if not isinstance(self.tag_keys, tuple) or any(
            not isinstance(key, CanonicalTagKey) for key in self.tag_keys
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if len(self.tag_keys) > MAX_MEDIA_TAGS or len(set(self.tag_keys)) != len(self.tag_keys):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if not isinstance(self.content_category, ContentCategory):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if not isinstance(self.acquisition_source, AcquisitionSource):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if not isinstance(self.genre_keys, tuple) or any(
            not isinstance(genre, MovieGenre) for genre in self.genre_keys
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if len(self.genre_keys) > MAX_MEDIA_GENRES or len(set(self.genre_keys)) != len(
            self.genre_keys
        ):
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        if self.genre_keys and self.content_category is not ContentCategory.MOVIE:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)
        kind, stable_id, handle, display_name = validate_creator_attribution_fields(
            self.creator_attribution_kind,
            self.creator_stable_id,
            self.creator_handle,
            self.creator_display_name,
        )
        object.__setattr__(self, "creator_attribution_kind", kind)
        object.__setattr__(self, "creator_stable_id", stable_id)
        object.__setattr__(self, "creator_handle", handle)
        object.__setattr__(self, "creator_display_name", display_name)
        _validate_non_negative_int(self.created_at_ms)
        _validate_non_negative_int(self.updated_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestMediaMetadataError(INVALID_MEDIA_METADATA_MESSAGE)


def normalize_genres_for_category(
    content_category: ContentCategory,
    genre_keys: tuple[MovieGenre, ...],
) -> tuple[MovieGenre, ...]:
    """Drop genres when the item is not categorized as a movie."""
    if content_category is not ContentCategory.MOVIE:
        return ()
    return genre_keys


def default_classification_fields() -> tuple[ContentCategory, AcquisitionSource, tuple[MovieGenre, ...]]:
    """Neutral defaults for rows that have never been classified."""
    return DEFAULT_CONTENT_CATEGORY, DEFAULT_ACQUISITION_SOURCE, ()
