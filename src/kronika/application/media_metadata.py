"""Application use cases for persistent media metadata and canonical tags."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Literal, Protocol

from kronika.application.ports.media_metadata_repository import (
    OMITTED,
    AcquisitionSourceImmutableError,
    CanonicalTagCreateResult,
    CanonicalTagNotFoundError,
    MediaMetadataRepository,
    MediaMetadataSaveResult,
    MediaMetadataSnapshot,
    SourceDerivedMetadataImmutableError,
)
from kronika.domain import MediaId
from kronika.domain.media_classification import (
    DEFAULT_ACQUISITION_SOURCE,
    DEFAULT_CONTENT_CATEGORY,
    MOVIE_GENRE_DISPLAY_NAMES,
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
    MediaDescription,
    MediaDisplayTitle,
    normalize_genres_for_category,
    validate_creator_attribution_fields,
)

MEDIA_METADATA_OPERATION_FAILED_MESSAGE = "Media metadata operation failed."
ACQUISITION_SOURCE_IMMUTABLE_MESSAGE = (
    "Acquisition source is immutable provenance and cannot be changed."
)
SOURCE_DERIVED_IMMUTABLE_MESSAGE = (
    "X source-derived values are immutable provenance and cannot be changed."
)


class ClockMs(Protocol):
    """Callable source of non-negative millisecond timestamps."""

    def __call__(self) -> int:
        """Return current timestamp in milliseconds."""


@dataclass(frozen=True, slots=True)
class CanonicalTagListResult:
    """Deterministic canonical tag list."""

    tags: tuple[CanonicalTag, ...]


@dataclass(frozen=True, slots=True)
class MediaMetadataView:
    """Application-facing complete metadata view."""

    persisted: bool
    display_title: str | None
    description: str | None
    tags: tuple[CanonicalTag, ...]
    collection_key: str | None
    processed_at_ms: int | None
    created_at_ms: int | None
    updated_at_ms: int | None
    content_category: str = DEFAULT_CONTENT_CATEGORY.value
    acquisition_source: str = DEFAULT_ACQUISITION_SOURCE.value
    genres: tuple[str, ...] = ()
    creator_attribution_kind: str | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None
    creator_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class SaveMediaMetadataResult:
    """Application save result with complete metadata view."""

    status: Literal["created", "updated", "unchanged"]
    metadata: MediaMetadataView


class CreateCanonicalTag:
    """Create one canonical tag definition idempotently."""

    def __init__(
        self,
        repository: MediaMetadataRepository,
        *,
        clock_ms: ClockMs | None = None,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms if clock_ms is not None else _utc_now_ms

    def execute(self, key: str, display_name: str) -> CanonicalTagCreateResult:
        return self._repository.create_canonical_tag(
            CanonicalTagKey(key),
            CanonicalTagDisplayName(display_name),
            _call_clock_ms(self._clock_ms),
        )


class ListCanonicalTags:
    """List all canonical tag definitions deterministically."""

    def __init__(self, repository: MediaMetadataRepository) -> None:
        self._repository = repository

    def execute(self) -> CanonicalTagListResult:
        return CanonicalTagListResult(tags=self._repository.list_canonical_tags())


class GetMediaMetadata:
    """Load complete metadata view for one logical media item."""

    def __init__(self, repository: MediaMetadataRepository) -> None:
        self._repository = repository

    def execute(self, media_id: str) -> MediaMetadataView:
        snapshot = self._repository.get_media_metadata(MediaId.from_string(media_id))
        return _view_from_snapshot(self._repository, snapshot)


class SaveMediaMetadata:
    """Persist one complete metadata replacement."""

    def __init__(
        self,
        repository: MediaMetadataRepository,
        *,
        clock_ms: ClockMs | None = None,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms if clock_ms is not None else _utc_now_ms

    def execute(
        self,
        media_id: str,
        display_title: str | None,
        description: str | None,
        tag_keys: list[str],
        *,
        content_category: str | None | object = OMITTED,
        acquisition_source: str | None = None,
        genres: list[str] | None = None,
        creator_attribution_kind: str | None | object = OMITTED,
        creator_stable_id: str | None | object = OMITTED,
        creator_handle: str | None | object = OMITTED,
        creator_display_name: str | None | object = OMITTED,
    ) -> SaveMediaMetadataResult:
        parsed_keys = tuple(CanonicalTagKey(key) for key in tag_keys)
        if len(parsed_keys) != len(set(parsed_keys)):
            raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE)
        parsed_title = None if display_title is None else MediaDisplayTitle(display_title)
        parsed_description = _normalize_description(description)
        if content_category is OMITTED:
            parsed_category = OMITTED
        elif content_category is None:
            parsed_category = None
        else:
            parsed_category = ContentCategory(content_category)
        parsed_source = None
        if acquisition_source is not None:
            parsed_source = AcquisitionSource(acquisition_source)
        parsed_genres = _parse_genres(genres or [])
        if parsed_category not in (None, OMITTED):
            parsed_genres = normalize_genres_for_category(parsed_category, parsed_genres)
        creator_fields = (
            creator_attribution_kind,
            creator_stable_id,
            creator_handle,
            creator_display_name,
        )
        if all(field is OMITTED for field in creator_fields):
            parsed_creator_kind = OMITTED
            parsed_stable_id = OMITTED
            parsed_handle = OMITTED
            parsed_display_name = OMITTED
        else:
            parsed_kind = (
                None
                if creator_attribution_kind is OMITTED or creator_attribution_kind is None
                else CreatorAttributionKind(creator_attribution_kind)
            )
            (
                parsed_creator_kind,
                parsed_stable_id,
                parsed_handle,
                parsed_display_name,
            ) = validate_creator_attribution_fields(
                parsed_kind,
                None if creator_stable_id is OMITTED else creator_stable_id,
                None if creator_handle is OMITTED else creator_handle,
                None if creator_display_name is OMITTED else creator_display_name,
            )
        result = self._repository.save_media_metadata(
            MediaId.from_string(media_id),
            parsed_title,
            parsed_description,
            parsed_keys,
            _call_clock_ms(self._clock_ms),
            content_category=parsed_category,
            acquisition_source=parsed_source,
            genre_keys=parsed_genres,
            creator_attribution_kind=parsed_creator_kind,
            creator_stable_id=parsed_stable_id,
            creator_handle=parsed_handle,
            creator_display_name=parsed_display_name,
        )
        return SaveMediaMetadataResult(
            status=result.status,
            metadata=_view_from_snapshot(self._repository, result.metadata),
        )


def _normalize_description(value: str | None) -> MediaDescription | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return MediaDescription(value)


def _parse_genres(values: list[str]) -> tuple[MovieGenre, ...]:
    display_to_genre = {
        display.casefold(): genre for genre, display in MOVIE_GENRE_DISPLAY_NAMES.items()
    }
    parsed: list[MovieGenre] = []
    seen: set[MovieGenre] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE)
        folded = value.strip().casefold()
        try:
            genre = MovieGenre(folded)
        except ValueError:
            genre = display_to_genre.get(folded)
            if genre is None:
                raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE) from None
        if genre in seen:
            raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE)
        seen.add(genre)
        parsed.append(genre)
    return tuple(parsed)


def _view_from_snapshot(
    repository: MediaMetadataRepository,
    snapshot: MediaMetadataSnapshot,
) -> MediaMetadataView:
    tags: list[CanonicalTag] = []
    for key in snapshot.tag_keys:
        tag = repository.get_canonical_tag(key)
        if tag is None:
            raise CanonicalTagNotFoundError()
        tags.append(tag)
    return MediaMetadataView(
        persisted=snapshot.persisted,
        display_title=None if snapshot.display_title is None else snapshot.display_title.value,
        description=None if snapshot.description is None else snapshot.description.value,
        tags=tuple(tags),
        collection_key=None if snapshot.collection_key is None else snapshot.collection_key.value,
        processed_at_ms=snapshot.processed_at_ms,
        created_at_ms=snapshot.created_at_ms,
        updated_at_ms=snapshot.updated_at_ms,
        content_category=snapshot.content_category.value,
        acquisition_source=snapshot.acquisition_source.value,
        genres=tuple(MOVIE_GENRE_DISPLAY_NAMES[genre] for genre in snapshot.genre_keys),
        creator_attribution_kind=(
            None
            if snapshot.creator_attribution_kind is None
            else snapshot.creator_attribution_kind.value
        ),
        creator_stable_id=snapshot.creator_stable_id,
        creator_handle=snapshot.creator_handle,
        creator_display_name=snapshot.creator_display_name,
    )


def _utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def _call_clock_ms(clock_ms: ClockMs) -> int:
    value = clock_ms()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE)
    return value


__all__ = [
    "ACQUISITION_SOURCE_IMMUTABLE_MESSAGE",
    "AcquisitionSourceImmutableError",
    "CreateCanonicalTag",
    "GetMediaMetadata",
    "ListCanonicalTags",
    "MEDIA_METADATA_OPERATION_FAILED_MESSAGE",
    "MediaMetadataView",
    "OMITTED",
    "SaveMediaMetadata",
    "SaveMediaMetadataResult",
    "SOURCE_DERIVED_IMMUTABLE_MESSAGE",
    "SourceDerivedMetadataImmutableError",
]
