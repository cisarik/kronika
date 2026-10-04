"""Pillow-backed still-image analysis preparation without FFmpeg."""

from __future__ import annotations

import io
import stat
from pathlib import Path

from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

from kronika.application.library_scan import IMAGE_EXTENSIONS, LibraryScanCandidateKind
from kronika.application.media_analysis import (
    AGGREGATE_PNG_PAYLOAD_MAX_BYTES,
    INVALID_MEDIA_PATH_MESSAGE,
    MAX_OUTPUT_DIMENSION,
    PNG_PAYLOAD_MAX_BYTES,
    PREPARATION_FAILED_MESSAGE,
    PREPARATION_UNAVAILABLE_MESSAGE,
    REQUESTED_FRAME_COUNT,
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PreparedAnalysisResult,
    TechnicalMetadata,
    build_representative_frame,
)
from kronika.infrastructure.ai.image_derivative import VLM_SOURCE_MAX_PIXELS

_STILL_PREPARATION_MAX_BYTES = 1_073_741_824
_STILL_MAX_DIMENSION = 8192
_STILL_MAX_TOTAL_PIXELS = 33_177_600
_ALLOWED_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
}


def prepare_still_image_analysis(
    candidate_path: Path,
    relative_path: MediaRelativePath,
) -> PreparedAnalysisResult:
    """Prepare exactly one bounded still-image frame for provider transport."""
    try:
        metadata = candidate_path.lstat()
    except OSError as exc:
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
    if metadata.st_size < 1 or metadata.st_size > _STILL_PREPARATION_MAX_BYTES:
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
    suffix = candidate_path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    expected_format = _ALLOWED_FORMATS[suffix]
    try:
        payload = candidate_path.read_bytes()
    except OSError as exc:
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from exc
    if len(payload) != metadata.st_size:
        raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)

    previous_max_pixels = Image.MAX_IMAGE_PIXELS
    previous_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
    Image.MAX_IMAGE_PIXELS = _STILL_MAX_TOTAL_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            if source.format != expected_format:
                raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)
            oriented = ImageOps.exif_transpose(source)
            working = oriented.convert("RGB")
            width, height = working.size
            if width <= 0 or height <= 0:
                raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)
            if (
                width > _STILL_MAX_DIMENSION
                or height > _STILL_MAX_DIMENSION
                or width * height > _STILL_MAX_TOTAL_PIXELS
            ):
                raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)
            target_size = _bounded_output_size(width, height)
            if target_size != working.size:
                working = working.resize(target_size, Image.Resampling.LANCZOS)
            png_buffer = io.BytesIO()
            working.save(png_buffer, format="PNG", optimize=False)
            png_payload = png_buffer.getvalue()
            out_width, out_height = working.size
    except MediaAnalysisFailedError:
        raise
    except Image.DecompressionBombError as exc:
        raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE) from exc
    except (OSError, UnidentifiedImageError, ValueError, TypeError, SyntaxError) as exc:
        raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_pixels
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated

    if not png_payload or len(png_payload) > PNG_PAYLOAD_MAX_BYTES:
        raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)
    if len(png_payload) > AGGREGATE_PNG_PAYLOAD_MAX_BYTES:
        raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)
    if out_width * out_height > VLM_SOURCE_MAX_PIXELS:
        raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)

    frame = build_representative_frame(timestamp_ms=0, payload=png_payload)
    container = "jpeg" if expected_format == "JPEG" else "png"
    return PreparedAnalysisResult(
        relative_path=relative_path,
        candidate_kind=LibraryScanCandidateKind.IMAGE,
        technical_metadata=TechnicalMetadata(
            duration_ms=None,
            width=out_width,
            height=out_height,
            video_codec="still",
            container_formats=(container,),
            has_audio=False,
        ),
        representative_frames=(frame,),
        requested_frame_count=REQUESTED_FRAME_COUNT,
        warnings=(),
        ffprobe_version="pillow-still-image",
        ffmpeg_version="unused",
    )


def _bounded_output_size(width: int, height: int) -> tuple[int, int]:
    long_edge = max(width, height)
    max_edge = min(MAX_OUTPUT_DIMENSION, int(VLM_SOURCE_MAX_PIXELS**0.5))
    if long_edge <= max_edge and width * height <= VLM_SOURCE_MAX_PIXELS:
        return (width, height)
    scale = min(max_edge / long_edge, (VLM_SOURCE_MAX_PIXELS / (width * height)) ** 0.5)
    return (max(1, round(width * scale)), max(1, round(height * scale)))
