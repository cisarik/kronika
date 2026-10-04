"""Application port for read-only library filesystem scanning."""

from __future__ import annotations

from typing import Protocol

from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanLimits,
)
from kronika.domain import LibraryRoot


class LibraryScanner(Protocol):
    """Infrastructure-independent scanner contract for library preview."""

    def preview(
        self,
        root: LibraryRoot,
        limits: LibraryScanLimits,
    ) -> LibraryFilesystemScanResult:
        """Return a bounded, deterministic filesystem preview for one library root."""
