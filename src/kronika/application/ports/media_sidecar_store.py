"""Application port for infrastructure-independent sidecar storage observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from kronika.domain.libraries import LibraryRoot
from kronika.domain.media import MediaRelativePath

SIDECAR_FILENAME_SUFFIX = ".framenest.json"
#: Accepted read-only filename spelling. ``sidecar_filename`` keeps returning the
#: current writer spelling until the durable-writer cut adopts the Kronika one.
COMPATIBLE_SIDECAR_FILENAME_SUFFIX = ".kronika.json"
ACCEPTED_SIDECAR_FILENAME_SUFFIXES = (
    SIDECAR_FILENAME_SUFFIX,
    COMPATIBLE_SIDECAR_FILENAME_SUFFIX,
)


SIDECAR_UNSAFE_TARGET = "SIDECAR_UNSAFE_TARGET"
SIDECAR_LOCATION_NOT_WRITABLE = "SIDECAR_LOCATION_NOT_WRITABLE"
SIDECAR_UNAVAILABLE = "SIDECAR_UNAVAILABLE"


class MediaSidecarStoreError(RuntimeError):
    """Sanitized infrastructure error for sidecar filesystem observations."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class SidecarTargetKind(StrEnum):
    """Classification of one sidecar directory entry before parsing."""

    MISSING = "missing"
    REGULAR = "regular"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class SidecarTargetObservation:
    """Read-only observation of one sidecar path."""

    kind: SidecarTargetKind
    payload: bytes | None = None


def sidecar_filename(media_relative_path: MediaRelativePath) -> str:
    """Return the adjacent sidecar filename for one media file."""
    return f"{media_relative_path.filename}{SIDECAR_FILENAME_SUFFIX}"


def accepted_sidecar_filenames(media_relative_path: MediaRelativePath) -> tuple[str, ...]:
    """Return every accepted adjacent sidecar filename, writer spelling first."""
    return tuple(
        f"{media_relative_path.filename}{suffix}"
        for suffix in ACCEPTED_SIDECAR_FILENAME_SUFFIXES
    )


class MediaSidecarStore(Protocol):
    """Safe adjacent sidecar create/replace/observe boundary."""

    def observe_adjacent(
        self,
        root: LibraryRoot,
        media_relative_path: MediaRelativePath,
    ) -> SidecarTargetObservation:
        """Classify the adjacent sidecar after enforcing media-path safety gates."""

    def create_adjacent(
        self,
        root: LibraryRoot,
        media_relative_path: MediaRelativePath,
        payload: bytes,
    ) -> None:
        """Atomically create one absent adjacent sidecar."""

    def replace_adjacent(
        self,
        root: LibraryRoot,
        media_relative_path: MediaRelativePath,
        payload: bytes,
    ) -> None:
        """Atomically replace one existing regular adjacent sidecar."""

    def observe_explicit(self, path: str) -> SidecarTargetObservation:
        """Classify one operator-supplied sidecar path without catalog access."""
