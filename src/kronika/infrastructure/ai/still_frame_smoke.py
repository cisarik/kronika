"""Non-persistent still-frame AI smoke preparation.

Pillow decoding and byte/pixel validation stay in the infrastructure boundary.
"""

from __future__ import annotations

import io
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    TechnicalMetadata,
    build_representative_frame,
    RepresentativeFrame,
)
from kronika.application.media_suggestion import (
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.infrastructure.ai.image_derivative import (
    VLM_JPEG_AGGREGATE_MAX_BYTES,
    VLM_JPEG_MAX_BYTES,
    VLM_JPEG_MAX_FRAMES,
    VLM_SOURCE_MAX_PIXELS,
)

STILL_FRAME_SMOKE_BASENAME = "still-frame-smoke.jpg"
STILL_FRAME_SMOKE_INVALID_MESSAGE = "Invalid still-frame smoke input."
ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
MAX_STILL_FRAMES = VLM_JPEG_MAX_FRAMES
MAX_BYTES_PER_IMAGE = VLM_JPEG_MAX_BYTES
MAX_AGGREGATE_INPUT_BYTES = VLM_JPEG_AGGREGATE_MAX_BYTES


class FrameNestStillFrameSmokeError(ValueError):
    """Sanitized error raised when still-frame smoke input is invalid."""


@dataclass(frozen=True, slots=True)
class StillFrameSmokeImage:
    """One validated local still image prepared for smoke transport."""

    path: Path
    width: int
    height: int
    format_name: str
    byte_size: int
    frame: RepresentativeFrame


def prepare_still_frame_smoke_images(paths: Sequence[Path]) -> tuple[StillFrameSmokeImage, ...]:
    """Load and validate one to three local still images without persistence."""
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    if not paths or len(paths) > MAX_STILL_FRAMES:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    prepared: list[StillFrameSmokeImage] = []
    aggregate = 0
    for index, path in enumerate(paths):
        image = _load_one_still_image(path, ordinal=index)
        aggregate += image.byte_size
        if aggregate > MAX_AGGREGATE_INPUT_BYTES:
            raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
        prepared.append(image)
    return tuple(prepared)


def build_still_frame_smoke_request(
    images: tuple[StillFrameSmokeImage, ...],
) -> MediaSuggestionRequest:
    """Build one provider-neutral suggestion request from validated still frames."""
    if not images or len(images) > MAX_STILL_FRAMES:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    first = images[0]
    try:
        return MediaSuggestionRequest(
            basename=STILL_FRAME_SMOKE_BASENAME,
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=TechnicalMetadata(
                duration_ms=None,
                width=first.width,
                height=first.height,
                video_codec="still",
                container_formats=("jpeg",),
                has_audio=False,
            ),
            representative_frames=tuple(image.frame for image in images),
            prompt_version=PROMPT_VERSION,
        )
    except Exception as exc:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE) from exc


def _load_one_still_image(path: Path, *, ordinal: int) -> StillFrameSmokeImage:
    if not isinstance(path, Path):
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    if metadata.st_size < 1 or metadata.st_size > MAX_BYTES_PER_IMAGE:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE) from exc
    if len(payload) != metadata.st_size or len(payload) > MAX_BYTES_PER_IMAGE:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            format_name = source.format
            if format_name not in {"JPEG", "PNG"}:
                raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
            if suffix in {".jpg", ".jpeg"} and format_name != "JPEG":
                raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
            if suffix == ".png" and format_name != "PNG":
                raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > VLM_SOURCE_MAX_PIXELS:
                raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE)
            png_buffer = io.BytesIO()
            source.convert("RGB").save(png_buffer, format="PNG", optimize=False)
            png_payload = png_buffer.getvalue()
    except FrameNestStillFrameSmokeError:
        raise
    except (OSError, UnidentifiedImageError, ValueError, TypeError) as exc:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE) from exc
    try:
        frame = build_representative_frame(
            timestamp_ms=ordinal * 1000,
            payload=png_payload,
        )
    except Exception as exc:
        raise FrameNestStillFrameSmokeError(STILL_FRAME_SMOKE_INVALID_MESSAGE) from exc
    return StillFrameSmokeImage(
        path=path,
        width=width,
        height=height,
        format_name=format_name.lower(),
        byte_size=len(payload),
        frame=frame,
    )
