"""Filesystem and Pillow adapters for persistent gallery preview derivatives."""

from __future__ import annotations

import hashlib
import io
import os
import stat as stat_module
import uuid
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from kronika.application.gallery_preview import (
    GALLERY_PREVIEW_CACHE_FAILED_MESSAGE,
    GALLERY_PREVIEW_MEDIA_TYPE,
    GalleryPreviewFailedError,
    GalleryPreviewUnavailableError,
)
from kronika.application.media_analysis import RepresentativeFrame
from kronika.application.ports.gallery_preview import (
    GalleryPreviewImage,
    OpenedGalleryPreview,
)

GALLERY_PREVIEW_MAX_LONG_EDGE = 512
GALLERY_PREVIEW_JPEG_QUALITY = 82
GALLERY_PREVIEW_MAX_BYTES = 524_288
GALLERY_PREVIEW_SOURCE_MAX_PIXELS = 1024 * 1024
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class PillowGalleryPreviewEncoder:
    """Pillow-backed deterministic PNG-to-JPEG encoder for gallery previews."""

    def encode_frame(self, frame: RepresentativeFrame) -> GalleryPreviewImage:
        if not isinstance(frame, RepresentativeFrame):
            raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
        try:
            with Image.open(io.BytesIO(frame.payload)) as source:
                if source.format != "PNG":
                    raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
                source.load()
                width, height = source.size
                _validate_dimensions(width, height)
                working = source.convert("RGB")
                target_size = _target_size(width, height)
                if target_size != source.size:
                    working = working.resize(target_size, Image.Resampling.LANCZOS)
                output = io.BytesIO()
                working.save(
                    output,
                    format="JPEG",
                    quality=GALLERY_PREVIEW_JPEG_QUALITY,
                    subsampling="4:2:0",
                    progressive=False,
                    optimize=False,
                )
        except GalleryPreviewFailedError:
            raise
        except (OSError, UnidentifiedImageError, ValueError):
            raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None
        return _validate_jpeg(output.getvalue())


class FilesystemGalleryPreviewCache:
    """Server-owned filesystem cache for persistent gallery preview derivatives."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)
        if not self._root.is_absolute():
            raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)

    @property
    def root(self) -> Path:
        return self._root

    def contains_current(self, cache_key: str) -> bool:
        try:
            path = self._path_for_key(cache_key)
            _validate_existing_file(path, self._root)
            _validate_jpeg(path.read_bytes())
        except (OSError, GalleryPreviewUnavailableError, GalleryPreviewFailedError):
            return False
        return True

    def contains_any_for_location(self, algorithm_version: str, location_id: str) -> bool:
        try:
            location_dir = self._resolve_contained_directory_key(
                f"{algorithm_version}/{location_id}"
            )
        except (OSError, GalleryPreviewUnavailableError):
            return False
        if not location_dir.exists() or not location_dir.is_dir():
            return False
        try:
            for candidate in location_dir.glob("*.jpg"):
                try:
                    _validate_existing_file(candidate, self._root)
                    _validate_jpeg(candidate.read_bytes())
                    return True
                except (OSError, GalleryPreviewUnavailableError, GalleryPreviewFailedError):
                    continue
        except OSError:
            return False
        return False

    def publish(self, cache_key: str, image: GalleryPreviewImage) -> None:
        if image.media_type != GALLERY_PREVIEW_MEDIA_TYPE:
            raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
        _validate_jpeg(image.payload)
        final_path = self._path_for_key(cache_key)
        temp_path = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self._ensure_root()
            final_path.parent.mkdir(parents=True, exist_ok=True)
            _validate_contained(final_path.parent.resolve(strict=True), self._root)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(str(temp_path), flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(image.payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            _validate_existing_file(temp_path, self._root)
            _validate_jpeg(temp_path.read_bytes())
            os.replace(str(temp_path), str(final_path))
        except (GalleryPreviewUnavailableError, GalleryPreviewFailedError):
            raise
        except OSError:
            raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None
        finally:
            try:
                if temp_path.exists() or temp_path.is_symlink():
                    temp_path.unlink()
            except OSError:
                pass

    def open(self, cache_key: str) -> OpenedGalleryPreview:
        path = self._path_for_key(cache_key)
        try:
            _validate_existing_file(path, self._root)
            payload = path.read_bytes()
            image = _validate_jpeg(payload)
        except (OSError, GalleryPreviewFailedError, GalleryPreviewUnavailableError):
            raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None
        return OpenedGalleryPreview(
            media_type=image.media_type,
            byte_size=image.byte_size,
            etag=f'"{image.sha256}"',
            payload=image.payload,
            close=lambda: None,
        )

    def _ensure_root(self) -> None:
        try:
            if self._root.exists() and self._root.is_symlink():
                raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
            self._root.mkdir(parents=True, exist_ok=True)
            if not self._root.is_dir() or self._root.is_symlink():
                raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
        except OSError:
            raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None

    def _path_for_key(self, cache_key: str) -> Path:
        if not cache_key.endswith(".jpg"):
            raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
        return self._resolve_contained_directory_key(cache_key)

    def _resolve_contained_directory_key(self, cache_key: str) -> Path:
        parsed = PurePosixPath(cache_key)
        if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
            raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
        candidate = self._root.joinpath(*parsed.parts)
        _validate_contained(candidate.resolve(strict=False), self._root)
        return candidate


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
    if width * height > GALLERY_PREVIEW_SOURCE_MAX_PIXELS:
        raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)


def _target_size(width: int, height: int) -> tuple[int, int]:
    long_edge = max(width, height)
    if long_edge <= GALLERY_PREVIEW_MAX_LONG_EDGE:
        return (width, height)
    scale = GALLERY_PREVIEW_MAX_LONG_EDGE / long_edge
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def _validate_jpeg(payload: bytes) -> GalleryPreviewImage:
    if not isinstance(payload, bytes) or not payload:
        raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
    if len(payload) > GALLERY_PREVIEW_MAX_BYTES:
        raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
    if not payload.startswith(JPEG_SOI) or not payload.endswith(JPEG_EOI):
        raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
    try:
        with Image.open(io.BytesIO(payload)) as generated:
            if generated.format != "JPEG":
                raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
            generated.load()
            if generated.mode != "RGB":
                raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
            width, height = generated.size
            _validate_dimensions(width, height)
            if max(width, height) > GALLERY_PREVIEW_MAX_LONG_EDGE:
                raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
    except GalleryPreviewFailedError:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None
    return GalleryPreviewImage(
        media_type=GALLERY_PREVIEW_MEDIA_TYPE,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )


def _validate_existing_file(path: Path, root: Path) -> None:
    resolved = path.resolve(strict=True)
    _validate_contained(resolved, root)
    if path.is_symlink() or resolved.is_symlink():
        raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
    stat_result = os.stat(str(resolved), follow_symlinks=False)
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)


def _validate_contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        raise GalleryPreviewUnavailableError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None
