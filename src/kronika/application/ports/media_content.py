"""Application port for secure read-only local media content delivery."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

from kronika.domain import LibraryRoot
from kronika.domain.media import MediaKind, MediaRelativePath

SUPPORTED_MEDIA_CONTENT: dict[tuple[MediaKind, str], str] = {
    (MediaKind.VIDEO, ".mp4"): "video/mp4",
    (MediaKind.ANIMATED_IMAGE, ".gif"): "image/gif",
    (MediaKind.IMAGE, ".jpg"): "image/jpeg",
    (MediaKind.IMAGE, ".jpeg"): "image/jpeg",
    (MediaKind.IMAGE, ".png"): "image/png",
}


@dataclass(frozen=True, slots=True)
class OpenedMediaContent:
    """Validated open media content with a stable descriptor and bounded byte streaming."""

    media_type: str
    byte_size: int
    stream: Callable[[int, int | None], Iterator[bytes]]
    close: Callable[[], None]
    mtime_ns: int | None = None


class MediaContentReader(Protocol):
    """Infrastructure-independent secure content reader for one catalog location."""

    def open(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
        kind: MediaKind,
    ) -> OpenedMediaContent:
        """Open one validated media file for bounded read-only byte streaming."""
