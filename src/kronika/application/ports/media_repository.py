"""Application port for the persistent media catalog foundation."""

from __future__ import annotations

from typing import Protocol

from kronika.domain import LibraryId, MediaId, MediaLocationId
from kronika.domain.media import LogicalMedia, MediaLocation, MediaRelativePath


class MediaAlreadyExistsError(RuntimeError):
    """Raised when a logical media item with the same identity already exists."""


class MediaLocationAlreadyExistsError(RuntimeError):
    """Raised when a physical media location with the same identity already exists."""


class MediaLocationNotUniqueError(RuntimeError):
    """Raised when a library-relative path is already claimed by a location."""


class MediaLocationReferenceNotFoundError(RuntimeError):
    """Raised when a media location references absent logical media or library."""


class FrameNestMediaRepositoryError(RuntimeError):
    """Sanitized error raised when media repository operations fail."""


class MediaRepository(Protocol):
    """Persistence-independent media catalog contract."""

    def add_media(self, media: LogicalMedia) -> None:
        """Persist one valid logical media item."""

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        """Return one logical media item by identity, or None when absent."""

    def list_media(self) -> tuple[LogicalMedia, ...]:
        """Return all logical media items in deterministic order."""

    def add_location(self, location: MediaLocation) -> None:
        """Persist one valid physical media location."""

    def add_media_with_location(self, media: LogicalMedia, location: MediaLocation) -> None:
        """Persist one logical media item and one physical location atomically."""

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        """Return one physical media location by identity, or None when absent."""

    def get_location_by_library_path(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> MediaLocation | None:
        """Return one location by exact library-relative path, or None."""

    def list_locations_for_media(self, media_id: MediaId) -> tuple[MediaLocation, ...]:
        """Return all locations for one logical media item in deterministic order."""

    def list_all_locations(self) -> tuple[MediaLocation, ...]:
        """Return all physical media locations in deterministic order."""
