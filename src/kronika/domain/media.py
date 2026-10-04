"""Pure-domain logical media and physical location entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath

from kronika.domain.identities import LibraryId, MediaId, MediaLocationId

INVALID_MEDIA_MESSAGE = "Invalid Kronika media."
INVALID_LOCATION_MESSAGE = "Invalid Kronika media location."
INVALID_PATH_MESSAGE = "Invalid Kronika media relative path."
MAX_RELATIVE_PATH_CODE_POINTS = 4096


class FrameNestMediaError(ValueError):
    """Sanitized error raised when logical media construction is invalid."""


class FrameNestMediaLocationError(ValueError):
    """Sanitized error raised when media location construction is invalid."""


class FrameNestMediaRelativePathError(ValueError):
    """Sanitized error raised when a media relative path is invalid."""


class MediaKind(StrEnum):
    """Supported initial logical media kinds."""

    VIDEO = "video"
    ANIMATED_IMAGE = "animated_image"
    IMAGE = "image"


class MediaLocationAvailability(StrEnum):
    """Supported initial physical media-location availability states."""

    AVAILABLE = "available"
    OFFLINE = "offline"
    MISSING = "missing"
    UNVERIFIED = "unverified"
    ARCHIVED = "archived"


def _validate_non_negative_int(
    value: object,
    *,
    error: type[ValueError],
    message: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise error(message)
    return value


def _validate_optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    return _validate_non_negative_int(
        value,
        error=FrameNestMediaLocationError,
        message=INVALID_LOCATION_MESSAGE,
    )


def _normalize_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)
    if "\x00" in value:
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)
    if len(value) > MAX_RELATIVE_PATH_CODE_POINTS:
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)

    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)
    canonical = str(path)
    if canonical != normalized:
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)

    parts = canonical.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise FrameNestMediaRelativePathError(INVALID_PATH_MESSAGE)
    return canonical


@dataclass(frozen=True, slots=True)
class MediaRelativePath:
    """Portable slash-separated path relative to one registered library."""

    value: str

    def __init__(self, value: object) -> None:
        object.__setattr__(self, "value", _normalize_relative_path(value))

    @property
    def filename(self) -> str:
        """Return the final path component."""
        return self.value.rsplit("/", maxsplit=1)[-1]

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class LogicalMedia:
    """One conceptual media item independent of physical byte locations."""

    id: MediaId
    kind: MediaKind
    created_at_ms: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, MediaId):
            raise FrameNestMediaError(INVALID_MEDIA_MESSAGE)
        if not isinstance(self.kind, MediaKind):
            raise FrameNestMediaError(INVALID_MEDIA_MESSAGE)
        _validate_non_negative_int(
            self.created_at_ms,
            error=FrameNestMediaError,
            message=INVALID_MEDIA_MESSAGE,
        )
        _validate_non_negative_int(
            self.updated_at_ms,
            error=FrameNestMediaError,
            message=INVALID_MEDIA_MESSAGE,
        )


@dataclass(frozen=True, slots=True)
class MediaLocation:
    """One known library-relative physical location for logical media."""

    id: MediaLocationId
    media_id: MediaId
    library_id: LibraryId
    relative_path: MediaRelativePath
    availability: MediaLocationAvailability
    observed_size_bytes: int | None
    observed_mtime_ns: int | None
    created_at_ms: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, MediaLocationId):
            raise FrameNestMediaLocationError(INVALID_LOCATION_MESSAGE)
        if not isinstance(self.media_id, MediaId):
            raise FrameNestMediaLocationError(INVALID_LOCATION_MESSAGE)
        if not isinstance(self.library_id, LibraryId):
            raise FrameNestMediaLocationError(INVALID_LOCATION_MESSAGE)
        if not isinstance(self.relative_path, MediaRelativePath):
            raise FrameNestMediaLocationError(INVALID_LOCATION_MESSAGE)
        if not isinstance(self.availability, MediaLocationAvailability):
            raise FrameNestMediaLocationError(INVALID_LOCATION_MESSAGE)
        object.__setattr__(
            self,
            "observed_size_bytes",
            _validate_optional_non_negative_int(self.observed_size_bytes),
        )
        object.__setattr__(
            self,
            "observed_mtime_ns",
            _validate_optional_non_negative_int(self.observed_mtime_ns),
        )
        _validate_non_negative_int(
            self.created_at_ms,
            error=FrameNestMediaLocationError,
            message=INVALID_LOCATION_MESSAGE,
        )
        _validate_non_negative_int(
            self.updated_at_ms,
            error=FrameNestMediaLocationError,
            message=INVALID_LOCATION_MESSAGE,
        )

    @property
    def filename(self) -> str:
        """Return the current physical filename derived from the relative path."""
        return self.relative_path.filename
