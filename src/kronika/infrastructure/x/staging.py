"""Staging for requester-private X assets (reuses descriptor-safe staging).

X and YouTube acquisition share the same exact descriptor-oriented, symlink-safe
staging contract. This module re-exposes that proven storage as a bounded X
staging binding without destabilizing the mature YouTube path.
"""

from __future__ import annotations

from pathlib import Path
import re

from kronika.application.ports.x_extractor import XExtractionError
from kronika.application.ports.youtube_staging import YouTubeStagedArtifactReader

from kronika.infrastructure.youtube.staging import (
    FilesystemYouTubeStaging,
)

ARTIFACT_FILENAME = "artifact.bin"
_MERGED_ARTIFACT_PATTERN = re.compile(
    re.escape(ARTIFACT_FILENAME) + r"\.([A-Za-z0-9]{1,10})"
)
_TEMPORARY_SUFFIXES = frozenset({"part", "partial", "tmp", "temp", "ytdl", "download"})


def find_artifact(directory: Path) -> Path | None:
    """Prefer the fixed file, otherwise accept one completed merged artifact."""
    exact = Path(directory) / ARTIFACT_FILENAME
    if not exact.is_symlink() and exact.is_file():
        return exact
    candidates: list[Path] = []
    try:
        entries = list(Path(directory).iterdir())
    except FileNotFoundError:
        return None
    for entry in entries:
        match = _MERGED_ARTIFACT_PATTERN.fullmatch(entry.name)
        if (
            match is not None
            and match.group(1).lower() not in _TEMPORARY_SUFFIXES
            and not entry.is_symlink()
            and entry.is_file()
        ):
            candidates.append(entry)
    if len(candidates) > 1:
        raise XExtractionError("X_STAGING_AMBIGUOUS", "X produced multiple artifacts.")
    return candidates[0] if candidates else None


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

    def open_artifact(
        self,
        staging_key: str,
        *,
        expected_size_bytes: int | None = None,
    ) -> YouTubeStagedArtifactReader:
        """Keep descriptor validation while opening the resolved merged name."""
        try:
            return super().open_artifact(
                staging_key, expected_size_bytes=expected_size_bytes
            )
        except FileNotFoundError:
            # The parent has validated the key, root and claim before fallback.
            artifact = find_artifact(self.root / staging_key)
            if artifact is None:
                raise
            return FilesystemYouTubeStaging(
                self.root,
                forbidden_roots=self._forbidden_roots,
                artifact_filename=artifact.name,
            ).open_artifact(staging_key, expected_size_bytes=expected_size_bytes)
