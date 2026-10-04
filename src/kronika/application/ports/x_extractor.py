"""Application port for a normalized X extractor (never raw provider output)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.domain.x_acquisition import XNormalizedInspection


class XExtractorConfigurationError(RuntimeError):
    """Raised when the extractor is not safely configurable."""


class XExtractionError(RuntimeError):
    """Sanitized base error for a failed normalized extraction."""

    def __init__(self, code: str, message: str = "X extraction failed.") -> None:
        super().__init__(message)
        self.code = code


class XRequiresAuthenticationError(XExtractionError):
    """Typed terminal failure when the post requires X authentication."""


class XExtractionInterrupted(Exception):
    """Lifecycle interruption of owned X extractor work; durable retry remains."""


@dataclass(frozen=True, slots=True)
class XAssetAcquisition:
    """Public-safe result of one bounded asset download into staging."""

    size_bytes: int
    sha256: str


class XExtractor(Protocol):
    """Persistence-independent normalized X post inspection contract."""

    def attest_version(self) -> str | None:
        """Return the extractor version or None when unavailable."""

    def inspect(
        self,
        *,
        post_id: str,
        submitted_url: str,
    ) -> XNormalizedInspection:
        """Inspect one validated X post and return normalized metadata."""

    def download(
        self,
        *,
        post_id: str,
        ordinal: int,
        media_type: str,
        expected_mime: str,
        source_media_key: str | None,
        selected_variant: str | None,
        stage_key: str,
        submitted_url: str,
        staging: XStagingStorage,
    ) -> XAssetAcquisition:
        """Download one source asset into claim-owned staging deterministically."""


class XStagingStorage(Protocol):
    """Bounded claim-owned staging contract reused for acquired X assets.

    This is the same descriptor-oriented, symlink-safe storage shape proven by
    the YouTube acquisition staging. The precedent implementation is reused to
    avoid destabilizing that mature path.
    """

    @property
    def root_available(self) -> bool:
        """Return True when the staging root is usable."""

    def prepare(self, staging_key: str) -> object:
        """Create one owned staging directory and return its path."""

    def artifact_size(self, staging_key: str) -> int | None:
        """Return the staged artifact size, or None when absent."""

    def clear(self, staging_key: str) -> None:
        """Idempotently remove one owned staging directory."""