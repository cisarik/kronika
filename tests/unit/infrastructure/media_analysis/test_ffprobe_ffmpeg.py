"""Unit tests for ffprobe and ffmpeg media analysis adapters."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from kronika.application.media_analysis import (
    FFPROBE_STDOUT_MAX_BYTES,
    PNG_SIGNATURE,
    FrameNestMediaAnalysisError,
    build_representative_frame,
    compute_target_timestamps_ms,
)
from kronika.infrastructure.media_analysis.ffmpeg import (
    FRAME_EXTRACTION_FAILED_MESSAGE,
    INDIVIDUAL_FRAME_FAILED_WARNING,
    build_ffmpeg_frame_argv,
    extract_representative_frames,
    format_timestamp_ms,
)
from kronika.infrastructure.media_analysis.ffprobe import parse_ffprobe_payload, probe_media_metadata
from kronika.infrastructure.media_analysis.process import ProcessRunResult

PRIVATE_ROOT = "/Users/agile/Video"


class _SequenceRunner:
    def __init__(self, results: list[ProcessRunResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(
        self,
        *,
        executable: str,
        argv: Sequence[str],
        timeout_seconds: float,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
    ) -> ProcessRunResult:
        self.calls.append((executable, tuple(argv)))
        return self._results.pop(0)


def _ffprobe_payload(
    *,
    duration: str | None = "10.0",
    stream_duration: str | None = None,
    width: int = 640,
    height: int = 360,
    codec: str = "h264",
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
    attached_pic: bool = False,
    include_video: bool = True,
    include_audio: bool = True,
) -> dict[str, object]:
    streams: list[dict[str, object]] = []
    if include_video:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": width,
                "height": height,
                "duration": stream_duration,
                "disposition": {"attached_pic": 1 if attached_pic else 0},
            }
        )
    if include_audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return {
        "streams": streams,
        "format": {"duration": duration, "format_name": format_name},
    }


def test_format_timestamp_ms_is_deterministic() -> None:
    assert format_timestamp_ms(1_234) == "00:00:01.234"


def test_build_ffmpeg_frame_argv_contract() -> None:
    argv = build_ffmpeg_frame_argv(media_path="/tmp/sample.mp4", timestamp_ms=500)
    assert argv[:4] == ["-nostdin", "-hide_banner", "-loglevel", "error"]
    assert argv[4:8] == ["-ss", "00:00:00.500", "-i", "/tmp/sample.mp4"]
    assert "-map" in argv and "0:v:0" in argv
    assert "pipe:1" in argv
    assert argv.index("-ss") < argv.index("-i")


def test_parse_ffprobe_prefers_stream_duration() -> None:
    payload = _ffprobe_payload(duration="9.0", stream_duration="8.5")
    metadata = parse_ffprobe_payload(payload)
    assert metadata.duration_ms == 8500


def test_parse_ffprobe_falls_back_to_format_duration() -> None:
    payload = _ffprobe_payload(duration="4.2", stream_duration=None)
    metadata = parse_ffprobe_payload(payload)
    assert metadata.duration_ms == 4200


def test_parse_ffprobe_skips_attached_picture_stream() -> None:
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "png",
                "width": 100,
                "height": 100,
                "disposition": {"attached_pic": 1},
            },
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 640,
                "height": 360,
                "disposition": {"attached_pic": 0},
            },
        ],
        "format": {"duration": "1.0", "format_name": "mp4"},
    }
    metadata = parse_ffprobe_payload(payload)
    assert metadata.video_codec == "h264"


def test_parse_ffprobe_rejects_malformed_json_shape() -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        parse_ffprobe_payload({"streams": "bad"})


def test_parse_ffprobe_rejects_missing_visual_stream() -> None:
    payload = _ffprobe_payload(include_video=False)
    with pytest.raises(FrameNestMediaAnalysisError):
        parse_ffprobe_payload(payload)


def test_probe_media_metadata_uses_expected_argv() -> None:
    runner = _SequenceRunner(
        [
            ProcessRunResult(
                returncode=0,
                stdout=json.dumps(_ffprobe_payload()).encode("utf-8"),
                stderr=b"",
            )
        ]
    )
    metadata = probe_media_metadata(
        runner,
        ffprobe_executable="/usr/bin/ffprobe",
        media_path="/tmp/sample.mp4",
    )
    assert metadata.width == 640
    executable, argv = runner.calls[0]
    assert executable == "/usr/bin/ffprobe"
    assert argv[:6] == ("-v", "error", "-print_format", "json", "-show_format", "-show_streams")


def test_probe_media_metadata_rejects_oversized_output() -> None:
    from kronika.infrastructure.media_analysis.process import (
        PROCESS_OUTPUT_LIMIT_MESSAGE,
        ProcessExecutionError,
    )

    class _OversizeRunner:
        def run(self, **kwargs: object) -> ProcessRunResult:
            raise ProcessExecutionError(PROCESS_OUTPUT_LIMIT_MESSAGE)

    with pytest.raises(FrameNestMediaAnalysisError):
        probe_media_metadata(
            _OversizeRunner(),
            ffprobe_executable="/usr/bin/ffprobe",
            media_path="/tmp/sample.mp4",
        )


def test_extract_frames_orders_targets_and_dedupes() -> None:
    png_a = PNG_SIGNATURE + b"a"
    png_b = PNG_SIGNATURE + b"b"
    runner = _SequenceRunner(
        [
            ProcessRunResult(returncode=0, stdout=png_a, stderr=b""),
            ProcessRunResult(returncode=0, stdout=png_a, stderr=b""),
            ProcessRunResult(returncode=0, stdout=png_b, stderr=b""),
        ]
    )
    frames, warnings = extract_representative_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/sample.mp4",
        duration_ms=10_000,
    )
    assert [frame.timestamp_ms for frame in frames] == [1_000, 9_000]
    assert warnings == ()


def test_extract_frames_records_warning_for_individual_failure() -> None:
    png = PNG_SIGNATURE + b"ok"
    runner = _SequenceRunner(
        [
            ProcessRunResult(returncode=1, stdout=b"", stderr=b"failed"),
            ProcessRunResult(returncode=0, stdout=png, stderr=b""),
            ProcessRunResult(returncode=0, stdout=png, stderr=b""),
        ]
    )
    frames, warnings = extract_representative_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/sample.mp4",
        duration_ms=10_000,
    )
    assert len(frames) == 1
    assert warnings == (INDIVIDUAL_FRAME_FAILED_WARNING,)


def test_extract_frames_fails_when_all_targets_and_zero_fallback_fail() -> None:
    runner = _SequenceRunner(
        [
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
        ]
    )
    with pytest.raises(FrameNestMediaAnalysisError, match=FRAME_EXTRACTION_FAILED_MESSAGE):
        extract_representative_frames(
            runner,
            ffmpeg_executable="/usr/bin/ffmpeg",
            media_path="/tmp/sample.mp4",
            duration_ms=10_000,
        )
    assert len(runner.calls) == 4
    assert runner.calls[-1][1][runner.calls[-1][1].index("-ss") + 1] == "00:00:00.000"


def test_extract_frames_falls_back_to_timestamp_zero_for_empty_midpoint_seeks() -> None:
    png = PNG_SIGNATURE + b"first-frame"
    runner = _SequenceRunner(
        [
            ProcessRunResult(returncode=0, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=0, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=0, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=0, stdout=png, stderr=b""),
        ]
    )
    frames, warnings = extract_representative_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/one-frame.gif",
        duration_ms=100,
    )
    assert [frame.timestamp_ms for frame in frames] == [0]
    assert warnings == (
        INDIVIDUAL_FRAME_FAILED_WARNING,
        INDIVIDUAL_FRAME_FAILED_WARNING,
        INDIVIDUAL_FRAME_FAILED_WARNING,
    )
    assert len(runner.calls) == 4
    assert runner.calls[-1][1][runner.calls[-1][1].index("-ss") + 1] == "00:00:00.000"


def test_compute_target_timestamps_matches_policy() -> None:
    assert compute_target_timestamps_ms(1000) == (100, 500, 900)


def test_build_representative_frame_enforces_digest() -> None:
    payload = PNG_SIGNATURE + b"x"
    frame = build_representative_frame(timestamp_ms=0, payload=payload)
    assert len(frame.sha256) == 64


def test_errors_do_not_leak_private_root() -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        parse_ffprobe_payload({"streams": []})
    assert PRIVATE_ROOT not in FRAME_EXTRACTION_FAILED_MESSAGE
