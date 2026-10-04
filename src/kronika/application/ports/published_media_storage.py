"""Application port for server-owned published original bytes."""

from __future__ import annotations

from typing import Protocol

from kronika.application.ports.quarantine_storage import QuarantineReader
from kronika.domain.identities import LibraryId
from kronika.domain.upload_publications import UploadPublication


class PublishedMediaStorageError(RuntimeError):
    """Base sanitized publication-storage failure."""


class PublishedMediaStorageUnavailableError(PublishedMediaStorageError):
    """Raised when the configured publication destination is unsafe or unavailable."""


class PublishedMediaInsufficientSpaceError(PublishedMediaStorageError):
    """Raised when destination free space is insufficient for publication allocation."""


class PublishedMediaTargetCollisionError(PublishedMediaStorageError):
    """Raised when an unexpected object owns the reserved final target."""


class PublishedMediaVerificationError(PublishedMediaStorageError):
    """Raised when source or destination bytes do not match durable evidence."""


class PublishedMediaWriteError(PublishedMediaStorageError):
    """Raised when a publication-owned filesystem mutation fails safely."""


class PublishedMediaStorage(Protocol):
    """Publish verified bytes inside one explicit server-managed destination."""

    @property
    def destination_id(self) -> LibraryId:
        """Return the opaque configured destination identity."""

    @property
    def root_available(self) -> bool:
        """Return whether the configured root is currently safe and writable."""

    def available_bytes(self) -> int:
        """Return free bytes on the destination filesystem without exposing paths."""

    def verify_target(self, publication: UploadPublication) -> bool:
        """Return True for exact final bytes, False when absent, or reject collision."""

    def publish_from_reader(
        self,
        publication: UploadPublication,
        source: QuarantineReader,
    ) -> None:
        """Crash-safely publish exact source bytes without replacing a final object."""
