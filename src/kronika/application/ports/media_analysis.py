"""Application port for read-only local media analysis preparation."""

from __future__ import annotations

from typing import Protocol

from kronika.application.media_analysis import MediaRelativePath, PreparedAnalysisResult
from kronika.domain import LibraryRoot


class LocalMediaAnalysisPreparer(Protocol):
    """Infrastructure-independent preparation contract for one library candidate."""

    def prepare(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
    ) -> PreparedAnalysisResult:
        """Prepare bounded technical metadata and representative frames for one candidate."""
