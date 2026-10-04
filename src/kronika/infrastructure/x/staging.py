"""Staging for requester-private X assets (reuses descriptor-safe staging).

X and YouTube acquisition share the same exact descriptor-oriented, symlink-safe
staging contract. This module re-exposes that proven storage as a bounded X
staging binding without destabilizing the mature YouTube path.
"""

from __future__ import annotations

from pathlib import Path

from kronika.infrastructure.youtube.staging import (
    FilesystemYouTubeStaging,
)

ARTIFACT_FILENAME = "artifact.bin"


class FilesystemXStaging(FilesystemYouTubeStaging):
    """X-specific binding over the shared symlink-safe staging storage."""

    def __init__(
        self,
        root: Path,
        *,
        forbidden_roots: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(
            root,
            forbidden_roots=forbidden_roots,
            artifact_filename=ARTIFACT_FILENAME,
        )

    def clear(self, staging_key: str) -> None:
        """Idempotently remove one owned staging directory."""
        self.cleanup(staging_key)
