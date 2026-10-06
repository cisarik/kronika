"""Deterministic portable media sidecar v1 schema and codec."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import NoReturn

from kronika.domain.identities import (
    FrameNestIdentityError,
    LibraryId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.media import FrameNestMediaRelativePathError, MediaKind, MediaRelativePath
from kronika.domain.media_classification import (
    MAX_MEDIA_GENRES,
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)
from kronika.domain.media_metadata import (
    MAX_MEDIA_TAGS,
    CanonicalTagDisplayName,
    CanonicalTagKey,
    FrameNestMediaMetadataError,
    MediaCollectionKey,
    MediaDescription,
    MediaDisplayTitle,
    validate_creator_attribution_fields,
)

SIDECAR_FORMAT = "kronika-media-sidecar"
#: The format spelling written before the durable-writer cut. Readers keep
#: accepting it, and the accepted set must name it explicitly: a literal
#: ``frozenset`` of only the writer spelling would silently drop it.
COMPATIBLE_SIDECAR_FORMAT = "framenest-media-sidecar"
ACCEPTED_SIDECAR_FORMATS = frozenset({SIDECAR_FORMAT, COMPATIBLE_SIDECAR_FORMAT})
SIDECAR_SCHEMA_VERSION = 1
MAX_SIDECAR_BYTES = 256 * 1024

_MALFORMED_MESSAGE = "Media sidecar is malformed."
_UNSUPPORTED_MESSAGE = "Media sidecar is unsupported."
_ERROR_MALFORMED = "SIDECAR_MALFORMED"
_ERROR_UNSUPPORTED = "SIDECAR_UNSUPPORTED"
_UTF8_BOM = b"\xef\xbb\xbf"

_ROOT_FIELDS = frozenset(
    {
        "format",
        "schema_version",
        "media_id",
        "media_kind",
        "display_title",
        "description",
        "tag_keys",
        "tag_definitions",
        "content_category",
        "acquisition_source",
        "genre_keys",
        "creator_attribution_kind",
        "creator_stable_id",
        "creator_handle",
        "creator_display_name",
        "processed",
        "created_at_ms",
        "updated_at_ms",
        "location",
    }
)
_LOCATION_FIELDS = frozenset({"location_id", "library_id", "relative_path"})
_TAG_DEFINITION_FIELDS = frozenset({"key", "display_name"})
_PROCESSED_FIELDS = frozenset({"collection_key", "processed_at_ms"})


class FrameNestMediaSidecarError(ValueError):
    """Sanitized error raised when sidecar encoding or decoding fails."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _malformed() -> NoReturn:
    raise FrameNestMediaSidecarError(_MALFORMED_MESSAGE, error_code=_ERROR_MALFORMED)


def _unsupported() -> NoReturn:
    raise FrameNestMediaSidecarError(_UNSUPPORTED_MESSAGE, error_code=_ERROR_UNSUPPORTED)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class SidecarTagDefinition:
    """One canonical tag definition projected into a sidecar document."""

    key: CanonicalTagKey
    display_name: CanonicalTagDisplayName

    def __post_init__(self) -> None:
        if not isinstance(self.key, CanonicalTagKey):
            _malformed()
        if not isinstance(self.display_name, CanonicalTagDisplayName):
            _malformed()


@dataclass(frozen=True, slots=True)
class SidecarProcessedState:
    """Built-in processed-collection membership projected into a sidecar."""

    collection_key: MediaCollectionKey
    processed_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.collection_key, MediaCollectionKey):
            _malformed()
        if not _is_int(self.processed_at_ms) or self.processed_at_ms < 0:
            _malformed()


@dataclass(frozen=True, slots=True)
class SidecarLocation:
    """One explicit physical location projected into a sidecar document."""

    location_id: MediaLocationId
    library_id: LibraryId
    relative_path: MediaRelativePath

    def __post_init__(self) -> None:
        if not isinstance(self.location_id, MediaLocationId):
            _malformed()
        if not isinstance(self.library_id, LibraryId):
            _malformed()
        if not isinstance(self.relative_path, MediaRelativePath):
            _malformed()


@dataclass(frozen=True, slots=True)
class SidecarDocument:
    """Closed portable media sidecar v1 document."""

    media_id: MediaId
    media_kind: MediaKind
    display_title: MediaDisplayTitle | None
    description: MediaDescription | None
    tag_keys: tuple[CanonicalTagKey, ...]
    tag_definitions: tuple[SidecarTagDefinition, ...]
    content_category: ContentCategory
    acquisition_source: AcquisitionSource
    genre_keys: tuple[MovieGenre, ...]
    creator_attribution_kind: CreatorAttributionKind | None
    creator_stable_id: str | None
    creator_handle: str | None
    creator_display_name: str | None
    processed: SidecarProcessedState | None
    created_at_ms: int | None
    updated_at_ms: int | None
    location: SidecarLocation
    format: str = SIDECAR_FORMAT
    schema_version: int = SIDECAR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.format not in ACCEPTED_SIDECAR_FORMATS or not isinstance(self.format, str):
            _malformed()
        if self.schema_version != SIDECAR_SCHEMA_VERSION or not _is_int(self.schema_version):
            _malformed()
        if not isinstance(self.media_id, MediaId):
            _malformed()
        if not isinstance(self.media_kind, MediaKind):
            _malformed()
        if self.display_title is not None and not isinstance(self.display_title, MediaDisplayTitle):
            _malformed()
        if self.description is not None and not isinstance(self.description, MediaDescription):
            _malformed()
        if not isinstance(self.tag_keys, tuple) or any(
            not isinstance(key, CanonicalTagKey) for key in self.tag_keys
        ):
            _malformed()
        if not isinstance(self.tag_definitions, tuple) or any(
            not isinstance(item, SidecarTagDefinition) for item in self.tag_definitions
        ):
            _malformed()
        if len(self.tag_keys) > MAX_MEDIA_TAGS or len(set(self.tag_keys)) != len(self.tag_keys):
            _malformed()
        if len(self.tag_keys) != len(self.tag_definitions):
            _malformed()
        if any(item.key != key for item, key in zip(self.tag_definitions, self.tag_keys, strict=True)):
            _malformed()
        if not isinstance(self.content_category, ContentCategory):
            _malformed()
        if not isinstance(self.acquisition_source, AcquisitionSource):
            _malformed()
        if not isinstance(self.genre_keys, tuple) or any(
            not isinstance(genre, MovieGenre) for genre in self.genre_keys
        ):
            _malformed()
        if len(self.genre_keys) > MAX_MEDIA_GENRES or len(set(self.genre_keys)) != len(self.genre_keys):
            _malformed()
        if self.genre_keys and self.content_category is not ContentCategory.MOVIE:
            _malformed()
        try:
            kind, stable_id, handle, display_name = validate_creator_attribution_fields(
                self.creator_attribution_kind,
                self.creator_stable_id,
                self.creator_handle,
                self.creator_display_name,
            )
        except FrameNestMediaMetadataError:
            _malformed()
        object.__setattr__(self, "creator_attribution_kind", kind)
        object.__setattr__(self, "creator_stable_id", stable_id)
        object.__setattr__(self, "creator_handle", handle)
        object.__setattr__(self, "creator_display_name", display_name)
        if self.processed is not None and not isinstance(self.processed, SidecarProcessedState):
            _malformed()
        object.__setattr__(self, "created_at_ms", _optional_non_negative_int(self.created_at_ms))
        object.__setattr__(self, "updated_at_ms", _optional_non_negative_int(self.updated_at_ms))
        if (self.created_at_ms is None) != (self.updated_at_ms is None):
            _malformed()
        if (
            self.created_at_ms is not None
            and self.updated_at_ms is not None
            and self.updated_at_ms < self.created_at_ms
        ):
            _malformed()
        if not isinstance(self.location, SidecarLocation):
            _malformed()


def encode_media_sidecar(document: SidecarDocument) -> bytes:
    """Serialize one validated sidecar document to canonical v1 bytes."""
    if not isinstance(document, SidecarDocument):
        _malformed()
    payload = {
        "format": SIDECAR_FORMAT,
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "media_id": document.media_id.to_string(),
        "media_kind": document.media_kind.value,
        "display_title": None if document.display_title is None else document.display_title.value,
        "description": None if document.description is None else document.description.value,
        "tag_keys": [key.value for key in document.tag_keys],
        "tag_definitions": [
            {"key": item.key.value, "display_name": item.display_name.value}
            for item in document.tag_definitions
        ],
        "content_category": document.content_category.value,
        "acquisition_source": document.acquisition_source.value,
        "genre_keys": [genre.value for genre in document.genre_keys],
        "creator_attribution_kind": (
            None
            if document.creator_attribution_kind is None
            else document.creator_attribution_kind.value
        ),
        "creator_stable_id": document.creator_stable_id,
        "creator_handle": document.creator_handle,
        "creator_display_name": document.creator_display_name,
        "processed": None
        if document.processed is None
        else {
            "collection_key": document.processed.collection_key.value,
            "processed_at_ms": document.processed.processed_at_ms,
        },
        "created_at_ms": document.created_at_ms,
        "updated_at_ms": document.updated_at_ms,
        "location": {
            "location_id": document.location.location_id.to_string(),
            "library_id": document.location.library_id.to_string(),
            "relative_path": document.location.relative_path.value,
        },
    }
    try:
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError):
        _malformed()
    if len(encoded) > MAX_SIDECAR_BYTES:
        _malformed()
    return encoded


def decode_media_sidecar(payload: bytes) -> SidecarDocument:
    """Parse canonical or canonicalizable v1 sidecar bytes into a document."""
    if not isinstance(payload, bytes):
        _malformed()
    if not payload:
        _malformed()
    if len(payload) > MAX_SIDECAR_BYTES:
        _malformed()
    if payload.startswith(_UTF8_BOM):
        _malformed()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        _malformed()
    try:
        parsed: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except FrameNestMediaSidecarError:
        raise
    except json.JSONDecodeError:
        _malformed()
    if not isinstance(parsed, dict):
        _malformed()
    _reject_unsupported_identity(parsed)
    mapping = _require_closed_object(parsed, _ROOT_FIELDS)
    if mapping["format"] not in ACCEPTED_SIDECAR_FORMATS or not isinstance(mapping["format"], str):
        _malformed()
    if mapping["schema_version"] != SIDECAR_SCHEMA_VERSION or not _is_int(mapping["schema_version"]):
        _malformed()
    tag_keys = _parse_tag_keys(mapping["tag_keys"])
    tag_definitions = _parse_tag_definitions(mapping["tag_definitions"])
    _require_definition_alignment(tag_keys, tag_definitions)
    content_category = _parse_enum(mapping["content_category"], ContentCategory)
    return SidecarDocument(
        media_id=_parse_identity(mapping["media_id"], MediaId),
        media_kind=_parse_enum(mapping["media_kind"], MediaKind),
        display_title=_parse_optional_title(mapping["display_title"]),
        description=_parse_optional_description(mapping["description"]),
        tag_keys=tag_keys,
        tag_definitions=tag_definitions,
        content_category=content_category,
        acquisition_source=_parse_enum(mapping["acquisition_source"], AcquisitionSource),
        genre_keys=_parse_genres(mapping["genre_keys"], content_category),
        creator_attribution_kind=_parse_optional_creator_kind(mapping["creator_attribution_kind"]),
        creator_stable_id=_parse_optional_creator_text(mapping["creator_stable_id"]),
        creator_handle=_parse_optional_creator_text(mapping["creator_handle"]),
        creator_display_name=_parse_optional_creator_text(mapping["creator_display_name"]),
        processed=_parse_processed(mapping["processed"]),
        created_at_ms=_optional_non_negative_int(mapping["created_at_ms"]),
        updated_at_ms=_optional_non_negative_int(mapping["updated_at_ms"]),
        location=_parse_location(mapping["location"]),
    )


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    if not _is_int(value) or value < 0:
        _malformed()
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for key, value in pairs:
        if key in mapping:
            _malformed()
        mapping[key] = value
    return mapping


def _reject_nonfinite(_value: str) -> None:
    _malformed()


def _reject_unsupported_identity(payload: dict[str, object]) -> None:
    fmt = payload.get("format")
    version = payload.get("schema_version")
    if isinstance(fmt, str) and fmt not in ACCEPTED_SIDECAR_FORMATS:
        _unsupported()
    if _is_int(version) and version != SIDECAR_SCHEMA_VERSION:
        _unsupported()


def _require_closed_object(value: object, expected: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        _malformed()
    return value


def _parse_identity(value: object, identity_type: type[MediaId]) -> MediaId:
    if not isinstance(value, str):
        _malformed()
    try:
        return identity_type.from_string(value)
    except FrameNestIdentityError:
        _malformed()


def _parse_enum(value: object, enum_type: type[MediaKind]) -> MediaKind:
    if not isinstance(value, str):
        _malformed()
    try:
        return enum_type(value)
    except ValueError:
        _malformed()


def _parse_optional_title(value: object) -> MediaDisplayTitle | None:
    if value is None:
        return None
    try:
        return MediaDisplayTitle(value)
    except FrameNestMediaMetadataError:
        _malformed()


def _parse_optional_description(value: object) -> MediaDescription | None:
    if value is None:
        return None
    try:
        return MediaDescription(value)
    except FrameNestMediaMetadataError:
        _malformed()


def _parse_tag_keys(value: object) -> tuple[CanonicalTagKey, ...]:
    if not isinstance(value, list):
        _malformed()
    keys: list[CanonicalTagKey] = []
    for item in value:
        try:
            keys.append(CanonicalTagKey(item))
        except FrameNestMediaMetadataError:
            _malformed()
    parsed = tuple(keys)
    if len(parsed) > MAX_MEDIA_TAGS or len(set(parsed)) != len(parsed):
        _malformed()
    return parsed


def _parse_tag_definitions(value: object) -> tuple[SidecarTagDefinition, ...]:
    if not isinstance(value, list):
        _malformed()
    definitions: list[SidecarTagDefinition] = []
    for item in value:
        mapping = _require_closed_object(item, _TAG_DEFINITION_FIELDS)
        try:
            definitions.append(
                SidecarTagDefinition(
                    key=CanonicalTagKey(mapping["key"]),
                    display_name=CanonicalTagDisplayName(mapping["display_name"]),
                )
            )
        except FrameNestMediaMetadataError:
            _malformed()
    return tuple(definitions)


def _require_definition_alignment(
    tag_keys: tuple[CanonicalTagKey, ...],
    tag_definitions: tuple[SidecarTagDefinition, ...],
) -> None:
    if len(tag_keys) != len(tag_definitions):
        _malformed()
    if any(item.key != key for item, key in zip(tag_definitions, tag_keys, strict=True)):
        _malformed()


def _parse_genres(value: object, content_category: ContentCategory) -> tuple[MovieGenre, ...]:
    if not isinstance(value, list):
        _malformed()
    genres: list[MovieGenre] = []
    for item in value:
        if not isinstance(item, str):
            _malformed()
        try:
            genres.append(MovieGenre(item))
        except ValueError:
            _malformed()
    parsed = tuple(genres)
    if len(parsed) > MAX_MEDIA_GENRES or len(set(parsed)) != len(parsed):
        _malformed()
    if parsed and content_category is not ContentCategory.MOVIE:
        _malformed()
    return parsed


def _parse_optional_creator_kind(value: object) -> CreatorAttributionKind | None:
    if value is None:
        return None
    return _parse_enum(value, CreatorAttributionKind)


def _parse_optional_creator_text(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    _malformed()


def _parse_processed(value: object) -> SidecarProcessedState | None:
    if value is None:
        return None
    mapping = _require_closed_object(value, _PROCESSED_FIELDS)
    try:
        collection_key = MediaCollectionKey(mapping["collection_key"])
    except FrameNestMediaMetadataError:
        _malformed()
    timestamp = mapping["processed_at_ms"]
    if not _is_int(timestamp) or timestamp < 0:
        _malformed()
    return SidecarProcessedState(collection_key=collection_key, processed_at_ms=timestamp)


def _parse_location(value: object) -> SidecarLocation:
    mapping = _require_closed_object(value, _LOCATION_FIELDS)
    try:
        relative_path = MediaRelativePath(mapping["relative_path"])
    except FrameNestMediaRelativePathError:
        _malformed()
    return SidecarLocation(
        location_id=_parse_identity(mapping["location_id"], MediaLocationId),
        library_id=_parse_identity(mapping["library_id"], LibraryId),
        relative_path=relative_path,
    )
