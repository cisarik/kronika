"""Application port for server-owned quarantine upload storage."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.uploads import UploadStorageKey


class QuarantineStorageUnavailableError(RuntimeError):
    """Raised when quarantine storage cannot be used safely."""


class QuarantineStorageInsufficientSpaceError(RuntimeError):
    """Raised when current free space is below the configured reserve."""


class QuarantineStateInconsistentError(RuntimeError):
    """Raised when a quarantine file is absent, unsafe, or size-inconsistent."""


class QuarantineWriteFailedError(RuntimeError):
    """Raised when writing, flushing, truncating, or deleting staged bytes fails."""


class QuarantineWriter(Protocol):
    """Open quarantine writer positioned at the authoritative offset."""

    def write(self, data: bytes) -> int:
        """Write one non-empty byte chunk and return bytes accepted by the OS."""

    def truncate_and_fsync(self, size: int) -> None:
        """Truncate the file to size and flush the change durably."""

    def flush_and_fsync(self) -> None:
        """Flush written bytes durably before database acknowledgement."""

    def close(self) -> None:
        """Close the writer."""


class QuarantineReader(Protocol):
    """Stable open quarantine reader for validation and hashing."""

    @property
    def size_bytes(self) -> int:
        """Return the physical size observed for the stable object."""

    @property
    def file_descriptor(self) -> int:
        """Return the stable open file descriptor for process-bound probes."""

    def read(self, size: int) -> bytes:
        """Read at most size bytes from the stable object."""

    def seek_start(self) -> None:
        """Rewind the stable object to its beginning."""

    def verify_still_consistent(self) -> None:
        """Raise when the stable object changed since opening."""

    def close(self) -> None:
        """Close the reader."""


class QuarantineStorage(Protocol):
    """Filesystem-independent quarantine storage contract."""

    @property
    def root_available(self) -> bool:
        """Return whether the configured root is currently usable."""

    def available_bytes(self) -> int:
        """Return currently available bytes for advisory limit checks."""

    def file_size(self, storage_key: UploadStorageKey) -> int | None:
        """Return the staged file size, or None when no file exists."""

    def open_writer(
        self,
        storage_key: UploadStorageKey,
        *,
        offset: int,
        create: bool,
    ) -> QuarantineWriter:
        """Open a safe regular quarantine file for append at offset."""

    def open_reader(
        self,
        storage_key: UploadStorageKey,
        *,
        expected_size_bytes: int,
    ) -> QuarantineReader:
        """Open a stable regular quarantine file for validation."""

    def truncate(self, storage_key: UploadStorageKey, size: int) -> None:
        """Durably truncate an existing quarantine file to size."""

    def remove(self, storage_key: UploadStorageKey) -> None:
        """Remove the staged file, treating absence as success."""
