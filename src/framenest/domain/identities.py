"""Stable typed identifiers for FrameNest domain entities."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Self

INVALID_IDENTITY_MESSAGE = "Invalid Kronika identity."


class FrameNestIdentityError(ValueError):
    """Sanitized error raised when an external identity is invalid."""


@dataclass(frozen=True, slots=True, repr=False)
class _UuidIdentity:
    """Private shared UUID behavior for concrete domain identity types."""

    _value: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self._value, uuid.UUID):
            raise FrameNestIdentityError(INVALID_IDENTITY_MESSAGE)
        if self._value.variant != uuid.RFC_4122 or self._value.version != 4:
            raise FrameNestIdentityError(INVALID_IDENTITY_MESSAGE)

    @classmethod
    def new(cls) -> Self:
        """Generate a new application-owned UUIDv4 identity."""
        return cls(uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        """Parse a canonical lowercase hyphenated UUIDv4 string."""
        if not isinstance(value, str):
            raise FrameNestIdentityError(INVALID_IDENTITY_MESSAGE)
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, TypeError, ValueError):
            raise FrameNestIdentityError(INVALID_IDENTITY_MESSAGE) from None
        if str(parsed) != value:
            raise FrameNestIdentityError(INVALID_IDENTITY_MESSAGE)
        if parsed.variant != uuid.RFC_4122 or parsed.version != 4:
            raise FrameNestIdentityError(INVALID_IDENTITY_MESSAGE)
        return cls(parsed)

    @property
    def value(self) -> uuid.UUID:
        """Return the wrapped standard-library UUID value."""
        return self._value

    def to_string(self) -> str:
        """Serialize this identity as canonical lowercase hyphenated UUID text."""
        return str(self._value)

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self.to_string()}')"

    def __hash__(self) -> int:
        return hash((type(self), self._value))


class MediaId(_UuidIdentity):
    """Stable identity for one logical media item."""

    __slots__ = ()


class MediaLocationId(_UuidIdentity):
    """Stable identity for one physical media location."""

    __slots__ = ()


class MediaByteIdentityId(_UuidIdentity):
    """Stable identity for one canonical exact-byte media identity."""

    __slots__ = ()


class YouTubeAcquisitionClaimId(_UuidIdentity):
    """Stable identity for one durable YouTube acquisition claim."""

    __slots__ = ()


class XPostClaimId(_UuidIdentity):
    """Stable identity for one durable requester-private X post claim."""

    __slots__ = ()


class XAssetId(_UuidIdentity):
    """Stable identity for one durable source X media asset."""

    __slots__ = ()


class DeviceId(_UuidIdentity):
    """Stable identity for one FrameNest device."""

    __slots__ = ()


class LibraryId(_UuidIdentity):
    """Stable identity for one FrameNest library."""

    __slots__ = ()


class StorageVolumeId(_UuidIdentity):
    """Stable identity for one storage volume."""

    __slots__ = ()


class SeriesId(_UuidIdentity):
    """Stable identity for one media series."""

    __slots__ = ()
