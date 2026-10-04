"""Application port for server-authoritative cover source probing and extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.application.media_analysis import RepresentativeFrame
from kronika.domain import LibraryRoot
from kronika.domain.media import MediaKind, MediaRelativePath


@dataclass(frozen=True, slots=True)
class CoverSourceProbe:
    """Sanitized server-authoritative observation of one physical cover source."""

    duration_ms: int | None
    source_size_bytes: int
    source_mtime_ns: int | None


class CoverSourceAnalyzer(Protocol):
    """Infrastructure-independent contract for authoritative source observation.

    The server never trusts browser-provided frame claims; it probes and
    extracts frames itself behind the registered-root containment boundary.
    """

    def probe(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
        kind: MediaKind,
    ) -> CoverSourceProbe:
        """Return bounded source observations for one available supported location."""

    def extract_frame(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
        kind: MediaKind,
        timestamp_ms: int,
    ) -> RepresentativeFrame:
        """Extract one bounded authoritative frame at an arbitrary timestamp."""
