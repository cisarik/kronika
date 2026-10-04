"""Secure local filesystem adapter for adjacent portable media sidecars."""

from __future__ import annotations

import errno
import os
import secrets
import stat as stat_module
from typing import NoReturn

from kronika.application.ports.media_sidecar_store import (
    SIDECAR_LOCATION_NOT_WRITABLE,
    SIDECAR_UNAVAILABLE,
    SIDECAR_UNSAFE_TARGET,
    MediaSidecarStoreError,
    SidecarTargetKind,
    SidecarTargetObservation,
    accepted_sidecar_filenames,
)
from kronika.domain.libraries import LibraryPathFlavor, LibraryRoot
from kronika.domain.media import MediaRelativePath
from kronika.domain.media_sidecar import MAX_SIDECAR_BYTES, FrameNestMediaSidecarError, decode_media_sidecar

_OPEN_NOFOLLOW = os.O_RDONLY | os.O_NOFOLLOW
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_TEMP_PREFIX = ".framenest-sidecar."
_TEMP_SUFFIX = ".tmp"
_PRIVATE_MODE = 0o600
_INSTALLED_MODE = 0o644
_MALFORMED = "SIDECAR_MALFORMED"
_UNSAFE_MESSAGE = "Media sidecar target is unsafe."
_UNAVAILABLE_MESSAGE = "Media sidecar is not available."
_NOT_WRITABLE_MESSAGE = "Media sidecar location is not writable."
_MALFORMED_MESSAGE = "Media sidecar is malformed."
_UNSUPPORTED_MESSAGE = "Media sidecar is unsupported."
_WRITE_FAILED_MESSAGE = "Media sidecar write failed."


class FilesystemMediaSidecarStore:
    """Path-safe adjacent sidecar observer and atomic installer."""

    def observe_adjacent(
        self,
        root: LibraryRoot,
        media_relative_path: MediaRelativePath,
    ) -> SidecarTargetObservation:
        parent_fd, sidecar_names = _open_placement(root, media_relative_path)
        try:
            for sidecar_name in sidecar_names:
                observation = _observe_named(parent_fd, sidecar_name, missing_ok=True)
                if observation.kind is not SidecarTargetKind.MISSING:
                    return observation
            return SidecarTargetObservation(kind=SidecarTargetKind.MISSING)
        finally:
            _close_fd(parent_fd)

    def create_adjacent(
        self,
        root: LibraryRoot,
        media_relative_path: MediaRelativePath,
        payload: bytes,
    ) -> None:
        _install_adjacent(root, media_relative_path, payload, replace=False)

    def replace_adjacent(
        self,
        root: LibraryRoot,
        media_relative_path: MediaRelativePath,
        payload: bytes,
    ) -> None:
        _install_adjacent(root, media_relative_path, payload, replace=True)

    def observe_explicit(self, path: str) -> SidecarTargetObservation:
        if not isinstance(path, str) or not path:
            raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE)
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
        except OSError:
            raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
        if stat_module.S_ISLNK(status.st_mode) or not stat_module.S_ISREG(status.st_mode):
            raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
        try:
            fd = os.open(path, _OPEN_NOFOLLOW)
        except OSError as exc:
            _raise_os_error(exc, writable=False)
        try:
            return _read_regular_fd(fd)
        finally:
            _close_fd(fd)


def _native_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _open_placement(
    root: LibraryRoot,
    media_relative_path: MediaRelativePath,
) -> tuple[int, tuple[str, ...]]:
    if not isinstance(root, LibraryRoot) or root.flavor is not _native_flavor():
        raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE)
    if not isinstance(media_relative_path, MediaRelativePath):
        raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE)
    parts = media_relative_path.value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
    filename = parts[-1]
    parent_parts = parts[:-1]
    try:
        root_fd = os.open(root.path, _DIR_FLAGS)
    except OSError as exc:
        _raise_os_error(exc, writable=False)
    current_fd = root_fd
    try:
        for part in parent_parts:
            try:
                next_fd = os.open(part, _DIR_FLAGS, dir_fd=current_fd)
            except OSError as exc:
                _raise_os_error(exc, writable=False)
            _close_fd(current_fd)
            current_fd = next_fd
        try:
            media_fd = os.open(filename, _OPEN_NOFOLLOW, dir_fd=current_fd)
        except OSError as exc:
            _raise_os_error(exc, writable=False)
        try:
            media_stat = os.fstat(media_fd)
            if not stat_module.S_ISREG(media_stat.st_mode):
                raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
        finally:
            _close_fd(media_fd)
        owned = current_fd
        current_fd = -1
        return owned, accepted_sidecar_filenames(media_relative_path)
    except Exception:
        if current_fd >= 0:
            _close_fd(current_fd)
        raise


def _observe_named(parent_fd: int, name: str, *, missing_ok: bool) -> SidecarTargetObservation:
    try:
        status = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return SidecarTargetObservation(kind=SidecarTargetKind.MISSING)
        raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
    except OSError as exc:
        _raise_os_error(exc, writable=False)
    if stat_module.S_ISLNK(status.st_mode) or not stat_module.S_ISREG(status.st_mode):
        raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
    try:
        fd = os.open(name, _OPEN_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        _raise_os_error(exc, writable=False)
    try:
        return _read_regular_fd(fd)
    finally:
        _close_fd(fd)


def _read_regular_fd(fd: int) -> SidecarTargetObservation:
    try:
        status = os.fstat(fd)
    except OSError as exc:
        _raise_os_error(exc, writable=False)
    if not stat_module.S_ISREG(status.st_mode):
        raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
    if status.st_size > MAX_SIDECAR_BYTES:
        raise MediaSidecarStoreError(_MALFORMED_MESSAGE, error_code=_MALFORMED)
    chunks: list[bytes] = []
    remaining = MAX_SIDECAR_BYTES + 1
    while remaining > 0:
        try:
            chunk = os.read(fd, min(65_536, remaining))
        except OSError as exc:
            _raise_os_error(exc, writable=False)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_SIDECAR_BYTES:
        raise MediaSidecarStoreError(_MALFORMED_MESSAGE, error_code=_MALFORMED)
    return SidecarTargetObservation(kind=SidecarTargetKind.REGULAR, payload=payload)


def _install_adjacent(
    root: LibraryRoot,
    media_relative_path: MediaRelativePath,
    payload: bytes,
    *,
    replace: bool,
) -> None:
    if not isinstance(payload, bytes) or len(payload) > MAX_SIDECAR_BYTES:
        raise MediaSidecarStoreError(_MALFORMED_MESSAGE, error_code=_MALFORMED)
    parent_fd, sidecar_names = _open_placement(root, media_relative_path)
    sidecar_name = sidecar_names[0]
    temp_name = f"{_TEMP_PREFIX}{secrets.token_hex(8)}{_TEMP_SUFFIX}"
    temp_fd = -1
    try:
        existing = _sidecar_stat(parent_fd, sidecar_name)
        if replace:
            if existing is None or not stat_module.S_ISREG(existing.st_mode) or stat_module.S_ISLNK(existing.st_mode):
                raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
        elif existing is not None:
            raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET)
        try:
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _PRIVATE_MODE,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            _raise_os_error(exc, writable=True)
        _write_all(temp_fd, payload)
        try:
            os.fsync(temp_fd)
        except OSError as exc:
            _raise_os_error(exc, writable=True)
        _close_fd(temp_fd)
        temp_fd = -1
        completed = _observe_named(parent_fd, temp_name, missing_ok=False)
        if completed.payload != payload:
            raise MediaSidecarStoreError(_WRITE_FAILED_MESSAGE, error_code=SIDECAR_UNAVAILABLE)
        try:
            decode_media_sidecar(completed.payload)
        except FrameNestMediaSidecarError as exc:
            message = _MALFORMED_MESSAGE if exc.error_code == _MALFORMED else _UNSUPPORTED_MESSAGE
            raise MediaSidecarStoreError(message, error_code=exc.error_code) from None
        try:
            os.chmod(temp_name, _INSTALLED_MODE, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            _raise_os_error(exc, writable=True)
        try:
            os.replace(temp_name, sidecar_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError as exc:
            _raise_os_error(exc, writable=True)
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            _raise_os_error(exc, writable=True)
        installed = _observe_named(parent_fd, sidecar_name, missing_ok=False)
        if installed.payload != payload:
            raise MediaSidecarStoreError(_WRITE_FAILED_MESSAGE, error_code=SIDECAR_UNAVAILABLE)
    except MediaSidecarStoreError:
        raise
    except FrameNestMediaSidecarError as exc:
        message = _MALFORMED_MESSAGE if exc.error_code == _MALFORMED else _UNSUPPORTED_MESSAGE
        raise MediaSidecarStoreError(message, error_code=exc.error_code) from None
    except OSError as exc:
        _raise_os_error(exc, writable=True)
    except Exception:
        raise MediaSidecarStoreError(_WRITE_FAILED_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
    finally:
        if temp_fd >= 0:
            _close_fd(temp_fd)
        _unlink_owned_temp(parent_fd, temp_name)
        _close_fd(parent_fd)


def _sidecar_stat(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _raise_os_error(exc, writable=False)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(fd, view)
        except OSError as exc:
            _raise_os_error(exc, writable=True)
        if written <= 0:
            raise MediaSidecarStoreError(_WRITE_FAILED_MESSAGE, error_code=SIDECAR_UNAVAILABLE)
        view = view[written:]


def _unlink_owned_temp(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError:
        return


def _close_fd(fd: int) -> None:
    if fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        return


def _raise_os_error(exc: OSError, *, writable: bool) -> NoReturn:
    if exc.errno in {errno.ELOOP, errno.ENOTDIR} or getattr(exc, "winerror", None) == 1920:
        raise MediaSidecarStoreError(_UNSAFE_MESSAGE, error_code=SIDECAR_UNSAFE_TARGET) from None
    if writable and exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
        raise MediaSidecarStoreError(_NOT_WRITABLE_MESSAGE, error_code=SIDECAR_LOCATION_NOT_WRITABLE) from None
    if exc.errno == errno.ENOENT:
        raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
    if writable:
        raise MediaSidecarStoreError(_WRITE_FAILED_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
    raise MediaSidecarStoreError(_UNAVAILABLE_MESSAGE, error_code=SIDECAR_UNAVAILABLE) from None
