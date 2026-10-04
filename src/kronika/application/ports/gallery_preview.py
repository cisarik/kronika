"""Application ports for persistent gallery preview derivatives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kronika.application.media_analysis import RepresentativeFrame


@dataclass(frozen=True, slots=True)
class GalleryPreviewImage:
    """One bounded static gallery preview image derivative."""

    media_type: str
    byte_size: int
    sha256: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class OpenedGalleryPreview:
    """Validated opened gallery preview artifact."""

    media_type: str
    byte_size: int
    etag: str
    payload: bytes
    close: Callable[[], None]


class GalleryPreviewEncoder(Protocol):
    """Encoder contract for one static gallery preview derivative."""

    def encode_frame(self, frame: RepresentativeFrame) -> GalleryPreviewImage:
        """Encode one prepared representative PNG frame as a bounded static image."""


class GalleryPreviewCache(Protocol):
    """Filesystem-cache contract for persistent gallery preview derivatives."""

    def contains_current(self, cache_key: str) -> bool:
        """Return whether the final artifact for the key exists and validates."""

    def contains_any_for_location(self, algorithm_version: str, location_id: str) -> bool:
        """Return whether any artifact exists for a location under this algorithm."""

    def publish(self, cache_key: str, image: GalleryPreviewImage) -> None:
        """Atomically publish a validated derivative for the key."""

    def open(self, cache_key: str) -> OpenedGalleryPreview:
        """Open one already generated validated derivative."""
