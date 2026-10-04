"""Server-owned filesystem quarantine storage for resumable uploads."""

from __future__ import annotations

import errno
import os
import shutil
import stat as stat_module
from pathlib import Path

from kronika.application.ports.quarantine_storage import (
    QuarantineStateInconsistentError,
    QuarantineStorageUnavailableError,
    QuarantineWriteFailedError,
)
from kronika.domain.uploads import UploadStorageKey

_PART_SUFFIX = ".part"


class FilesystemQuarantineStorage:
    """Quarantine adapter rooted in one pre-existing non-symlink directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root_available(self) -> bool:
        try:
            root_fd = self._open_root()
        except QuarantineStorageUnavailableError:
            return False
        _close_descriptor(root_fd)
        return True

    @property
    def root(self) -> Path:
        return self._root

    def available_bytes(self) -> int:
        self._validate_root_path()
        try:
            return shutil.disk_usage(self._root).free
        except OSError as exc:
            raise QuarantineStorageUnavailableError("quarantine storage unavailable") from exc

    def file_size(self, storage_key: UploadStorageKey) -> int | None:
        root_fd = self._open_root()
        try:
            fd = _open_file(
                root_fd,
                _filename(storage_key),
                os.O_RDONLY,
                exists_ok=True,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise QuarantineStateInconsistentError("quarantine state inconsistent") from exc
        finally:
            _close_descriptor(root_fd)
        try:
            stat_result = os.fstat(fd)
            _validate_regular_file(stat_result)
            return stat_result.st_size
        except OSError as exc:
            raise QuarantineStateInconsistentError("quarantine state inconsistent") from exc
        finally:
            _close_descriptor(fd)

    def open_writer(
        self,
        storage_key: UploadStorageKey,
        *,
        offset: int,
        create: bool,
    ) -> "_QuarantineFileWriter":
        root_fd = self._open_root()
        filename = _filename(storage_key)
        try:
            flags = os.O_WRONLY if create else os.O_RDWR
            if create:
                flags |= os.O_CREAT | os.O_EXCL
            fd = _open_file(root_fd, filename, flags, exists_ok=not create)
            stat_result = os.fstat(fd)
            _validate_regular_file(stat_result)
            if stat_result.st_mode & 0o111:
                raise QuarantineStateInconsistentError("quarantine state inconsistent")
            os.lseek(fd, offset, os.SEEK_SET)
            return _QuarantineFileWriter(fd)
        except (OSError, QuarantineStateInconsistentError) as exc:
            raise QuarantineWriteFailedError("quarantine write failed") from exc
        finally:
            _close_descriptor(root_fd)

    def open_reader(
        self,
        storage_key: UploadStorageKey,
        *,
        expected_size_bytes: int,
    ) -> "_QuarantineFileReader":
        root_fd = self._open_root()
        try:
            fd = _open_file(
                root_fd,
                _filename(storage_key),
                os.O_RDONLY,
                exists_ok=True,
            )
        except (FileNotFoundError, OSError) as exc:
            raise QuarantineStateInconsistentError("quarantine state inconsistent") from exc
        finally:
            _close_descriptor(root_fd)
        try:
            stat_result = os.fstat(fd)
            _validate_regular_file(stat_result)
            if stat_result.st_size != expected_size_bytes:
                raise QuarantineStateInconsistentError("quarantine state inconsistent")
            os.lseek(fd, 0, os.SEEK_SET)
            return _QuarantineFileReader(fd, stat_result)
        except (OSError, QuarantineStateInconsistentError):
            _close_descriptor(fd)
            raise

    def truncate(self, storage_key: UploadStorageKey, size: int) -> None:
        writer = self.open_writer(storage_key, offset=size, create=False)
        try:
            writer.truncate_and_fsync(size)
        finally:
            writer.close()

    def remove(self, storage_key: UploadStorageKey) -> None:
        root_fd = self._open_root()
        try:
            os.unlink(_filename(storage_key), dir_fd=root_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise QuarantineWriteFailedError("quarantine cleanup failed") from exc
        finally:
            _close_descriptor(root_fd)

    def _validate_root_path(self) -> None:
        if not self._root.is_absolute():
            raise QuarantineStorageUnavailableError("quarantine storage unavailable")
        try:
            if self._root.is_symlink() or not self._root.exists() or not self._root.is_dir():
                raise QuarantineStorageUnavailableError("quarantine storage unavailable")
        except OSError as exc:
            raise QuarantineStorageUnavailableError("quarantine storage unavailable") from exc

    def _open_root(self) -> int:
        self._validate_root_path()
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self._root), flags)
        except OSError as exc:
            raise QuarantineStorageUnavailableError("quarantine storage unavailable") from exc
        try:
            stat_result = os.fstat(fd)
            if not stat_module.S_ISDIR(stat_result.st_mode):
                raise QuarantineStorageUnavailableError("quarantine storage unavailable")
        except Exception:
            _close_descriptor(fd)
            raise
        return fd


class _QuarantineFileWriter:
    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._closed = False

    def write(self, data: bytes) -> int:
        try:
            return os.write(self._fd, data)
        except OSError as exc:
            raise QuarantineWriteFailedError("quarantine write failed") from exc

    def truncate_and_fsync(self, size: int) -> None:
        try:
            os.ftruncate(self._fd, size)
            os.fsync(self._fd)
            os.lseek(self._fd, size, os.SEEK_SET)
        except OSError as exc:
            raise QuarantineWriteFailedError("quarantine write failed") from exc

    def flush_and_fsync(self) -> None:
        try:
            os.fsync(self._fd)
        except OSError as exc:
            raise QuarantineWriteFailedError("quarantine write failed") from exc

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _close_descriptor(self._fd)


class _QuarantineFileReader:
    def __init__(self, fd: int, initial_stat: os.stat_result) -> None:
        self._fd = fd
        self._initial_fingerprint = _stat_fingerprint(initial_stat)
        self._size_bytes = initial_stat.st_size
        self._closed = False

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    @property
    def file_descriptor(self) -> int:
        return self._fd

    def read(self, size: int) -> bytes:
        try:
            return os.read(self._fd, size)
        except OSError as exc:
            raise QuarantineStateInconsistentError("quarantine state inconsistent") from exc

    def seek_start(self) -> None:
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
        except OSError as exc:
            raise QuarantineStateInconsistentError("quarantine state inconsistent") from exc

    def verify_still_consistent(self) -> None:
        try:
            stat_result = os.fstat(self._fd)
            _validate_regular_file(stat_result)
        except OSError as exc:
            raise QuarantineStateInconsistentError("quarantine state inconsistent") from exc
        if _stat_fingerprint(stat_result) != self._initial_fingerprint:
            raise QuarantineStateInconsistentError("quarantine state inconsistent")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _close_descriptor(self._fd)


def _filename(storage_key: UploadStorageKey) -> str:
    return f"{storage_key.value}{_PART_SUFFIX}"


def _open_file(root_fd: int, filename: str, flags: int, *, exists_ok: bool) -> int:
    open_flags = flags
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        return os.open(filename, open_flags, 0o600, dir_fd=root_fd)
    except OSError as exc:
        if exists_ok and exc.errno == errno.ENOENT:
            raise FileNotFoundError(filename) from exc
        raise


def _validate_regular_file(stat_result: os.stat_result) -> None:
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise QuarantineStateInconsistentError("quarantine state inconsistent")


def _stat_fingerprint(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_size,
        getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)),
    )


def _close_descriptor(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
