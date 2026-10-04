"""POSIX private-catalog creation and open checks.

Preparation runs when a connection is opened. It does not change the process
umask except for the duration of one creation call, which always restores the
previous mask. Unsafe existing objects are rejected without chmod.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat

from kronika.infrastructure.persistence.errors import FrameNestPersistenceError

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_MESSAGE = "Private catalog state is not available."


class PrivateCatalogError(FrameNestPersistenceError):
    """Sanitized private-catalog failure. The message has no path or content."""

    def __init__(self, *, error_code: str) -> None:
        super().__init__(
            _MESSAGE,
            error_code=error_code,
            retryable=False,
        )


def prepare_writable_catalog(database_path: Path) -> None:
    """Create a missing private database or validate an existing one."""
    _require_posix()
    path = _leaf_path(database_path)
    _ensure_directory(path.parent)
    _ensure_database_file(path)
    verify_private_catalog(path)


def prepare_readonly_catalog(database_path: Path) -> None:
    """Validate an existing catalog without creating anything."""
    _require_posix()
    path = _leaf_path(database_path)
    if not path.exists() or path.is_symlink():
        raise PrivateCatalogError(error_code="DATABASE_NOT_FOUND")
    _validate_directory(path.parent)
    _validate_database_file(path)
    _validate_auxiliary_files(path)


def verify_private_catalog(database_path: Path) -> None:
    """Re-check directory, database, and any SQLite auxiliary files."""
    _require_posix()
    path = _leaf_path(database_path)
    _validate_directory(path.parent)
    _validate_database_file(path)
    _validate_auxiliary_files(path)


def _require_posix() -> None:
    if os.name == "nt" or os.name != "posix":
        raise PrivateCatalogError(error_code="PRIVATE_CATALOG_UNSUPPORTED")


def _leaf_path(database_path: Path) -> Path:
    try:
        path = Path(database_path)
    except (TypeError, ValueError) as exc:
        raise PrivateCatalogError(error_code="INVALID_DATABASE_PATH") from exc
    if not path.is_absolute():
        raise PrivateCatalogError(error_code="INVALID_DATABASE_PATH")
    return path


def _ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_PATH")
    if path.exists():
        _validate_directory(path)
        return
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_PATH")
    previous = os.umask(0o077)
    try:
        os.mkdir(path, _DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PrivateCatalogError(error_code="PRIVATE_CATALOG_CREATE_FAILED") from exc
    finally:
        os.umask(previous)
    _validate_directory(path)


def _ensure_database_file(path: Path) -> None:
    if path.is_symlink():
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_PATH")
    if path.exists():
        _validate_database_file(path)
        return
    previous = os.umask(0o077)
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
            _FILE_MODE,
        )
    except FileExistsError:
        descriptor = None
    except OSError as exc:
        raise PrivateCatalogError(error_code="PRIVATE_CATALOG_CREATE_FAILED") from exc
    finally:
        os.umask(previous)
    if descriptor is not None:
        os.close(descriptor)
    _validate_database_file(path)


def _validate_directory(path: Path) -> None:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_PATH")
    if info.st_uid != os.getuid():
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_OWNER")
    if stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_MODE")


def _validate_database_file(path: Path) -> None:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_PATH")
    if info.st_uid != os.getuid():
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_OWNER")
    if info.st_nlink != 1:
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_LINK")
    if stat.S_IMODE(info.st_mode) != _FILE_MODE:
        raise PrivateCatalogError(error_code="UNSAFE_CATALOG_MODE")


def _validate_auxiliary_files(database_path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = Path(f"{database_path}{suffix}")
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise PrivateCatalogError(error_code="UNSAFE_CATALOG_PATH")
        if info.st_uid != os.getuid():
            raise PrivateCatalogError(error_code="UNSAFE_CATALOG_OWNER")
        if stat.S_IMODE(info.st_mode) != _FILE_MODE:
            raise PrivateCatalogError(error_code="UNSAFE_CATALOG_MODE")


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise PrivateCatalogError(error_code="PRIVATE_CATALOG_UNAVAILABLE") from exc
