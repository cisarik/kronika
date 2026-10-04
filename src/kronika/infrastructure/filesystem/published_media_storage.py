"""Crash-safe filesystem adapter for server-owned published originals."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat as stat_module
import threading

from kronika.application.ports.published_media_storage import (
    PublishedMediaInsufficientSpaceError,
    PublishedMediaStorageError,
    PublishedMediaStorageUnavailableError,
    PublishedMediaTargetCollisionError,
    PublishedMediaVerificationError,
    PublishedMediaWriteError,
)
from kronika.application.ports.quarantine_storage import QuarantineReader
from kronika.application.root_paths import roots_overlap
from kronika.domain.identities import LibraryId
from kronika.domain.upload_publications import UploadPublication

_COPY_CHUNK_SIZE = 1024 * 1024
_TEMP_SUFFIX = ".publish.tmp"


class FilesystemPublishedMediaStorage:
    """Publish into one pre-existing explicit non-symlink POSIX library root."""

    def __init__(
        self,
        destination_id: LibraryId,
        root: Path,
        *,
        forbidden_roots: tuple[Path, ...] = (),
        min_free_space_reserve_bytes: int = 0,
    ) -> None:
        if (
            isinstance(min_free_space_reserve_bytes, bool)
            or min_free_space_reserve_bytes < 0
        ):
            raise ValueError("min free space reserve bytes must be non-negative")
        self._destination_id = destination_id
        self._root = root
        self._forbidden_roots = forbidden_roots
        self._min_free_space_reserve_bytes = min_free_space_reserve_bytes
        self._stop = threading.Event()

    def request_stop(self) -> None:
        """Request cooperative interruption at copy chunk boundaries."""
        self._stop.set()

    @property
    def destination_id(self) -> LibraryId:
        return self._destination_id

    @property
    def root(self) -> Path:
        return self._root

    @property
    def root_available(self) -> bool:
        try:
            root_fd = self._open_root()
        except PublishedMediaStorageUnavailableError:
            return False
        _close_descriptor(root_fd)
        return True

    def available_bytes(self) -> int:
        self._validate_root_path()
        try:
            return shutil.disk_usage(self._root).free
        except OSError as exc:
            raise PublishedMediaStorageUnavailableError(
                "published media storage unavailable"
            ) from exc

    def verify_target(self, publication: UploadPublication) -> bool:
        self._require_destination(publication)
        root_fd = self._open_root()
        try:
            return self._verify_final(root_fd, publication)
        finally:
            _close_descriptor(root_fd)

    def publish_from_reader(
        self,
        publication: UploadPublication,
        source: QuarantineReader,
    ) -> None:
        self._require_destination(publication)
        root_fd = self._open_root()
        temp_name = _temporary_name(publication)
        try:
            if self._verify_final(root_fd, publication):
                _unlink_owned_temporary(root_fd, temp_name)
                _fsync_directory(root_fd)
                return
            if source.size_bytes != publication.expected_size_bytes:
                raise PublishedMediaVerificationError(
                    "published media verification failed"
                )
            # Publication always copies source bytes into a destination temporary
            # before hardlinking within the same destination filesystem. Charge the
            # full source size plus reserve; do not assume rename/hardlink from
            # quarantine can avoid allocation.
            self._ensure_space_for_copy(publication.expected_size_bytes)
            try:
                temp_fd = _open_owned_temporary(root_fd, temp_name)
                try:
                    _copy_and_verify_source(
                        temp_fd,
                        source,
                        publication,
                        stop_event=self._stop,
                    )
                    _fsync_file(temp_fd)
                finally:
                    _close_descriptor(temp_fd)
            except PublishedMediaInsufficientSpaceError:
                _unlink_owned_temporary(root_fd, temp_name)
                raise
            try:
                os.link(
                    temp_name,
                    publication.relative_path.value,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                if not self._verify_final(root_fd, publication):
                    raise PublishedMediaTargetCollisionError(
                        "published media target collision"
                    )
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    if not self._verify_final(root_fd, publication):
                        raise PublishedMediaTargetCollisionError(
                            "published media target collision"
                        ) from exc
                elif exc.errno == errno.ENOSPC:
                    _unlink_owned_temporary(root_fd, temp_name)
                    raise PublishedMediaInsufficientSpaceError(
                        "insufficient published media storage"
                    ) from exc
                else:
                    raise PublishedMediaWriteError(
                        "published media write failed"
                    ) from exc
            _fsync_directory(root_fd)
            if not self._verify_final(root_fd, publication):
                raise PublishedMediaVerificationError(
                    "published media verification failed"
                )
            _unlink_owned_temporary(root_fd, temp_name)
            _fsync_directory(root_fd)
        finally:
            _close_descriptor(root_fd)

    def _ensure_space_for_copy(self, requested_bytes: int) -> None:
        if isinstance(requested_bytes, bool) or requested_bytes < 0:
            raise PublishedMediaVerificationError(
                "published media verification failed"
            )
        try:
            available = self.available_bytes()
        except PublishedMediaStorageUnavailableError:
            raise
        required = requested_bytes + self._min_free_space_reserve_bytes
        if required > available:
            raise PublishedMediaInsufficientSpaceError(
                "insufficient published media storage"
            )

    def _require_destination(self, publication: UploadPublication) -> None:
        if publication.destination_id != self._destination_id:
            raise PublishedMediaStorageUnavailableError(
                "published media storage unavailable"
            )

    def _verify_final(
        self,
        root_fd: int,
        publication: UploadPublication,
    ) -> bool:
        try:
            fd = _open_readonly(root_fd, publication.relative_path.value)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise PublishedMediaTargetCollisionError(
                "published media target collision"
            ) from exc
        try:
            if not _descriptor_matches(fd, publication):
                raise PublishedMediaTargetCollisionError(
                    "published media target collision"
                )
            return True
        finally:
            _close_descriptor(fd)

    def _open_root(self) -> int:
        self._validate_root_path()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(str(self._root), flags)
        except OSError as exc:
            raise PublishedMediaStorageUnavailableError(
                "published media storage unavailable"
            ) from exc
        try:
            stat_result = os.fstat(fd)
            if not stat_module.S_ISDIR(stat_result.st_mode):
                raise PublishedMediaStorageUnavailableError(
                    "published media storage unavailable"
                )
        except Exception:
            _close_descriptor(fd)
            raise
        return fd

    def _validate_root_path(self) -> None:
        if not isinstance(self._root, Path) or not self._root.is_absolute():
            raise PublishedMediaStorageUnavailableError(
                "published media storage unavailable"
            )
        try:
            if (
                self._root.is_symlink()
                or not self._root.exists()
                or not self._root.is_dir()
                or self._root.resolve(strict=True) != self._root
                or not os.access(self._root, os.W_OK | os.X_OK)
            ):
                raise PublishedMediaStorageUnavailableError(
                    "published media storage unavailable"
                )
            if any(roots_overlap(self._root, other) for other in self._forbidden_roots):
                raise PublishedMediaStorageUnavailableError(
                    "published media storage unavailable"
                )
        except OSError as exc:
            raise PublishedMediaStorageUnavailableError(
                "published media storage unavailable"
            ) from exc


def _open_owned_temporary(root_fd: int, name: str) -> int:
    flags = os.O_WRONLY | os.O_NOFOLLOW
    try:
        fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd)
    except FileExistsError:
        try:
            fd = os.open(name, flags, dir_fd=root_fd)
        except OSError as exc:
            raise PublishedMediaWriteError("published media write failed") from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise PublishedMediaInsufficientSpaceError(
                "insufficient published media storage"
            ) from exc
        raise PublishedMediaWriteError("published media write failed") from exc
    try:
        stat_result = os.fstat(fd)
        if (
            not stat_module.S_ISREG(stat_result.st_mode)
            or stat_module.S_IMODE(stat_result.st_mode) != 0o600
            or stat_result.st_nlink != 1
        ):
            raise PublishedMediaWriteError("published media write failed")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except Exception:
        _close_descriptor(fd)
        raise


def _copy_and_verify_source(
    target_fd: int,
    source: QuarantineReader,
    publication: UploadPublication,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    digest = hashlib.sha256()
    copied = 0
    try:
        source.seek_start()
        while True:
            if stop_event is not None and stop_event.is_set():
                raise PublishedMediaWriteError("published media write failed")
            chunk = source.read(_COPY_CHUNK_SIZE)
            if not chunk:
                break
            copied += len(chunk)
            if copied > publication.expected_size_bytes:
                raise PublishedMediaVerificationError(
                    "published media verification failed"
                )
            digest.update(chunk)
            _write_all(target_fd, chunk)
        source.verify_still_consistent()
        stat_result = os.fstat(target_fd)
    except PublishedMediaStorageError:
        raise
    except Exception as exc:
        raise PublishedMediaVerificationError(
            "published media verification failed"
        ) from exc
    if (
        copied != publication.expected_size_bytes
        or stat_result.st_size != publication.expected_size_bytes
        or digest.hexdigest() != publication.checksum_hex
        or not stat_module.S_ISREG(stat_result.st_mode)
    ):
        raise PublishedMediaVerificationError("published media verification failed")


def _descriptor_matches(fd: int, publication: UploadPublication) -> bool:
    try:
        initial = os.fstat(fd)
        if (
            not stat_module.S_ISREG(initial.st_mode)
            or initial.st_size != publication.expected_size_bytes
        ):
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, _COPY_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > publication.expected_size_bytes:
                return False
            digest.update(chunk)
        final = os.fstat(fd)
    except OSError as exc:
        raise PublishedMediaVerificationError(
            "published media verification failed"
        ) from exc
    return (
        size == publication.expected_size_bytes
        and digest.hexdigest() == publication.checksum_hex
        and _stat_fingerprint(initial) == _stat_fingerprint(final)
    )


def _open_readonly(root_fd: int, name: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(name) from exc
        raise


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise PublishedMediaInsufficientSpaceError(
                    "insufficient published media storage"
                ) from exc
            raise PublishedMediaWriteError("published media write failed") from exc
        if written <= 0:
            raise PublishedMediaWriteError("published media write failed")
        view = view[written:]


def _fsync_file(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise PublishedMediaInsufficientSpaceError(
                "insufficient published media storage"
            ) from exc
        raise PublishedMediaWriteError("published media write failed") from exc


def _fsync_directory(root_fd: int) -> None:
    try:
        os.fsync(root_fd)
    except OSError as exc:
        raise PublishedMediaWriteError("published media write failed") from exc


def _unlink_owned_temporary(root_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=root_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PublishedMediaWriteError("published media write failed") from exc


def _temporary_name(publication: UploadPublication) -> str:
    return f".{publication.publication_id.value.hex}{_TEMP_SUFFIX}"


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
