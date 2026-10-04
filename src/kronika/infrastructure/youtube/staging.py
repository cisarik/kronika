"""Descriptor-oriented private staging for YouTube acquisition."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import re
import stat as stat_module

from kronika.application.ports.youtube_staging import (
    YouTubeStagedArtifactReader,
    YouTubeStagingInconsistentError,
    YouTubeStagingUnavailableError,
)

ARTIFACT_FILENAME = "artifact.mp4"
_STAGING_KEY_PATTERN = re.compile(r"[0-9a-f]{32}")
_OWNED_ENTRY_PATTERN = re.compile(r"artifact(?:[.][A-Za-z0-9_-]+)*")


class FilesystemYouTubeStaging:
    """Private root with opaque claim directories and fixed final filename."""

    def __init__(
        self,
        root: Path,
        *,
        forbidden_roots: tuple[Path, ...] = (),
        artifact_filename: str = ARTIFACT_FILENAME,
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise YouTubeStagingUnavailableError("YouTube staging unavailable.")
        if (
            not isinstance(artifact_filename, str)
            or _OWNED_ENTRY_PATTERN.fullmatch(artifact_filename) is None
        ):
            raise YouTubeStagingUnavailableError("YouTube staging unavailable.")
        self._root = Path(os.path.abspath(root))
        self._artifact_filename = artifact_filename
        self._forbidden_roots = tuple(
            Path(os.path.abspath(path)) for path in forbidden_roots
        )
        if any(_paths_overlap(self._root, path) for path in self._forbidden_roots):
            raise YouTubeStagingUnavailableError("YouTube staging unavailable.")

    @property
    def root(self) -> Path:
        return self._root

    @property
    def root_available(self) -> bool:
        try:
            root_fd = self._open_root()
        except YouTubeStagingUnavailableError:
            return False
        _close_descriptor(root_fd)
        return True

    def prepare(self, staging_key: str) -> Path:
        _validate_staging_key(staging_key)
        root_fd = self._open_root()
        try:
            try:
                os.mkdir(staging_key, mode=0o700, dir_fd=root_fd)
                os.fsync(root_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise YouTubeStagingUnavailableError(
                    "YouTube staging unavailable."
                ) from exc
            claim_fd = _open_claim_directory(root_fd, staging_key)
            try:
                _validate_claim_entries(claim_fd)
            finally:
                _close_descriptor(claim_fd)
        finally:
            _close_descriptor(root_fd)
        return self._root / staging_key

    def claim_directory(self, staging_key: str) -> Path:
        self.prepare(staging_key)
        return self._root / staging_key

    def usage_bytes(self, staging_key: str) -> int:
        root_fd, claim_fd = self._open_claim(staging_key)
        try:
            total = 0
            for name in os.listdir(claim_fd):
                _validate_owned_entry_name(name)
                try:
                    stat_result = os.stat(
                        name,
                        dir_fd=claim_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise YouTubeStagingInconsistentError(
                        "YouTube staging inconsistent."
                    ) from exc
                _validate_regular_owned_file(stat_result)
                total += stat_result.st_size
            return total
        finally:
            _close_descriptor(claim_fd)
            _close_descriptor(root_fd)

    def available_bytes(self) -> int:
        root_fd = self._open_root()
        try:
            statvfs = os.fstatvfs(root_fd)
            return statvfs.f_bavail * statvfs.f_frsize
        except OSError as exc:
            raise YouTubeStagingUnavailableError(
                "YouTube staging unavailable."
            ) from exc
        finally:
            _close_descriptor(root_fd)

    def artifact_size(self, staging_key: str) -> int | None:
        try:
            reader = self.open_artifact(staging_key)
        except FileNotFoundError:
            return None
        try:
            return reader.size_bytes
        finally:
            reader.close()

    def open_artifact(
        self,
        staging_key: str,
        *,
        expected_size_bytes: int | None = None,
    ) -> YouTubeStagedArtifactReader:
        if expected_size_bytes is not None and (
            isinstance(expected_size_bytes, bool)
            or not isinstance(expected_size_bytes, int)
            or expected_size_bytes <= 0
        ):
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            )
        root_fd, claim_fd = self._open_claim(staging_key)
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(self._artifact_filename, flags, dir_fd=claim_fd)
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    raise FileNotFoundError(self._artifact_filename) from exc
                raise YouTubeStagingInconsistentError(
                    "YouTube staging inconsistent."
                ) from exc
            try:
                stat_result = os.fstat(fd)
                _validate_regular_owned_file(stat_result)
                if (
                    expected_size_bytes is not None
                    and stat_result.st_size != expected_size_bytes
                ):
                    raise YouTubeStagingInconsistentError(
                        "YouTube staging inconsistent."
                    )
                reader = _FilesystemStagedArtifactReader(
                    fd,
                    claim_fd,
                    stat_result,
                    artifact_filename=self._artifact_filename,
                )
                claim_fd = -1
                return reader
            except BaseException:
                _close_descriptor(fd)
                raise
        finally:
            if claim_fd >= 0:
                _close_descriptor(claim_fd)
            _close_descriptor(root_fd)

    def cleanup(self, staging_key: str) -> None:
        _validate_staging_key(staging_key)
        root_fd = self._open_root()
        claim_fd: int | None = None
        try:
            try:
                claim_fd = _open_claim_directory(root_fd, staging_key)
            except FileNotFoundError:
                return
            for name in os.listdir(claim_fd):
                _validate_owned_entry_name(name)
                try:
                    stat_result = os.stat(
                        name,
                        dir_fd=claim_fd,
                        follow_symlinks=False,
                    )
                    if stat_module.S_ISDIR(stat_result.st_mode):
                        raise YouTubeStagingInconsistentError(
                            "YouTube staging inconsistent."
                        )
                    os.unlink(name, dir_fd=claim_fd)
                except YouTubeStagingInconsistentError:
                    raise
                except OSError as exc:
                    raise YouTubeStagingInconsistentError(
                        "YouTube staging inconsistent."
                    ) from exc
            os.fsync(claim_fd)
            _close_descriptor(claim_fd)
            claim_fd = None
            try:
                os.rmdir(staging_key, dir_fd=root_fd)
                os.fsync(root_fd)
            except OSError as exc:
                raise YouTubeStagingInconsistentError(
                    "YouTube staging inconsistent."
                ) from exc
        finally:
            if claim_fd is not None:
                _close_descriptor(claim_fd)
            _close_descriptor(root_fd)

    def _open_claim(self, staging_key: str) -> tuple[int, int]:
        _validate_staging_key(staging_key)
        root_fd = self._open_root()
        try:
            claim_fd = _open_claim_directory(root_fd, staging_key)
        except BaseException:
            _close_descriptor(root_fd)
            raise
        return root_fd, claim_fd

    def _open_root(self) -> int:
        try:
            stat_result = os.lstat(self._root)
        except OSError as exc:
            raise YouTubeStagingUnavailableError(
                "YouTube staging unavailable."
            ) from exc
        if (
            not stat_module.S_ISDIR(stat_result.st_mode)
            or stat_module.S_ISLNK(stat_result.st_mode)
            or stat_result.st_mode & 0o077
        ):
            raise YouTubeStagingUnavailableError("YouTube staging unavailable.")
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        root_fd: int | None = None
        try:
            root_fd = os.open(self._root, flags)
            opened_stat = os.fstat(root_fd)
            if (
                not stat_module.S_ISDIR(opened_stat.st_mode)
                or opened_stat.st_dev != stat_result.st_dev
                or opened_stat.st_ino != stat_result.st_ino
            ):
                raise YouTubeStagingUnavailableError(
                    "YouTube staging unavailable."
                )
            return root_fd
        except YouTubeStagingUnavailableError:
            if root_fd is not None:
                _close_descriptor(root_fd)
            raise
        except OSError as exc:
            if root_fd is not None:
                _close_descriptor(root_fd)
            raise YouTubeStagingUnavailableError(
                "YouTube staging unavailable."
            ) from exc


class _FilesystemStagedArtifactReader:
    def __init__(
        self,
        fd: int,
        claim_fd: int,
        initial_stat: os.stat_result,
        *,
        artifact_filename: str = ARTIFACT_FILENAME,
    ) -> None:
        self._fd = fd
        self._claim_fd = claim_fd
        self._artifact_filename = artifact_filename
        self._fingerprint = _stat_fingerprint(initial_stat)
        self._size_bytes = initial_stat.st_size
        self._closed = False

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    def read(self, size: int) -> bytes:
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            )
        try:
            return os.read(self._fd, size)
        except OSError as exc:
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            ) from exc

    def seek(self, offset: int) -> None:
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or offset > self._size_bytes
        ):
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            )
        try:
            os.lseek(self._fd, offset, os.SEEK_SET)
        except OSError as exc:
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            ) from exc

    def verify_still_consistent(self) -> None:
        try:
            stat_result = os.fstat(self._fd)
            _validate_regular_owned_file(stat_result)
        except OSError as exc:
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            ) from exc
        if _stat_fingerprint(stat_result) != self._fingerprint:
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            )
        try:
            path_stat = os.stat(
                self._artifact_filename,
                dir_fd=self._claim_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            ) from exc
        if (
            not stat_module.S_ISREG(path_stat.st_mode)
            or path_stat.st_dev != stat_result.st_dev
            or path_stat.st_ino != stat_result.st_ino
        ):
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _close_descriptor(self._fd)
            _close_descriptor(self._claim_fd)


def _open_claim_directory(root_fd: int, staging_key: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        claim_fd = os.open(staging_key, flags, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(staging_key) from exc
        raise YouTubeStagingInconsistentError(
            "YouTube staging inconsistent."
        ) from exc
    try:
        stat_result = os.fstat(claim_fd)
        if (
            not stat_module.S_ISDIR(stat_result.st_mode)
            or stat_result.st_mode & 0o077
        ):
            raise YouTubeStagingInconsistentError(
                "YouTube staging inconsistent."
            )
        return claim_fd
    except BaseException:
        _close_descriptor(claim_fd)
        raise


def _validate_claim_entries(claim_fd: int) -> None:
    for name in os.listdir(claim_fd):
        _validate_owned_entry_name(name)
        stat_result = os.stat(name, dir_fd=claim_fd, follow_symlinks=False)
        _validate_regular_owned_file(stat_result)


def _validate_regular_owned_file(stat_result: os.stat_result) -> None:
    if (
        not stat_module.S_ISREG(stat_result.st_mode)
        or stat_result.st_nlink != 1
        or stat_result.st_mode & 0o111
    ):
        raise YouTubeStagingInconsistentError("YouTube staging inconsistent.")


def _validate_owned_entry_name(name: object) -> None:
    if (
        not isinstance(name, str)
        or len(name) > 120
        or _OWNED_ENTRY_PATTERN.fullmatch(name) is None
    ):
        raise YouTubeStagingInconsistentError("YouTube staging inconsistent.")


def _validate_staging_key(staging_key: object) -> None:
    if (
        not isinstance(staging_key, str)
        or _STAGING_KEY_PATTERN.fullmatch(staging_key) is None
    ):
        raise YouTubeStagingInconsistentError("YouTube staging inconsistent.")


def _stat_fingerprint(
    stat_result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_nlink,
        stat_result.st_size,
        getattr(
            stat_result,
            "st_mtime_ns",
            int(stat_result.st_mtime * 1_000_000_000),
        ),
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _close_descriptor(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
