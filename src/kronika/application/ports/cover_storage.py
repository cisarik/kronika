"""Application ports for durable cover artifacts and regenerable cover thumbnails."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kronika.application.media_analysis import RepresentativeFrame
from kronika.domain.identities import MediaId


class CoverStorageError(RuntimeError):
    """Sanitized failure raised when durable cover storage operations fail."""


class CoverThumbnailUnavailableError(RuntimeError):
    """Raised when a cover thumbnail cannot be safely opened or validated."""


@dataclass(frozen=True, slots=True)
class CoverArtifact:
    """One validated immutable durable accepted-cover JPEG artifact."""

    profile: str
    media_type: str
    digest: str
    width: int
    height: int
    byte_size: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class CoverThumbnailImage:
    """One validated regenerable cover-thumbnail JPEG image."""

    media_type: str
    byte_size: int
    sha256: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class OpenedCoverThumbnail:
    """Validated opened cover-thumbnail artifact for HTTP delivery."""

    media_type: str
    byte_size: int
    payload: bytes
    close: Callable[[], None]


class CoverEncoder(Protocol):
    """Encoder contract for the durable artifact and its regenerable thumbnail."""

    def encode_artifact_frame(self, frame: RepresentativeFrame) -> CoverArtifact:
        """Encode one server-extracted frame as a validated durable JPEG artifact."""

    def encode_thumbnail(self, artifact_payload: bytes) -> CoverThumbnailImage:
        """Encode one validated thumbnail JPEG from a durable artifact payload."""


class DurableCoverStorage(Protocol):
    """Immutable content-addressed storage for durable accepted-cover artifacts."""

    def publish(self, *, media_id: MediaId, artifact: CoverArtifact) -> None:
        """No-clobber publish one immutable content-addressed artifact."""

    def artifact_valid(self, *, media_id: MediaId, digest: str) -> bool:
        """Return whether the content-addressed artifact exists and validates."""

    def read_bytes(self, *, media_id: MediaId, digest: str) -> bytes:
        """Return validated immutable artifact bytes, or raise CoverStorageError."""


class CoverThumbnailCache(Protocol):
    """Regenerable server-owned cache for cover thumbnails."""

    algorithm: str

    def key_for(self, *, media_id: MediaId, artifact_digest: str) -> str:
        """Return the deterministic cache key for one cover-artifact digest."""

    def contains(self, cache_key: str) -> bool:
        """Return whether the key holds a validated current thumbnail."""

    def contains_many(self, cache_keys: tuple[str, ...]) -> set[str]:
        """Return the subset of keys that hold validated current thumbnails."""

    def publish(self, cache_key: str, image: CoverThumbnailImage) -> None:
        """Atomically publish one validated thumbnail with no-clobber semantics."""

    def open(self, cache_key: str) -> OpenedCoverThumbnail:
        """Open one validated cover thumbnail for inline delivery."""
