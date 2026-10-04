"""Application port for private lifecycle-owned YouTube staging."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class YouTubeStagingError(RuntimeError):
    """Sanitized base error for unsafe or unavailable acquisition staging."""


class YouTubeStagingUnavailableError(YouTubeStagingError):
    """The configured private root cannot be used safely."""


class YouTubeStagingInconsistentError(YouTubeStagingError):
    """Claim-owned staging content violates path or object invariants."""


class YouTubeStagingLimitExceededError(YouTubeStagingError):
    """Staging usage or free-space reserve crossed a configured bound."""


class YouTubeStagedArtifactReader(Protocol):
    """Stable descriptor-backed reader used for exact upload handoff."""

    @property
    def size_bytes(self) -> int:
        """Return the size captured when the regular file was opened."""

    def read(self, size: int) -> bytes:
        """Read at most size bytes."""

    def seek(self, offset: int) -> None:
        """Seek to an exact non-negative upload offset."""

    def verify_still_consistent(self) -> None:
        """Reject inode, link, size, mode, or mtime replacement."""

    def close(self) -> None:
        """Close the held descriptor."""


class YouTubeStagingStorage(Protocol):
    """Private exact-ownership storage contract."""

    @property
    def root_available(self) -> bool:
        """Return whether the pre-existing root is safely usable."""

    def prepare(self, staging_key: str) -> Path:
        """Create or validate one opaque 0700 claim directory."""

    def claim_directory(self, staging_key: str) -> Path:
        """Return a validated path for subprocess cwd/output."""

    def usage_bytes(self, staging_key: str) -> int:
        """Return total regular-file bytes without following links."""

    def available_bytes(self) -> int:
        """Return currently available root filesystem bytes."""

    def artifact_size(self, staging_key: str) -> int | None:
        """Return fixed artifact size when present and safe."""

    def open_artifact(
        self,
        staging_key: str,
        *,
        expected_size_bytes: int | None = None,
    ) -> YouTubeStagedArtifactReader:
        """Open the fixed final artifact through a stable descriptor."""

    def cleanup(self, staging_key: str) -> None:
        """Remove only the exact opaque claim directory without following links."""
