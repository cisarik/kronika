"""Application port for the local library registry."""

from __future__ import annotations

from typing import Protocol

from kronika.domain import DeviceId, Library, LibraryId


class LibraryAlreadyExistsError(RuntimeError):
    """Raised when a library with the same identity is already registered."""


class LibraryDeviceNotFoundError(RuntimeError):
    """Raised when the owning device is not registered."""


class LibraryRootAlreadyRegisteredError(RuntimeError):
    """Raised when the same root is already registered for the device."""


class FrameNestLibraryRepositoryError(RuntimeError):
    """Sanitized error raised when library repository operations fail."""


class LibraryRepository(Protocol):
    """Persistence-independent library registry contract."""

    def add(self, library: Library) -> None:
        """Register one valid library."""

    def get(self, library_id: LibraryId) -> Library | None:
        """Return one library by identity, or None when absent."""

    def list_all(self) -> tuple[Library, ...]:
        """Return all registered libraries in deterministic order."""
