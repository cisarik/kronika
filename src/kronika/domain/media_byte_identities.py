"""Pure-domain canonical exact-byte media identities."""

from __future__ import annotations

from dataclasses import dataclass
import re

from kronika.domain.identities import MediaByteIdentityId

INVALID_MEDIA_BYTE_IDENTITY_MESSAGE = "Invalid media byte identity."

_SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")


class FrameNestMediaByteIdentityError(ValueError):
    """Sanitized error raised when byte-identity evidence is invalid."""


@dataclass(frozen=True, slots=True)
class MediaByteIdentity:
    """Canonical identity for one exact byte stream."""

    id: MediaByteIdentityId
    checksum_algorithm: str
    size_bytes: int
    checksum_hex: str
    created_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, MediaByteIdentityId):
            raise FrameNestMediaByteIdentityError(INVALID_MEDIA_BYTE_IDENTITY_MESSAGE)
        validate_media_byte_identity_evidence(
            checksum_algorithm=self.checksum_algorithm,
            size_bytes=self.size_bytes,
            checksum_hex=self.checksum_hex,
        )
        if (
            isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms < 0
        ):
            raise FrameNestMediaByteIdentityError(INVALID_MEDIA_BYTE_IDENTITY_MESSAGE)


def validate_media_byte_identity_evidence(
    *,
    checksum_algorithm: object,
    size_bytes: object,
    checksum_hex: object,
) -> tuple[str, int, str]:
    """Return validated canonical exact-byte evidence."""
    if checksum_algorithm != "sha256":
        raise FrameNestMediaByteIdentityError(INVALID_MEDIA_BYTE_IDENTITY_MESSAGE)
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise FrameNestMediaByteIdentityError(INVALID_MEDIA_BYTE_IDENTITY_MESSAGE)
    if not isinstance(checksum_hex, str) or not _SHA256_HEX_PATTERN.fullmatch(
        checksum_hex
    ):
        raise FrameNestMediaByteIdentityError(INVALID_MEDIA_BYTE_IDENTITY_MESSAGE)
    return "sha256", size_bytes, checksum_hex
