"""Pillow and filesystem adapters for durable cover artifacts and thumbnails."""

from __future__ import annotations

import hashlib
import io
import os
import stat as stat_module
import uuid
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError

from kronika.application.media_analysis import RepresentativeFrame
from kronika.application.ports.cover_storage import (
    CoverArtifact,
    CoverStorageError,
    CoverThumbnailImage,
    CoverThumbnailUnavailableError,
    OpenedCoverThumbnail,
)
from kronika.domain.identities import MediaId
from kronika.domain.media_cover import (
    COVER_ARTIFACT_MEDIA_TYPE,
    COVER_ARTIFACT_PROFILE,
    COVER_THUMBNAIL_ALGORITHM,
)

COVER_ARTIFACT_MAX_LONG_EDGE = 1024
COVER_ARTIFACT_MAX_PIXELS = 1_048_576
COVER_ARTIFACT_MAX_BYTES = 2_097_152
COVER_ARTIFACT_JPEG_QUALITY = 88

COVER_THUMBNAIL_MAX_LONG_EDGE = 512
COVER_THUMBNAIL_MAX_BYTES = 524_288
COVER_THUMBNAIL_JPEG_QUALITY = 82

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

_SHA256_PATTERN_MESSAGE = "Cover image digest is invalid."


class PillowCoverEncoder:
    """Pillow-backed deterministic JPEG encoder for durable covers and thumbnails."""

    def encode_artifact_frame(self, frame: RepresentativeFrame) -> CoverArtifact:
        try:
            with Image.open(io.BytesIO(frame.payload)) as source:
                if source.format != "PNG":
                    raise CoverStorageError(_failure_message())
                source.load()
                width, height = source.size
                _validate_source_dimensions(width, height)
                working = ImageOps.exif_transpose(source).convert("RGB")
                target_size = _target_size(width, height, COVER_ARTIFACT_MAX_LONG_EDGE)
                if target_size != working.size:
                    working = working.resize(target_size, Image.Resampling.LANCZOS)
                payload = _encode_jpeg(
                    working,
                    quality=COVER_ARTIFACT_JPEG_QUALITY,
                    max_bytes=COVER_ARTIFACT_MAX_BYTES,
                    max_long_edge=COVER_ARTIFACT_MAX_LONG_EDGE,
                )
        except CoverStorageError:
            raise
        except (OSError, UnidentifiedImageError, ValueError):
            raise CoverStorageError(_failure_message()) from None
        final_width, final_height = _jpeg_dimensions(payload)
        return CoverArtifact(
            profile=COVER_ARTIFACT_PROFILE,
            media_type=COVER_ARTIFACT_MEDIA_TYPE,
            digest=hashlib.sha256(payload).hexdigest(),
            width=final_width,
            height=final_height,
            byte_size=len(payload),
            payload=payload,
        )

    def encode_thumbnail(self, artifact_payload: bytes) -> CoverThumbnailImage:
        try:
            source_width, source_height = _validate_jpeg(
                artifact_payload,
                max_bytes=COVER_ARTIFACT_MAX_BYTES,
                max_long_edge=COVER_ARTIFACT_MAX_LONG_EDGE,
            )
            with Image.open(io.BytesIO(artifact_payload)) as source:
                if source.format != "JPEG":
                    raise CoverStorageError(_failure_message())
                source.load()
                working = source.convert("RGB")
                target_size = _target_size(
                    source_width,
                    source_height,
                    COVER_THUMBNAIL_MAX_LONG_EDGE,
                )
                if target_size != working.size:
                    working = working.resize(target_size, Image.Resampling.LANCZOS)
                payload = _encode_jpeg(
                    working,
                    quality=COVER_THUMBNAIL_JPEG_QUALITY,
                    max_bytes=COVER_THUMBNAIL_MAX_BYTES,
                    max_long_edge=COVER_THUMBNAIL_MAX_LONG_EDGE,
                )
        except CoverStorageError:
            raise
        except (OSError, UnidentifiedImageError, ValueError):
            raise CoverStorageError(_failure_message()) from None
        return CoverThumbnailImage(
            media_type=COVER_ARTIFACT_MEDIA_TYPE,
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload,
        )


class FilesystemDurableCoverStorage:
    """Server-owned immutable content-addressed durable cover artifact storage."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)
        if not self._root.is_absolute():
            raise CoverStorageError(_failure_message())

    @property
    def root(self) -> Path:
        return self._root

    def publish(self, *, media_id: MediaId, artifact: CoverArtifact) -> None:
        if artifact.media_type != COVER_ARTIFACT_MEDIA_TYPE:
            raise CoverStorageError(_failure_message())
        if artifact.profile != COVER_ARTIFACT_PROFILE:
            raise CoverStorageError(_failure_message())
        _validate_jpeg(
            artifact.payload,
            max_bytes=COVER_ARTIFACT_MAX_BYTES,
            max_long_edge=COVER_ARTIFACT_MAX_LONG_EDGE,
        )
        if hashlib.sha256(artifact.payload).hexdigest() != artifact.digest:
            raise CoverStorageError(_failure_message())
        if artifact.byte_size != len(artifact.payload):
            raise CoverStorageError(_failure_message())
        final_path = self._path_for(media_id, artifact.digest)
        _no_clobber_publish(
            final_path=final_path,
            root=self._root,
            payload=artifact.payload,
            expected_digest=artifact.digest,
            validate=_durable_artifact_validator(),
        )

    def artifact_valid(self, *, media_id: MediaId, digest: str) -> bool:
        try:
            path = self._path_for(media_id, digest)
            _validate_existing_regular_file(path, self._root)
            observed = _validated_jpeg_digest(path.read_bytes())
        except (OSError, CoverStorageError, CoverThumbnailUnavailableError):
            return False
        return observed == digest

    def read_bytes(self, *, media_id: MediaId, digest: str) -> bytes:
        try:
            path = self._path_for(media_id, digest)
            _validate_existing_regular_file(path, self._root)
            payload = path.read_bytes()
            observed = _validated_jpeg_digest(payload)
        except (OSError, CoverStorageError, CoverThumbnailUnavailableError):
            raise CoverStorageError(_failure_message()) from None
        if observed != digest:
            raise CoverStorageError(_failure_message())
        return payload

    def _path_for(self, media_id: MediaId, digest: str) -> Path:
        if not _is_sha256(digest):
            raise CoverStorageError(_failure_message())
        return _contained_path(self._root, media_id.to_string(), f"{digest}.jpg")


class FilesystemCoverThumbnailCache:
    """Server-owned regenerable cache for cover thumbnails."""

    algorithm = COVER_THUMBNAIL_ALGORITHM

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)
        if not self._root.is_absolute():
            raise CoverStorageError(_failure_message())

    @property
    def root(self) -> Path:
        return self._root

    def key_for(self, *, media_id: MediaId, artifact_digest: str) -> str:
        if not _is_sha256(artifact_digest):
            raise CoverStorageError(_failure_message())
        return f"{COVER_THUMBNAIL_ALGORITHM}/{media_id.to_string()}/{artifact_digest}.jpg"

    def contains(self, cache_key: str) -> bool:
        try:
            path = _cache_path_for_key(self._root, cache_key)
            _validate_existing_regular_file(path, self._root)
            _validate_jpeg(
                path.read_bytes(),
                max_bytes=COVER_THUMBNAIL_MAX_BYTES,
                max_long_edge=COVER_THUMBNAIL_MAX_LONG_EDGE,
            )
        except (OSError, CoverStorageError, CoverThumbnailUnavailableError):
            return False
        return True

    def contains_many(self, cache_keys: tuple[str, ...]) -> set[str]:
        result: set[str] = set()
        for cache_key in cache_keys:
            if self.contains(cache_key):
                result.add(cache_key)
        return result

    def publish(self, cache_key: str, image: CoverThumbnailImage) -> None:
        if image.media_type != COVER_ARTIFACT_MEDIA_TYPE:
            raise CoverStorageError(_failure_message())
        _validate_jpeg(
            image.payload,
            max_bytes=COVER_THUMBNAIL_MAX_BYTES,
            max_long_edge=COVER_THUMBNAIL_MAX_LONG_EDGE,
        )
        if hashlib.sha256(image.payload).hexdigest() != image.sha256:
            raise CoverStorageError(_failure_message())
        final_path = _cache_path_for_key(self._root, cache_key)
        _no_clobber_publish(
            final_path=final_path,
            root=self._root,
            payload=image.payload,
            expected_digest=image.sha256,
            validate=_thumbnail_validator(),
        )

    def open(self, cache_key: str) -> OpenedCoverThumbnail:
        path = _cache_path_for_key(self._root, cache_key)
        try:
            _validate_existing_regular_file(path, self._root)
            payload = path.read_bytes()
            _validate_jpeg(
                payload,
                max_bytes=COVER_THUMBNAIL_MAX_BYTES,
                max_long_edge=COVER_THUMBNAIL_MAX_LONG_EDGE,
            )
        except (OSError, CoverThumbnailUnavailableError):
            raise CoverThumbnailUnavailableError(_failure_message()) from None
        except CoverStorageError:
            raise CoverThumbnailUnavailableError(_failure_message()) from None
        return OpenedCoverThumbnail(
            media_type=COVER_ARTIFACT_MEDIA_TYPE,
            byte_size=len(payload),
            payload=payload,
            close=lambda: None,
        )


def _durable_artifact_validator():
    def validate(payload: bytes) -> None:
        _validate_jpeg(
            payload,
            max_bytes=COVER_ARTIFACT_MAX_BYTES,
            max_long_edge=COVER_ARTIFACT_MAX_LONG_EDGE,
        )

    return validate


def _thumbnail_validator():
    def validate(payload: bytes) -> None:
        _validate_jpeg(
            payload,
            max_bytes=COVER_THUMBNAIL_MAX_BYTES,
            max_long_edge=COVER_THUMBNAIL_MAX_LONG_EDGE,
        )

    return validate


def _no_clobber_publish(
    *,
    final_path: Path,
    root: Path,
    payload: bytes,
    expected_digest: str,
    validate,
) -> None:
    temp_path = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.tmp")
    published = False
    try:
        _ensure_root(root)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        _validate_contained(final_path.parent.resolve(strict=True), root)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(temp_path), flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        try:
            validate(temp_path.read_bytes())
        except (CoverStorageError, OSError):
            raise CoverStorageError(_failure_message()) from None
        if hashlib.sha256(temp_path.read_bytes()).hexdigest() != expected_digest:
            raise CoverStorageError(_failure_message())
        try:
            os.link(str(temp_path), str(final_path), follow_symlinks=False)
            published = True
        except FileExistsError:
            try:
                _validate_existing_regular_file(final_path, root)
                final_payload = final_path.read_bytes()
                validate(final_payload)
            except (OSError, CoverStorageError, CoverThumbnailUnavailableError):
                raise CoverStorageError(_failure_message()) from None
            if hashlib.sha256(final_payload).hexdigest() != expected_digest:
                raise CoverStorageError(_failure_message())
        try:
            _fsync_directory(final_path.parent)
        except OSError:
            pass
    except CoverStorageError:
        raise
    except OSError:
        raise CoverStorageError(_failure_message()) from None
    finally:
        try:
            if temp_path.exists() or temp_path.is_symlink():
                temp_path.unlink()
        except OSError:
            pass


def _encode_jpeg(
    working: Image.Image,
    *,
    quality: int,
    max_bytes: int,
    max_long_edge: int,
) -> bytes:
    output = io.BytesIO()
    working.save(
        output,
        format="JPEG",
        quality=quality,
        subsampling="4:2:0",
        progressive=False,
        optimize=False,
    )
    payload = output.getvalue()
    _validate_jpeg(
        payload,
        max_bytes=max_bytes,
        max_long_edge=max_long_edge,
    )
    return payload


def _validate_source_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise CoverStorageError(_failure_message())
    if width * height > COVER_ARTIFACT_MAX_PIXELS:
        raise CoverStorageError(_failure_message())


def _validate_jpeg(
    payload: bytes,
    *,
    max_bytes: int,
    max_long_edge: int,
) -> tuple[int, int]:
    if not isinstance(payload, bytes) or not payload:
        raise CoverStorageError(_failure_message())
    if len(payload) > max_bytes:
        raise CoverStorageError(_failure_message())
    if not payload.startswith(JPEG_SOI) or not payload.endswith(JPEG_EOI):
        raise CoverStorageError(_failure_message())
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "JPEG":
                raise CoverStorageError(_failure_message())
            image.load()
            if image.mode != "RGB":
                raise CoverStorageError(_failure_message())
            width, height = image.size
            if width <= 0 or height <= 0:
                raise CoverStorageError(_failure_message())
            if width * height > COVER_ARTIFACT_MAX_PIXELS:
                raise CoverStorageError(_failure_message())
            if max(width, height) > max_long_edge:
                raise CoverStorageError(_failure_message())
            return width, height
    except CoverStorageError:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise CoverStorageError(_failure_message()) from None


def _jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    return _validate_jpeg(
        payload,
        max_bytes=COVER_ARTIFACT_MAX_BYTES,
        max_long_edge=COVER_ARTIFACT_MAX_LONG_EDGE,
    )


def _validated_jpeg_digest(payload: bytes) -> str:
    _validate_jpeg(
        payload,
        max_bytes=COVER_ARTIFACT_MAX_BYTES,
        max_long_edge=COVER_ARTIFACT_MAX_LONG_EDGE,
    )
    return hashlib.sha256(payload).hexdigest()


def _target_size(width: int, height: int, max_long_edge: int) -> tuple[int, int]:
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return (width, height)
    scale = max_long_edge / long_edge
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _contained_path(root: Path, *parts: str) -> Path:
    parsed = PurePosixPath(*parts)
    if parsed.is_absolute() or any(
        part in ("", ".", "..") for part in parsed.parts
    ):
        raise CoverStorageError(_failure_message())
    candidate = root.joinpath(*parsed.parts)
    _validate_contained(candidate.resolve(strict=False), root)
    return candidate


def _cache_path_for_key(root: Path, cache_key: str) -> Path:
    if not cache_key.endswith(".jpg"):
        raise CoverStorageError(_failure_message())
    return _contained_path(root, *cache_key.split("/"))


def _ensure_root(root: Path) -> None:
    try:
        if root.exists() and root.is_symlink():
            raise CoverStorageError(_failure_message())
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir() or root.is_symlink():
            raise CoverStorageError(_failure_message())
    except OSError:
        raise CoverStorageError(_failure_message()) from None


def _validate_existing_regular_file(path: Path, root: Path) -> None:
    resolved = path.resolve(strict=True)
    _validate_contained(resolved, root)
    if path.is_symlink() or resolved.is_symlink():
        raise CoverStorageError(_failure_message())
    stat_result = os.stat(str(resolved), follow_symlinks=False)
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise CoverStorageError(_failure_message())


def _validate_contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        raise CoverStorageError(_failure_message()) from None


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        raise
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _failure_message() -> str:
    return "Cover storage operation failed."
