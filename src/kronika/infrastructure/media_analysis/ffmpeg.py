"""ffmpeg representative-frame extraction for local media analysis preparation."""

from __future__ import annotations

from kronika.application.media_analysis import (
    AGGREGATE_PNG_PAYLOAD_MAX_BYTES,
    FFMPEG_FRAME_TIMEOUT_SECONDS,
    INVALID_REPRESENTATIVE_FRAME_MESSAGE,
    MAX_OUTPUT_DIMENSION,
    PNG_PAYLOAD_MAX_BYTES,
    REQUESTED_FRAME_COUNT,
    SUBPROCESS_STDERR_MAX_BYTES,
    FrameNestMediaAnalysisError,
    RepresentativeFrame,
    build_representative_frame,
    compute_target_timestamps_ms,
    deduplicate_representative_frames,
)
from kronika.infrastructure.media_analysis.process import (
    PROCESS_FAILED_MESSAGE,
    ProcessExecutionError,
    ProcessRunner,
)
from kronika.infrastructure.media_analysis.tools import sanitize_retained_stderr

FRAME_EXTRACTION_FAILED_MESSAGE = "Representative frame extraction failed."
INDIVIDUAL_FRAME_FAILED_WARNING = "Representative frame extraction failed for one target."

_SCALE_FILTER = (
    f"scale='min({MAX_OUTPUT_DIMENSION},iw)':"
    f"'min({MAX_OUTPUT_DIMENSION},ih)':"
    "force_original_aspect_ratio=decrease,format=rgb24"
)


def format_timestamp_ms(timestamp_ms: int) -> str:
    """Return a deterministic ffmpeg timestamp for integer milliseconds."""
    hours = timestamp_ms // 3_600_000
    remainder = timestamp_ms % 3_600_000
    minutes = remainder // 60_000
    remainder = remainder % 60_000
    seconds = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def build_ffmpeg_frame_argv(*, media_path: str, timestamp_ms: int) -> list[str]:
    """Return the deterministic ffmpeg argv for one representative frame.

    Seek with ``-ss`` before ``-i`` so long cataloged videos remain within the
    bounded per-frame timeout; accurate decode-from-start seeking is too slow
    for multi-hour sources under the accepted timeout budget.
    """
    return [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        format_timestamp_ms(timestamp_ms),
        "-i",
        media_path,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-vf",
        _SCALE_FILTER,
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]


def _extract_one_frame(
    runner: ProcessRunner,
    *,
    ffmpeg_executable: str,
    media_path: str,
    timestamp_ms: int,
) -> RepresentativeFrame | None:
    try:
        result = runner.run(
            executable=ffmpeg_executable,
            argv=build_ffmpeg_frame_argv(media_path=media_path, timestamp_ms=timestamp_ms),
            timeout_seconds=FFMPEG_FRAME_TIMEOUT_SECONDS,
            stdout_max_bytes=PNG_PAYLOAD_MAX_BYTES,
            stderr_max_bytes=SUBPROCESS_STDERR_MAX_BYTES,
        )
    except ProcessExecutionError:
        return None
    if result.returncode != 0:
        # Retain sanitized stderr only for process accounting; warning text stays fixed.
        sanitize_retained_stderr(result.stderr)
        return None
    try:
        return build_representative_frame(timestamp_ms=timestamp_ms, payload=result.stdout)
    except FrameNestMediaAnalysisError:
        return None


def extract_representative_frames(
    runner: ProcessRunner,
    *,
    ffmpeg_executable: str,
    media_path: str,
    duration_ms: int | None,
) -> tuple[tuple[RepresentativeFrame, ...], tuple[str, ...]]:
    """Extract bounded representative PNG frames for one local media file."""
    targets = compute_target_timestamps_ms(duration_ms)
    frames: list[RepresentativeFrame] = []
    warnings: list[str] = []
    aggregate_size = 0

    for target_ms in targets:
        frame = _extract_one_frame(
            runner,
            ffmpeg_executable=ffmpeg_executable,
            media_path=media_path,
            timestamp_ms=target_ms,
        )
        if frame is None:
            warnings.append(INDIVIDUAL_FRAME_FAILED_WARNING)
            continue
        if aggregate_size + frame.byte_size > AGGREGATE_PNG_PAYLOAD_MAX_BYTES:
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        aggregate_size += frame.byte_size
        frames.append(frame)

    unique_frames = deduplicate_representative_frames(tuple(frames))
    # Ultra-short / one-frame GIF and video sources can report a positive duration
    # while mid-point seeks return empty payloads. Fall back to timestamp 0 once.
    if not unique_frames and 0 not in targets:
        frame = _extract_one_frame(
            runner,
            ffmpeg_executable=ffmpeg_executable,
            media_path=media_path,
            timestamp_ms=0,
        )
        if frame is None:
            warnings.append(INDIVIDUAL_FRAME_FAILED_WARNING)
        elif frame.byte_size > AGGREGATE_PNG_PAYLOAD_MAX_BYTES:
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        else:
            unique_frames = (frame,)
    if not unique_frames:
        raise FrameNestMediaAnalysisError(FRAME_EXTRACTION_FAILED_MESSAGE)
    if len(unique_frames) > REQUESTED_FRAME_COUNT:
        raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
    return unique_frames, tuple(warnings)
