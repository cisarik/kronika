"""ffprobe JSON parsing for local media analysis preparation."""

from __future__ import annotations

import json
from typing import Any

from kronika.application.media_analysis import (
    FFPROBE_STDOUT_MAX_BYTES,
    FFPROBE_TIMEOUT_SECONDS,
    INVALID_TECHNICAL_METADATA_MESSAGE,
    SUBPROCESS_STDERR_MAX_BYTES,
    FrameNestMediaAnalysisError,
    TechnicalMetadata,
    parse_duration_seconds_to_ms,
)
from kronika.infrastructure.media_analysis.process import (
    PROCESS_FAILED_MESSAGE,
    ProcessExecutionError,
    ProcessRunner,
)


def _is_attached_picture(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return False
    attached = disposition.get("attached_pic")
    return attached in (1, True, "1")


def _select_primary_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        if _is_attached_picture(stream):
            continue
        return stream
    return None


def _parse_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str):
            try:
                parsed = int(value)
            except ValueError:
                return None
            value = parsed
        else:
            return None
    if value <= 0:
        return None
    return value


def _parse_duration_field(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 0:
            return None
        return int(value * 1000)
    if isinstance(value, str):
        return parse_duration_seconds_to_ms(value)
    return None


def _normalize_container_formats(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)
    parts = [part.strip().lower() for part in value.split(",")]
    normalized = tuple(part for part in parts if part)
    if not normalized:
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)
    return normalized


def parse_ffprobe_payload(payload: dict[str, Any]) -> TechnicalMetadata:
    """Parse bounded ffprobe JSON into technical metadata."""
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)
    video_stream = _select_primary_video_stream(streams)
    if video_stream is None:
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)

    width = _parse_positive_int(video_stream.get("width"))
    height = _parse_positive_int(video_stream.get("height"))
    codec = video_stream.get("codec_name")
    if width is None or height is None or not isinstance(codec, str) or not codec:
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)

    duration_ms = _parse_duration_field(video_stream.get("duration"))
    format_block = payload.get("format")
    if duration_ms is None and isinstance(format_block, dict):
        duration_ms = _parse_duration_field(format_block.get("duration"))

    container_formats: tuple[str, ...] = ()
    if isinstance(format_block, dict):
        container_formats = _normalize_container_formats(format_block.get("format_name"))

    has_audio = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
    )

    return TechnicalMetadata(
        duration_ms=duration_ms,
        width=width,
        height=height,
        video_codec=codec.lower(),
        container_formats=container_formats,
        has_audio=has_audio,
    )


def probe_media_metadata(
    runner: ProcessRunner,
    *,
    ffprobe_executable: str,
    media_path: str,
) -> TechnicalMetadata:
    """Run ffprobe and parse bounded technical metadata for one local file."""
    try:
        result = runner.run(
            executable=ffprobe_executable,
            argv=[
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-i",
                media_path,
            ],
            timeout_seconds=FFPROBE_TIMEOUT_SECONDS,
            stdout_max_bytes=FFPROBE_STDOUT_MAX_BYTES,
            stderr_max_bytes=SUBPROCESS_STDERR_MAX_BYTES,
        )
    except ProcessExecutionError as exc:
        raise FrameNestMediaAnalysisError(str(exc)) from None
    if result.returncode != 0:
        raise FrameNestMediaAnalysisError(PROCESS_FAILED_MESSAGE)
    try:
        text = result.stdout.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeError, json.JSONDecodeError):
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE) from None
    if not isinstance(payload, dict):
        raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)
    return parse_ffprobe_payload(payload)
