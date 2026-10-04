"""Bounded movie-identification contact-sheet derivative.

Extracts a small set of representative frames, rejects near-black and duplicate
frames, and composes one bounded JPEG contact sheet for provider transport.
The original movie and audio never leave the local process.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

from kronika.application.media_analysis import (
    FFMPEG_FRAME_TIMEOUT_SECONDS,
    PNG_PAYLOAD_MAX_BYTES,
    SUBPROCESS_STDERR_MAX_BYTES,
    FrameNestMediaAnalysisError,
    RepresentativeFrame,
    TechnicalMetadata,
    build_representative_frame,
    deduplicate_representative_frames,
)
from kronika.domain.media_classification import CONTACT_SHEET_REQUESTED_FRAME_COUNT
from kronika.application.movie_identification import LocalMovieHints
from kronika.infrastructure.media_analysis.ffmpeg import (
    FRAME_EXTRACTION_FAILED_MESSAGE,
    INDIVIDUAL_FRAME_FAILED_WARNING,
    build_ffmpeg_frame_argv,
)
from kronika.infrastructure.media_analysis.process import (
    ProcessExecutionError,
    ProcessRunner,
)
from kronika.infrastructure.media_analysis.tools import sanitize_retained_stderr

CONTACT_SHEET_FAILED_MESSAGE = "Movie identification contact sheet failed."
CONTACT_SHEET_MAX_LONG_EDGE = 1280
CONTACT_SHEET_CELL_MAX_EDGE = 420
CONTACT_SHEET_JPEG_QUALITY = 82
CONTACT_SHEET_MAX_BYTES = 1_572_864
CONTACT_SHEET_MAX_SOURCE_PIXELS = 2_000_000
NEAR_BLACK_MEAN_THRESHOLD = 12.0
NEAR_BLACK_MAX_STDDEV = 8.0


@dataclass(frozen=True, slots=True)
class ContactSheetDerivative:
    """One ephemeral bounded JPEG contact sheet for movie identification."""

    width: int
    height: int
    mime_type: str
    sha256: str
    byte_size: int
    source_frame_count: int
    source_timestamps_ms: tuple[int, ...]
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.mime_type != "image/jpeg":
            raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)
        if self.byte_size != len(self.payload) or self.byte_size > CONTACT_SHEET_MAX_BYTES:
            raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)
        if max(self.width, self.height) > CONTACT_SHEET_MAX_LONG_EDGE:
            raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)
        if self.sha256 != hashlib.sha256(self.payload).hexdigest():
            raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)
        if not self.payload.startswith(b"\xff\xd8") or not self.payload.endswith(b"\xff\xd9"):
            raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)


def compute_movie_identification_timestamps_ms(duration_ms: int | None) -> tuple[int, ...]:
    """Return five timeline-stratified targets (opening through ending)."""
    if duration_ms is None or duration_ms <= 0:
        return (0,)
    # Variant B: opening, early-middle, middle, late-middle, ending.
    fractions = (0.02, 0.22, 0.45, 0.70, 0.96)
    seen: set[int] = set()
    ordered: list[int] = []
    for fraction in fractions:
        target = min(duration_ms - 1, max(0, int(duration_ms * fraction)))
        if target not in seen:
            seen.add(target)
            ordered.append(target)
    return tuple(ordered)


def is_near_black_png(payload: bytes) -> bool:
    """Return True when a PNG frame is black or near-black."""
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG":
                return True
            image.load()
            rgb = image.convert("RGB")
            stats = ImageStat.Stat(rgb)
            mean = sum(stats.mean) / 3.0
            stddev = sum(stats.stddev) / 3.0
            return mean <= NEAR_BLACK_MEAN_THRESHOLD and stddev <= NEAR_BLACK_MAX_STDDEV
    except (OSError, UnidentifiedImageError, ValueError):
        return True


def sanitize_local_hint(value: object, *, maximum: int = 120) -> str | None:
    """Sanitize one optional local hint for provider text, never a path."""
    if not isinstance(value, str):
        return None
    if "/" in value or "\\" in value:
        return None
    cleaned = "".join(ch for ch in value.strip() if ch.isprintable())
    cleaned = " ".join(cleaned.split())
    if not cleaned or len(cleaned) > maximum:
        return None
    if cleaned.startswith(".") or ".." in cleaned:
        return None
    return cleaned


def build_local_movie_hints(
    *,
    basename: str,
    technical_metadata: TechnicalMetadata,
    container_title: str | None = None,
) -> LocalMovieHints:
    """Build allowlisted local hints from basename and technical metadata."""
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return LocalMovieHints(
        filename_stem=sanitize_local_hint(stem),
        container_title=sanitize_local_hint(container_title),
        duration_ms=technical_metadata.duration_ms,
        width=technical_metadata.width,
        height=technical_metadata.height,
    )


def extract_movie_identification_frames(
    runner: ProcessRunner,
    *,
    ffmpeg_executable: str,
    media_path: str,
    duration_ms: int | None,
) -> tuple[tuple[RepresentativeFrame, ...], tuple[str, ...]]:
    """Extract up to five timeline-stratified frames for contact-sheet composition."""
    targets = compute_movie_identification_timestamps_ms(duration_ms)
    frames: list[RepresentativeFrame] = []
    warnings: list[str] = []
    for target_ms in targets:
        try:
            result = runner.run(
                executable=ffmpeg_executable,
                argv=build_ffmpeg_frame_argv(media_path=media_path, timestamp_ms=target_ms),
                timeout_seconds=FFMPEG_FRAME_TIMEOUT_SECONDS,
                stdout_max_bytes=PNG_PAYLOAD_MAX_BYTES,
                stderr_max_bytes=SUBPROCESS_STDERR_MAX_BYTES,
            )
        except ProcessExecutionError:
            warnings.append(INDIVIDUAL_FRAME_FAILED_WARNING)
            continue
        if result.returncode != 0:
            warnings.append(INDIVIDUAL_FRAME_FAILED_WARNING)
            if sanitize_retained_stderr(result.stderr):
                pass
            continue
        try:
            frame = build_representative_frame(timestamp_ms=target_ms, payload=result.stdout)
        except FrameNestMediaAnalysisError:
            warnings.append(INDIVIDUAL_FRAME_FAILED_WARNING)
            continue
        if is_near_black_png(frame.payload):
            warnings.append("Rejected near-black representative frame.")
            continue
        frames.append(frame)
    unique = deduplicate_representative_frames(tuple(frames))
    if not unique:
        raise FrameNestMediaAnalysisError(FRAME_EXTRACTION_FAILED_MESSAGE)
    if len(unique) > CONTACT_SHEET_REQUESTED_FRAME_COUNT:
        unique = unique[:CONTACT_SHEET_REQUESTED_FRAME_COUNT]
    return unique, tuple(warnings)


def compose_contact_sheet(frames: tuple[RepresentativeFrame, ...]) -> ContactSheetDerivative:
    """Compose one bounded JPEG contact sheet from validated PNG frames."""
    if not frames or len(frames) > CONTACT_SHEET_REQUESTED_FRAME_COUNT:
        raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)

    cells: list[Image.Image] = []
    sheet: Image.Image | None = None
    try:
        for frame in frames:
            with Image.open(io.BytesIO(frame.payload)) as source:
                if source.format != "PNG":
                    raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)
                source.load()
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > CONTACT_SHEET_MAX_SOURCE_PIXELS:
                    raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE)
                rgb = source.convert("RGB")
                long_edge = max(rgb.size)
                if long_edge > CONTACT_SHEET_CELL_MAX_EDGE:
                    scale = CONTACT_SHEET_CELL_MAX_EDGE / long_edge
                    rgb = rgb.resize(
                        (
                            max(1, round(rgb.size[0] * scale)),
                            max(1, round(rgb.size[1] * scale)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                cells.append(rgb.copy())

        columns = 3 if len(cells) > 3 else max(1, len(cells))
        rows = (len(cells) + columns - 1) // columns
        cell_width = max(image.size[0] for image in cells)
        cell_height = max(image.size[1] for image in cells)
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), color=(0, 0, 0))
        for index, cell in enumerate(cells):
            row = index // columns
            column = index % columns
            offset_x = column * cell_width + (cell_width - cell.size[0]) // 2
            offset_y = row * cell_height + (cell_height - cell.size[1]) // 2
            sheet.paste(cell, (offset_x, offset_y))

        long_edge = max(sheet.size)
        if long_edge > CONTACT_SHEET_MAX_LONG_EDGE:
            scale = CONTACT_SHEET_MAX_LONG_EDGE / long_edge
            sheet = sheet.resize(
                (
                    max(1, round(sheet.size[0] * scale)),
                    max(1, round(sheet.size[1] * scale)),
                ),
                Image.Resampling.LANCZOS,
            )

        output = io.BytesIO()
        sheet.save(
            output,
            format="JPEG",
            quality=CONTACT_SHEET_JPEG_QUALITY,
            subsampling="4:2:0",
            progressive=False,
            optimize=False,
        )
        payload = output.getvalue()
        width, height = sheet.size
    except FrameNestMediaAnalysisError:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise FrameNestMediaAnalysisError(CONTACT_SHEET_FAILED_MESSAGE) from exc
    finally:
        for cell in cells:
            cell.close()
        if sheet is not None:
            sheet.close()

    return ContactSheetDerivative(
        width=width,
        height=height,
        mime_type="image/jpeg",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        source_frame_count=len(frames),
        source_timestamps_ms=tuple(frame.timestamp_ms for frame in frames),
        payload=payload,
    )


def extract_and_compose_contact_sheet(
    runner: ProcessRunner,
    *,
    ffmpeg_executable: str,
    media_path: str,
    duration_ms: int | None,
) -> tuple[ContactSheetDerivative, tuple[str, ...]]:
    """Extract frames and compose one contact sheet with deterministic cleanup."""
    # media_path is local-only and never forwarded to providers.
    work_dir = tempfile.mkdtemp(prefix="framenest-contact-")
    try:
        frames, warnings = extract_movie_identification_frames(
            runner,
            ffmpeg_executable=ffmpeg_executable,
            media_path=media_path,
            duration_ms=duration_ms,
        )
        sheet = compose_contact_sheet(frames)
        return sheet, warnings
    finally:
        # Frame payloads are in-memory; remove any residual temp directory.
        path = Path(work_dir)
        if path.exists():
            for child in path.iterdir():
                child.unlink(missing_ok=True)
            path.rmdir()
