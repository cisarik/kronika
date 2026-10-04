"""Local filesystem safety for one explicit library analysis candidate."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from kronika.application.library_scan import classify_candidate_extension
from kronika.application.media_analysis import (
    INVALID_MEDIA_PATH_MESSAGE,
    FrameNestMediaAnalysisError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PREPARATION_UNAVAILABLE_MESSAGE,
    candidate_kind_for_relative_path,
)
from kronika.domain import LibraryPathFlavor, LibraryRoot


def _native_path_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _is_nested_symlink(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        parent = current.parent
        if parent == current:
            break
        if current.is_symlink():
            return True
        current = parent
    return False


def resolve_safe_candidate_path(
    root: LibraryRoot,
    relative_path: MediaRelativePath,
) -> tuple[Path, str]:
    """Resolve one safe regular-file candidate beneath a registered library root."""
    if root.flavor != _native_path_flavor():
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)

    try:
        candidate_kind_for_relative_path(relative_path)
    except FrameNestMediaAnalysisError:
        raise MediaAnalysisUnavailableError(INVALID_MEDIA_PATH_MESSAGE) from None

    host_root = Path(root.path)
    try:
        if not host_root.exists() or not host_root.is_dir():
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
    except OSError:
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from None

    parsed = PurePosixPath(relative_path.value)
    if parsed.is_absolute() or any(part in (".", "..") or not part for part in parsed.parts):
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
    if any(part.startswith(".") for part in parsed.parts):
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)

    candidate = host_root
    for part in parsed.parts:
        candidate = candidate / part
        try:
            if candidate.is_symlink():
                raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
        except OSError:
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from None

    try:
        if not candidate.exists():
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
        if candidate.is_dir():
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
        if candidate.is_symlink():
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
        if not candidate.is_file():
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
    except OSError:
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from None

    try:
        effective_root = host_root.resolve()
        effective_candidate = candidate.resolve()
        effective_candidate.relative_to(effective_root)
    except (OSError, ValueError):
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from None

    if _is_nested_symlink(candidate, host_root):
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)

    extension = candidate.suffix.lower()
    if classify_candidate_extension(extension) is None:
        raise MediaAnalysisUnavailableError(INVALID_MEDIA_PATH_MESSAGE)

    return candidate, extension
