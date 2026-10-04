"""Generic catalog backup transfer primitives.

Owns only shared concepts: bundle identity capture/matching, protocol-v1
framing, and Linux no-replace publication. Destination/store validation and
transport orchestration remain caller-specific.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import select
import time
from typing import Any, BinaryIO

from kronika.infrastructure.persistence.catalog_backup import (
    CATALOG_NAME,
    MANIFEST_NAME,
    BackupError,
    sha256_file,
    verify_catalog_backup,
)

# Fixed protocol-v1 magic (8 bytes). Non-configurable.
PROTOCOL_MAGIC = b"FNCBE01\0"
PROTOCOL_VERSION = 1
MAX_HEADER_BYTES = 8192
HEADER_REQUIRED_KEYS = frozenset(
    {
        "protocol_version",
        "bundle_id",
        "manifest_filename",
        "catalog_filename",
        "manifest_size_bytes",
        "manifest_sha256",
        "catalog_size_bytes",
        "catalog_sha256",
        "alembic_revision",
        "semantic",
    }
)
SHA256_PATTERN = frozenset("0123456789abcdef")
AT_FDCWD = -100
RENAME_NOREPLACE = 1
# Fail export when a real stdout pipe makes no forward byte progress for this long.
# Not caller-configurable; tests may monkeypatch the module constant.
EXPORT_STDOUT_NO_PROGRESS_SECONDS = 120
_STREAM_IO_CHUNK_BYTES = 64 * 1024


class TransferError(BackupError):
    """Sanitized generic transfer failure."""


@dataclass(frozen=True, slots=True)
class BundleIdentity:
    """Exact byte identity of a catalog backup bundle."""

    bundle_id: str
    catalog_sha256: str
    catalog_size_bytes: int
    alembic_revision: str
    manifest_sha256: str
    manifest_size_bytes: int


def capture_bundle_identity(
    bundle: Path | str,
    *,
    bundle_id: str | None = None,
) -> BundleIdentity:
    """Capture exact identity of a verified catalog backup bundle."""
    bundle_path = Path(bundle)
    verified = verify_catalog_backup(bundle_path)
    manifest_path = _require_regular_file(bundle_path / MANIFEST_NAME, description="manifest")
    catalog_path = _require_regular_file(bundle_path / CATALOG_NAME, description="catalog artifact")
    return BundleIdentity(
        bundle_id=bundle_id if bundle_id is not None else bundle_path.name,
        catalog_sha256=verified.catalog_sha256,
        catalog_size_bytes=verified.catalog_size_bytes,
        alembic_revision=verified.alembic_revision,
        manifest_sha256=sha256_file(manifest_path),
        manifest_size_bytes=manifest_path.stat().st_size,
    )


def identities_match(left: BundleIdentity, right: BundleIdentity) -> bool:
    """Return True when two bundle identities are exactly equal."""
    return (
        left.bundle_id == right.bundle_id
        and left.catalog_sha256 == right.catalog_sha256
        and left.catalog_size_bytes == right.catalog_size_bytes
        and left.alembic_revision == right.alembic_revision
        and left.manifest_sha256 == right.manifest_sha256
        and left.manifest_size_bytes == right.manifest_size_bytes
    )


def build_protocol_v1_header(
    identity: BundleIdentity,
    *,
    semantic: Mapping[str, Any],
) -> bytes:
    """Build canonical UTF-8 JSON header bytes for protocol v1."""
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "bundle_id": identity.bundle_id,
        "manifest_filename": MANIFEST_NAME,
        "catalog_filename": CATALOG_NAME,
        "manifest_size_bytes": identity.manifest_size_bytes,
        "manifest_sha256": identity.manifest_sha256,
        "catalog_size_bytes": identity.catalog_size_bytes,
        "catalog_sha256": identity.catalog_sha256,
        "alembic_revision": identity.alembic_revision,
        "semantic": _normalize_semantic(semantic),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_HEADER_BYTES:
        raise TransferError(
            "Protocol header exceeds maximum size.",
            error_code="TRANSFER_HEADER_TOO_LARGE",
        )
    return encoded


def parse_protocol_v1_header(header_bytes: bytes) -> dict[str, Any]:
    """Parse and strictly validate a protocol-v1 JSON header."""
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise TransferError(
            "Protocol header exceeds maximum size.",
            error_code="TRANSFER_HEADER_TOO_LARGE",
        )
    try:
        payload = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferError(
            "Protocol header is malformed.",
            error_code="TRANSFER_HEADER_MALFORMED",
        ) from exc
    if not isinstance(payload, dict):
        raise TransferError(
            "Protocol header is malformed.",
            error_code="TRANSFER_HEADER_MALFORMED",
        )
    keys = set(payload.keys())
    if keys != HEADER_REQUIRED_KEYS:
        if not HEADER_REQUIRED_KEYS.issubset(keys):
            raise TransferError(
                "Protocol header is missing required fields.",
                error_code="TRANSFER_HEADER_MISSING_FIELD",
            )
        raise TransferError(
            "Protocol header contains unknown fields.",
            error_code="TRANSFER_HEADER_UNKNOWN_FIELD",
        )
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise TransferError(
            "Unsupported transfer protocol version.",
            error_code="TRANSFER_PROTOCOL_UNSUPPORTED",
        )
    if payload.get("manifest_filename") != MANIFEST_NAME:
        raise TransferError(
            "Protocol manifest filename mismatch.",
            error_code="TRANSFER_FILENAME_MISMATCH",
        )
    if payload.get("catalog_filename") != CATALOG_NAME:
        raise TransferError(
            "Protocol catalog filename mismatch.",
            error_code="TRANSFER_FILENAME_MISMATCH",
        )
    bundle_id = payload.get("bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id or len(bundle_id) > 128:
        raise TransferError(
            "Protocol bundle identity is invalid.",
            error_code="TRANSFER_HEADER_INVALID",
        )
    for size_key in ("manifest_size_bytes", "catalog_size_bytes"):
        size = payload.get(size_key)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise TransferError(
                "Protocol size fields are invalid.",
                error_code="TRANSFER_HEADER_INVALID",
            )
    for digest_key in ("manifest_sha256", "catalog_sha256"):
        digest = payload.get(digest_key)
        if not isinstance(digest, str) or len(digest) != 64:
            raise TransferError(
                "Protocol digest fields are invalid.",
                error_code="TRANSFER_HEADER_INVALID",
            )
        if any(ch not in SHA256_PATTERN for ch in digest):
            raise TransferError(
                "Protocol digest fields are invalid.",
                error_code="TRANSFER_HEADER_INVALID",
            )
    revision = payload.get("alembic_revision")
    if not isinstance(revision, str) or not revision:
        raise TransferError(
            "Protocol revision is invalid.",
            error_code="TRANSFER_HEADER_INVALID",
        )
    semantic = payload.get("semantic")
    if not isinstance(semantic, dict):
        raise TransferError(
            "Protocol semantic evidence is invalid.",
            error_code="TRANSFER_HEADER_INVALID",
        )
    _normalize_semantic(semantic)  # validates shape
    return payload


def write_protocol_v1_stream(
    stdout: BinaryIO,
    *,
    identity: BundleIdentity,
    semantic: Mapping[str, Any],
    manifest_path: Path,
    catalog_path: Path,
) -> None:
    """Write a complete protocol-v1 stream to stdout and flush.

    Real file-descriptor stdout pipes use a monotonic no-progress write bound so
    a non-draining consumer cannot hold the caller’s operation lock indefinitely.
    """
    header = build_protocol_v1_header(identity, semantic=semantic)
    _write_bytes_with_no_progress(stdout, PROTOCOL_MAGIC)
    _write_bytes_with_no_progress(stdout, len(header).to_bytes(4, "big"))
    _write_bytes_with_no_progress(stdout, header)
    _stream_exact_file(
        manifest_path,
        stdout,
        expected_size=identity.manifest_size_bytes,
        expected_sha256=identity.manifest_sha256,
    )
    _stream_exact_file(
        catalog_path,
        stdout,
        expected_size=identity.catalog_size_bytes,
        expected_sha256=identity.catalog_sha256,
    )
    try:
        stdout.flush()
    except BrokenPipeError as exc:
        raise TransferError(
            "Transfer stdout pipe broke during export.",
            error_code="TRANSFER_BROKEN_PIPE",
        ) from exc


def read_exact(
    stream: BinaryIO,
    size: int,
    *,
    what: str,
    deadline: float | None = None,
) -> bytes:
    """Read exactly ``size`` bytes or fail on premature EOF / deadline."""
    if size < 0:
        raise TransferError(
            "Invalid transfer size.",
            error_code="TRANSFER_SIZE_INVALID",
        )
    if size == 0:
        return b""
    if deadline is None:
        return _read_exact_unbounded(stream, size, what=what)
    return _read_exact_with_deadline(stream, size, what=what, deadline=deadline)


def read_protocol_v1_preamble(
    stream: BinaryIO,
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Read magic + length-prefixed header and return validated header payload."""
    magic = read_exact(stream, len(PROTOCOL_MAGIC), what="protocol magic", deadline=deadline)
    if magic != PROTOCOL_MAGIC:
        raise TransferError(
            "Protocol magic mismatch.",
            error_code="TRANSFER_MAGIC_MISMATCH",
        )
    length_bytes = read_exact(stream, 4, what="header length", deadline=deadline)
    header_len = int.from_bytes(length_bytes, "big")
    if header_len > MAX_HEADER_BYTES:
        raise TransferError(
            "Protocol header exceeds maximum size.",
            error_code="TRANSFER_HEADER_TOO_LARGE",
        )
    header_bytes = read_exact(stream, header_len, what="protocol header", deadline=deadline)
    return parse_protocol_v1_header(header_bytes)


def receive_file_bytes(
    stream: BinaryIO,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    fsync: Any = os.fsync,
    deadline: float | None = None,
) -> None:
    """Receive exact file bytes into a new private destination with digest checks."""
    if destination.exists() or destination.is_symlink():
        raise TransferError(
            "Transfer staging path already exists.",
            error_code="TRANSFER_STAGE_EXISTS",
        )
    digest = hashlib.sha256()
    received = 0
    try:
        with open(destination, "xb", buffering=0) as handle:
            remaining = expected_size
            while remaining > 0:
                chunk = read_exact(
                    stream,
                    min(remaining, _STREAM_IO_CHUNK_BYTES),
                    what="transfer payload",
                    deadline=deadline,
                )
                handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                remaining -= len(chunk)
            handle.flush()
            fsync(handle.fileno())
        os.chmod(destination, 0o600)
    except TransferError:
        raise
    except FileExistsError as exc:
        raise TransferError(
            "Transfer staging path already exists.",
            error_code="TRANSFER_STAGE_EXISTS",
        ) from exc
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise TransferError(
                "Insufficient space for transfer staging.",
                error_code="TRANSFER_ENOSPC",
            ) from exc
        raise TransferError(
            "Transfer payload write failed.",
            error_code="TRANSFER_WRITE_FAILED",
        ) from exc
    if received != expected_size:
        raise TransferError(
            "Transfer payload size mismatch.",
            error_code="TRANSFER_SIZE_MISMATCH",
        )
    if digest.hexdigest() != expected_sha256:
        raise TransferError(
            "Transfer payload digest mismatch.",
            error_code="TRANSFER_DIGEST_MISMATCH",
        )


def assert_stream_eof(stream: BinaryIO, *, deadline: float | None = None) -> None:
    """Fail when trailing bytes remain after the framed payload."""
    if deadline is None:
        trailing = stream.read(1)
    else:
        trailing = _read_at_most_with_deadline(stream, 1, what="trailing byte proof", deadline=deadline)
    if trailing:
        raise TransferError(
            "Unexpected trailing bytes after transfer payload.",
            error_code="TRANSFER_TRAILING_BYTES",
        )


def rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename with Linux RENAME_NOREPLACE semantics."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError as exc:
        raise TransferError(
            "Atomic no-replace publication is unsupported.",
            error_code="TRANSFER_ATOMIC_PUBLISH_UNSUPPORTED",
        ) from exc
    if not hasattr(libc, "renameat2"):
        raise TransferError(
            "Atomic no-replace publication is unsupported.",
            error_code="TRANSFER_ATOMIC_PUBLISH_UNSUPPORTED",
        )
    renameat2 = libc.renameat2
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        AT_FDCWD,
        os.fsencode(source),
        AT_FDCWD,
        os.fsencode(destination),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        raise FileExistsError(err, os.strerror(err), str(destination))
    if err in {errno.ENOSYS, errno.EINVAL}:
        raise TransferError(
            "Atomic no-replace publication is unsupported.",
            error_code="TRANSFER_ATOMIC_PUBLISH_UNSUPPORTED",
        )
    raise OSError(err, os.strerror(err))


def fsync_directory(path: Path, *, fsync: Any = os.fsync) -> None:
    """Fsync a directory inode."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fsync(fd)
    finally:
        os.close(fd)


def _stream_exact_file(
    path: Path,
    stdout: BinaryIO,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    digest = hashlib.sha256()
    written = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_STREAM_IO_CHUNK_BYTES)
            if not chunk:
                break
            _write_bytes_with_no_progress(stdout, chunk)
            digest.update(chunk)
            written += len(chunk)
    if written != expected_size:
        raise TransferError(
            "Source file size changed during export.",
            error_code="TRANSFER_SOURCE_SIZE_CHANGED",
        )
    if digest.hexdigest() != expected_sha256:
        raise TransferError(
            "Source file digest changed during export.",
            error_code="TRANSFER_SOURCE_DIGEST_CHANGED",
        )


def _stream_fileno(stream: BinaryIO) -> int | None:
    try:
        fileno = stream.fileno()
    except (AttributeError, io.UnsupportedOperation, OSError):
        return None
    if not isinstance(fileno, int) or fileno < 0:
        return None
    return fileno


def _raise_timeout(what: str) -> None:
    raise TransferError(
        f"Transfer timed out while reading {what}.",
        error_code="TRANSFER_TIMEOUT",
    )


def _read_exact_unbounded(stream: BinaryIO, size: int, *, what: str) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(min(remaining, _STREAM_IO_CHUNK_BYTES))
        if not chunk:
            raise TransferError(
                f"Premature EOF while reading {what}.",
                error_code="TRANSFER_PREMATURE_EOF",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_exact_with_deadline(
    stream: BinaryIO,
    size: int,
    *,
    what: str,
    deadline: float,
) -> bytes:
    fileno = _stream_fileno(stream)
    if fileno is not None:
        return _read_exact_from_fd(fileno, size, what=what, deadline=deadline)
    # Non-fd streams (e.g. BytesIO tests): reads must return promptly. A missing
    # fileno never authorizes treating a real production pipe as unbounded.
    return _read_exact_non_fd_deadline(stream, size, what=what, deadline=deadline)


def _read_exact_from_fd(fileno: int, size: int, *, what: str, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        wait = deadline - time.monotonic()
        if wait <= 0:
            _raise_timeout(what)
        ready, _, _ = select.select([fileno], [], [], wait)
        if not ready:
            _raise_timeout(what)
        try:
            chunk = os.read(fileno, min(remaining, _STREAM_IO_CHUNK_BYTES))
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                continue
            raise TransferError(
                f"Transfer read failed while reading {what}.",
                error_code="TRANSFER_READ_FAILED",
            ) from exc
        if not chunk:
            raise TransferError(
                f"Premature EOF while reading {what}.",
                error_code="TRANSFER_PREMATURE_EOF",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_exact_non_fd_deadline(
    stream: BinaryIO,
    size: int,
    *,
    what: str,
    deadline: float,
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        if time.monotonic() >= deadline:
            _raise_timeout(what)
        chunk = stream.read(min(remaining, _STREAM_IO_CHUNK_BYTES))
        if not chunk:
            raise TransferError(
                f"Premature EOF while reading {what}.",
                error_code="TRANSFER_PREMATURE_EOF",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_at_most_with_deadline(
    stream: BinaryIO,
    size: int,
    *,
    what: str,
    deadline: float,
) -> bytes:
    """Read up to ``size`` bytes; empty result means EOF before any data."""
    if size <= 0:
        return b""
    fileno = _stream_fileno(stream)
    if fileno is None:
        if time.monotonic() >= deadline:
            _raise_timeout(what)
        return stream.read(size) or b""
    wait = deadline - time.monotonic()
    if wait <= 0:
        _raise_timeout(what)
    ready, _, _ = select.select([fileno], [], [], wait)
    if not ready:
        _raise_timeout(what)
    try:
        chunk = os.read(fileno, size)
    except InterruptedError:
        return b""
    except OSError as exc:
        if exc.errno == errno.EAGAIN:
            return b""
        raise TransferError(
            f"Transfer read failed while reading {what}.",
            error_code="TRANSFER_READ_FAILED",
        ) from exc
    return chunk


def _write_bytes_with_no_progress(stdout: BinaryIO, data: bytes) -> None:
    if not data:
        return
    fileno = _stream_fileno(stdout)
    if fileno is None:
        try:
            stdout.write(data)
        except BrokenPipeError as exc:
            raise TransferError(
                "Transfer stdout pipe broke during export.",
                error_code="TRANSFER_BROKEN_PIPE",
            ) from exc
        return
    _write_bytes_to_fd_with_no_progress(fileno, data)


def _write_bytes_to_fd_with_no_progress(fileno: int, data: bytes) -> None:
    no_progress = max(1, int(EXPORT_STDOUT_NO_PROGRESS_SECONDS))
    progress_deadline = time.monotonic() + no_progress
    flags = fcntl.fcntl(fileno, fcntl.F_GETFL)
    nonblocking = bool(flags & os.O_NONBLOCK)
    if not nonblocking:
        fcntl.fcntl(fileno, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    offset = 0
    try:
        while offset < len(data):
            remaining = progress_deadline - time.monotonic()
            if remaining <= 0:
                raise TransferError(
                    "Export stdout made no forward progress before the bound.",
                    error_code="TRANSFER_WRITE_NO_PROGRESS",
                )
            _, ready, _ = select.select([], [fileno], [], remaining)
            if not ready:
                raise TransferError(
                    "Export stdout made no forward progress before the bound.",
                    error_code="TRANSFER_WRITE_NO_PROGRESS",
                )
            try:
                written = os.write(fileno, data[offset : offset + _STREAM_IO_CHUNK_BYTES])
            except BlockingIOError:
                continue
            except InterruptedError:
                continue
            except BrokenPipeError as exc:
                raise TransferError(
                    "Transfer stdout pipe broke during export.",
                    error_code="TRANSFER_BROKEN_PIPE",
                ) from exc
            except OSError as exc:
                if exc.errno in {errno.EPIPE, errno.ECONNRESET}:
                    raise TransferError(
                        "Transfer stdout pipe broke during export.",
                        error_code="TRANSFER_BROKEN_PIPE",
                    ) from exc
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    continue
                raise TransferError(
                    "Transfer stdout write failed during export.",
                    error_code="TRANSFER_WRITE_FAILED",
                ) from exc
            if written <= 0:
                raise TransferError(
                    "Transfer stdout write failed during export.",
                    error_code="TRANSFER_WRITE_FAILED",
                )
            offset += written
            progress_deadline = time.monotonic() + no_progress
    finally:
        if not nonblocking:
            try:
                fcntl.fcntl(fileno, fcntl.F_SETFL, flags)
            except OSError:
                pass


def _normalize_semantic(semantic: Mapping[str, Any]) -> dict[str, Any]:
    if set(semantic.keys()) != {"alembic_revision", "counts"}:
        raise TransferError(
            "Protocol semantic evidence is invalid.",
            error_code="TRANSFER_HEADER_INVALID",
        )
    revision = semantic.get("alembic_revision")
    counts = semantic.get("counts")
    if not isinstance(revision, str) or not revision:
        raise TransferError(
            "Protocol semantic evidence is invalid.",
            error_code="TRANSFER_HEADER_INVALID",
        )
    if not isinstance(counts, dict):
        raise TransferError(
            "Protocol semantic evidence is invalid.",
            error_code="TRANSFER_HEADER_INVALID",
        )
    normalized_counts: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise TransferError(
                "Protocol semantic evidence is invalid.",
                error_code="TRANSFER_HEADER_INVALID",
            )
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TransferError(
                "Protocol semantic evidence is invalid.",
                error_code="TRANSFER_HEADER_INVALID",
            )
        normalized_counts[key] = value
    return {"alembic_revision": revision, "counts": normalized_counts}


def _require_regular_file(path: Path, *, description: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise TransferError(
            f"Invalid {description}.",
            error_code="TRANSFER_PATH_INVALID",
        )
    return path
