"""Local filesystem preparation for catalog library registration."""

from __future__ import annotations

import os

from kronika.domain import FrameNestLibraryRootError, LibraryPathFlavor, LibraryRoot

# Symlink targets are not resolved; the lexical symlink path is preserved per ADR-0013.


class LibraryRootNotUsableError(Exception):
    """Raised when a host path cannot be prepared as a library root."""


def native_library_path_flavor() -> LibraryPathFlavor:
    """Return the path flavor for the machine running the catalog CLI."""
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def prepare_library_root(raw_path: str) -> LibraryRoot:
    """Prepare a host path as a device-local library root without resolving symlinks."""
    if not isinstance(raw_path, str) or not raw_path:
        raise LibraryRootNotUsableError()
    try:
        expanded = os.path.expanduser(raw_path)
        if not os.path.isabs(expanded):
            expanded = os.path.join(os.getcwd(), expanded)
        normalized = os.path.normpath(expanded)
    except (OSError, TypeError, ValueError):
        raise LibraryRootNotUsableError() from None
    if not os.path.exists(normalized):
        raise LibraryRootNotUsableError()
    if not os.path.isdir(normalized):
        raise LibraryRootNotUsableError()
    try:
        return LibraryRoot(flavor=native_library_path_flavor(), path=normalized)
    except FrameNestLibraryRootError:
        raise LibraryRootNotUsableError() from None
