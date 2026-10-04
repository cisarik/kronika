"""Pure-domain accepted-cover entity and source-observation model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from framenest.domain.identities import FrameNestIdentityError, MediaId, MediaLocationId

INVALID_COVER_MESSAGE = "Invalid Kronika accepted cover."
INVALID_SOURCE_OBSERVATION_MESSAGE = "Invalid Kronika cover source observation."

SOURCE_OBSERVATION_ALGORITHM = "cover-source-observation-v1"
COVER_ARTIFACT_PROFILE = "durable-cover-jpeg-v1"
COVER_ARTIFACT_MEDIA_TYPE = "image/jpeg"
COVER_THUMBNAIL_ALGORITHM = "cover-thumbnail-jpeg-v1"

MAX_SOURCE_REFERENCE_LENGTH = 128
MAX_ALGORITHM_NAME_LENGTH = 64

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REFERENCE_PATTERN = re.compile(r"^location:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MACHINE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,62}$")


TIMELESS_IMAGE_TIMESTAMP_MS = 0


class FrameNestMediaCoverError(ValueError):
    """Sanitized error raised when accepted-cover construction is invalid."""


class CoverSourceKind(StrEnum):
    """Supported physical source kinds for the manual cover workflow.

    ``IMAGE`` is a timeless still-image source: it has no video duration and
    always uses the canonical source timestamp ``0``, which is never presented
    as a selected temporal frame.
    """

    GIF = "gif"
    MP4 = "mp4"
    IMAGE = "image"


def _validate_non_negative_int(value: object, *, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestMediaCoverError(message)
    return value


def _validate_positive_int(value: object, *, message: str) -> int:
    validated = _validate_non_negative_int(value, message=message)
    if validated <= 0:
        raise FrameNestMediaCoverError(message)
    return validated


def _validate_optional_non_negative_int(value: object, *, message: str) -> int | None:
    if value is None:
        return None
    return _validate_non_negative_int(value, message=message)


def _validate_machine_name(value: object, *, message: str) -> str:
    if not isinstance(value, str) or not _MACHINE_NAME_PATTERN.fullmatch(value):
        raise FrameNestMediaCoverError(message)
    return value


def _validate_sha256(value: object, *, message: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise FrameNestMediaCoverError(message)
    return value


def _validate_source_reference(value: object, *, message: str) -> str:
    if not isinstance(value, str) or not _SOURCE_REFERENCE_PATTERN.fullmatch(value):
        raise FrameNestMediaCoverError(message)
    return value


def source_reference_for_location(location_id: MediaLocationId) -> str:
    """Return the opaque sanitized provenance reference for one source location."""
    return f"location:{location_id.to_string()}"


@dataclass(frozen=True, slots=True)
class CoverSourceObservation:
    """Immutable sanitized observation facts for one cover source location.

    These values are the server-authoritative evidence that a manually selected
    frame came from a specific physical source, without exposing any absolute
    path, library root, or original filename.
    """

    source_location_id: MediaLocationId
    source_kind: CoverSourceKind
    source_size_bytes: int
    source_mtime_ns: int | None
    source_duration_ms: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.source_location_id, MediaLocationId):
            raise FrameNestMediaCoverError(INVALID_SOURCE_OBSERVATION_MESSAGE)
        if not isinstance(self.source_kind, CoverSourceKind):
            raise FrameNestMediaCoverError(INVALID_SOURCE_OBSERVATION_MESSAGE)
        object.__setattr__(
            self,
            "source_size_bytes",
            _validate_positive_int(
                self.source_size_bytes,
                message=INVALID_SOURCE_OBSERVATION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "source_mtime_ns",
            _validate_optional_non_negative_int(
                self.source_mtime_ns,
                message=INVALID_SOURCE_OBSERVATION_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "source_duration_ms",
            _validate_optional_non_negative_int(
                self.source_duration_ms,
                message=INVALID_SOURCE_OBSERVATION_MESSAGE,
            ),
        )


@dataclass(frozen=True, slots=True)
class MediaCover:
    """One durable accepted manually selected cover for one logical medium.

    The accepted cover belongs to logical media, never to a physical location.
    The source location and sanitized observation facts are provenance only.
    """

    media_id: MediaId
    source_location_id: MediaLocationId | None
    source_reference: str
    source_kind: CoverSourceKind
    source_timestamp_ms: int
    source_size_bytes: int
    source_mtime_ns: int | None
    source_duration_ms: int | None
    source_observation_version: str
    source_observation_digest: str
    artifact_profile: str
    artifact_media_type: str
    artifact_digest: str
    artifact_width: int
    artifact_height: int
    artifact_byte_size: int
    revision: int
    accepted_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.media_id, MediaId):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if self.source_location_id is not None and not isinstance(
            self.source_location_id, MediaLocationId
        ):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        object.__setattr__(
            self,
            "source_reference",
            _validate_source_reference(
                self.source_reference,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        if not isinstance(self.source_kind, CoverSourceKind):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        object.__setattr__(
            self,
            "source_timestamp_ms",
            _validate_non_negative_int(
                self.source_timestamp_ms,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "source_size_bytes",
            _validate_positive_int(
                self.source_size_bytes,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "source_mtime_ns",
            _validate_optional_non_negative_int(
                self.source_mtime_ns,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "source_duration_ms",
            _validate_optional_non_negative_int(
                self.source_duration_ms,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        if not isinstance(self.source_observation_version, str) or not isinstance(
            self.source_observation_digest, str
        ):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if len(self.source_observation_version) > MAX_ALGORITHM_NAME_LENGTH:
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in self.source_observation_version):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if self.source_observation_version != SOURCE_OBSERVATION_ALGORITHM:
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        object.__setattr__(
            self,
            "source_observation_digest",
            _validate_sha256(
                self.source_observation_digest,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        if not isinstance(self.artifact_profile, str) or not isinstance(
            self.artifact_media_type, str
        ):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if len(self.artifact_profile) > MAX_ALGORITHM_NAME_LENGTH:
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in self.artifact_profile):
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if self.artifact_profile != COVER_ARTIFACT_PROFILE:
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        if self.artifact_media_type != COVER_ARTIFACT_MEDIA_TYPE:
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE)
        object.__setattr__(
            self,
            "artifact_digest",
            _validate_sha256(
                self.artifact_digest,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "artifact_width",
            _validate_positive_int(
                self.artifact_width,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "artifact_height",
            _validate_positive_int(
                self.artifact_height,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "artifact_byte_size",
            _validate_positive_int(
                self.artifact_byte_size,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "revision",
            _validate_positive_int(
                self.revision,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "accepted_at_ms",
            _validate_non_negative_int(
                self.accepted_at_ms,
                message=INVALID_COVER_MESSAGE,
            ),
        )
        try:
            self.media_id.to_string()
            if self.source_location_id is not None:
                self.source_location_id.to_string()
        except (AttributeError, FrameNestIdentityError) as exc:
            raise FrameNestMediaCoverError(INVALID_COVER_MESSAGE) from exc

    @property
    def source_reference_matches_location(self) -> bool:
        """Return whether the live location matches the persisted provenance."""
        if self.source_location_id is None:
            return False
        return source_reference_for_location(self.source_location_id) == self.source_reference
