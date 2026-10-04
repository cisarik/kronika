"""Focused tests for Variant B movie-identification contact-sheet selection."""

from __future__ import annotations

import io
import tempfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    build_representative_frame,
    compute_target_timestamps_ms,
)
from kronika.application.movie_identification import (
    LocalMovieHints,
    MovieIdentificationRequest,
    parse_movie_identification_payload,
)
from kronika.domain.media_classification import (
    CONTACT_SHEET_DERIVATIVE_STRATEGY,
    CONTACT_SHEET_REQUESTED_FRAME_COUNT,
    MOVIE_IDENTIFICATION_MAX_TOKENS,
    MOVIE_IDENTIFICATION_PROMPT_VERSION,
    MOVIE_IDENTIFICATION_REASONING_BUDGET,
    MOVIE_IDENTIFICATION_TEMPERATURE,
    MOVIE_IDENTIFICATION_TOP_P,
)
from kronika.infrastructure.ai.nvidia_nim import build_nvidia_movie_identification_body
from kronika.infrastructure.media_analysis.contact_sheet import (
    CONTACT_SHEET_CELL_MAX_EDGE,
    CONTACT_SHEET_JPEG_QUALITY,
    CONTACT_SHEET_MAX_LONG_EDGE,
    compose_contact_sheet,
    compute_movie_identification_timestamps_ms,
    extract_and_compose_contact_sheet,
    extract_movie_identification_frames,
)
from kronika.infrastructure.media_analysis.ffmpeg import (
    FRAME_EXTRACTION_FAILED_MESSAGE,
    INDIVIDUAL_FRAME_FAILED_WARNING,
)
from kronika.infrastructure.media_analysis.process import (
    ProcessExecutionError,
    ProcessRunResult,
)

VARIANT_B_FRACTIONS = (0.02, 0.22, 0.45, 0.70, 0.96)
TARGET_DURATION_MS = 6_847_960
TARGET_TIMESTAMPS_MS = (
    136_959,
    1_506_551,
    3_081_582,
    4_793_572,
    6_574_041,
)
DURATION_114_MIN_MS = 114 * 60 * 1000
TIMESTAMPS_114_MIN_MS = (
    136_800,
    1_504_800,
    3_078_000,
    4_788_000,
    6_566_400,
)


def _png(*, color: tuple[int, int, int], size: tuple[int, int] = (64, 36)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _widescreen_png(*, color: tuple[int, int, int]) -> bytes:
    return _png(color=color, size=(1280, 720))


class _ScriptedRunner:
    """Deterministic ProcessRunner returning scripted frame payloads."""

    def __init__(self, results: Sequence[ProcessRunResult | BaseException]) -> None:
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
        pass_fds: Sequence[int] = (),
    ) -> ProcessRunResult:
        del timeout_seconds, stdout_max_bytes, stderr_max_bytes, pass_fds
        self.calls.append((executable, tuple(argv)))
        next_result = self._results.pop(0)
        if isinstance(next_result, BaseException):
            raise next_result
        return next_result


# --- Timestamp selection -----------------------------------------------------


def test_variant_b_exact_tuple_for_production_duration() -> None:
    assert compute_movie_identification_timestamps_ms(TARGET_DURATION_MS) == TARGET_TIMESTAMPS_MS


def test_variant_b_exact_tuple_for_114_minute_source() -> None:
    assert DURATION_114_MIN_MS == 6_840_000
    assert compute_movie_identification_timestamps_ms(DURATION_114_MIN_MS) == TIMESTAMPS_114_MIN_MS


def test_ordinary_long_form_requests_five_ascending_unique_timestamps() -> None:
    targets = compute_movie_identification_timestamps_ms(3_600_000)
    assert len(targets) == 5
    assert targets == tuple(sorted(targets))
    assert len(set(targets)) == 5
    assert targets == tuple(
        min(3_600_000 - 1, max(0, int(3_600_000 * fraction))) for fraction in VARIANT_B_FRACTIONS
    )


def test_integer_truncation_not_nearest_rounding() -> None:
    # 1001 * 0.02 = 20.02 -> truncate to 20, not round to 20.
    # 1001 * 0.96 = 960.96 -> truncate to 960, not round to 961.
    assert compute_movie_identification_timestamps_ms(1001) == (
        20,
        220,
        450,
        700,
        960,
    )


def test_normalized_endpoints_are_not_requested() -> None:
    targets = compute_movie_identification_timestamps_ms(10_000)
    assert 0 not in VARIANT_B_FRACTIONS
    assert 1.0 not in VARIANT_B_FRACTIONS
    assert targets[0] > 0
    assert targets[-1] < 10_000 - 1 or targets[-1] == min(9999, int(10_000 * 0.96))
    assert targets[-1] == 9_600


def test_short_duration_collapses_duplicate_requested_timestamps() -> None:
    targets = compute_movie_identification_timestamps_ms(3)
    assert targets == (0, 1, 2)
    assert targets == tuple(dict.fromkeys(targets))


def test_duration_one_collapses_to_zero() -> None:
    assert compute_movie_identification_timestamps_ms(1) == (0,)


@pytest.mark.parametrize("duration_ms", [None, 0, -1, -100])
def test_unavailable_or_non_positive_duration_preserves_zero(duration_ms: int | None) -> None:
    assert compute_movie_identification_timestamps_ms(duration_ms) == (0,)


def test_medium_and_long_durations_have_no_duplicate_requested_timestamps() -> None:
    for duration_ms in (60_000, 600_000, 3_600_000, TARGET_DURATION_MS, DURATION_114_MIN_MS):
        targets = compute_movie_identification_timestamps_ms(duration_ms)
        assert len(targets) == 5
        assert len(set(targets)) == 5


def test_requested_frame_ceiling_is_five() -> None:
    assert CONTACT_SHEET_REQUESTED_FRAME_COUNT == 5
    assert len(VARIANT_B_FRACTIONS) == 5


# --- Filtering and extraction ------------------------------------------------


def test_one_primary_timestamp_extraction_failure_skips_and_continues() -> None:
    colorful = _png(color=(40, 160, 90))
    runner = _ScriptedRunner(
        [
            ProcessExecutionError("boom"),
            ProcessRunResult(returncode=0, stdout=colorful, stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(50, 170, 100)), stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(60, 180, 110)), stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(70, 190, 120)), stderr=b""),
        ]
    )
    frames, warnings = extract_movie_identification_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    assert len(frames) == 4
    assert warnings == (INDIVIDUAL_FRAME_FAILED_WARNING,)
    assert [frame.timestamp_ms for frame in frames] == list(
        compute_movie_identification_timestamps_ms(100_000)[1:]
    )


def test_several_extraction_failures_leave_one_usable_frame() -> None:
    usable = _png(color=(200, 40, 80))
    runner = _ScriptedRunner(
        [
            ProcessRunResult(returncode=1, stdout=b"", stderr=b"fail"),
            ProcessExecutionError("boom"),
            ProcessRunResult(returncode=0, stdout=usable, stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b"fail"),
            ProcessExecutionError("boom"),
        ]
    )
    frames, warnings = extract_movie_identification_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    assert len(frames) == 1
    assert frames[0].timestamp_ms == compute_movie_identification_timestamps_ms(100_000)[2]
    assert warnings.count(INDIVIDUAL_FRAME_FAILED_WARNING) == 4


def test_near_black_selected_frame_is_removed() -> None:
    black = _png(color=(0, 0, 0))
    colorful = _png(color=(180, 40, 90))
    runner = _ScriptedRunner(
        [
            ProcessRunResult(returncode=0, stdout=black, stderr=b""),
            ProcessRunResult(returncode=0, stdout=colorful, stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(10, 200, 40)), stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(20, 210, 50)), stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(30, 220, 60)), stderr=b""),
        ]
    )
    frames, warnings = extract_movie_identification_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    assert len(frames) == 4
    assert "Rejected near-black representative frame." in warnings
    assert frames[0].timestamp_ms == compute_movie_identification_timestamps_ms(100_000)[1]


def test_opening_and_ending_near_black_are_skipped() -> None:
    black = _png(color=(0, 0, 0))
    mid_colors = [(90, 40, 200), (100, 50, 210), (110, 60, 220)]
    runner = _ScriptedRunner(
        [
            ProcessRunResult(returncode=0, stdout=black, stderr=b""),
            *[
                ProcessRunResult(returncode=0, stdout=_png(color=color), stderr=b"")
                for color in mid_colors
            ],
            ProcessRunResult(returncode=0, stdout=black, stderr=b""),
        ]
    )
    frames, warnings = extract_movie_identification_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    targets = compute_movie_identification_timestamps_ms(100_000)
    assert [frame.timestamp_ms for frame in frames] == list(targets[1:4])
    assert warnings.count("Rejected near-black representative frame.") == 2


def test_exact_duplicate_digest_keeps_first_occurrence_order() -> None:
    shared = _png(color=(40, 90, 160))
    runner = _ScriptedRunner(
        [
            ProcessRunResult(returncode=0, stdout=shared, stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(50, 100, 170)), stderr=b""),
            ProcessRunResult(returncode=0, stdout=shared, stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(70, 120, 190)), stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(80, 130, 200)), stderr=b""),
        ]
    )
    frames, warnings = extract_movie_identification_frames(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    targets = compute_movie_identification_timestamps_ms(100_000)
    assert [frame.timestamp_ms for frame in frames] == [
        targets[0],
        targets[1],
        targets[3],
        targets[4],
    ]
    assert warnings == ()
    assert len({frame.sha256 for frame in frames}) == 4


def test_one_usable_frame_still_composes() -> None:
    usable = _png(color=(200, 80, 40))
    runner = _ScriptedRunner(
        [
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=0, stdout=usable, stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
        ]
    )
    sheet, warnings = extract_and_compose_contact_sheet(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    assert sheet.source_frame_count == 1
    assert sheet.mime_type == "image/jpeg"
    assert warnings.count(INDIVIDUAL_FRAME_FAILED_WARNING) == 4


def test_zero_usable_frames_raises_preparation_failure_without_provider() -> None:
    runner = _ScriptedRunner(
        [
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(0, 0, 0)), stderr=b""),
            ProcessExecutionError("boom"),
            ProcessRunResult(returncode=1, stdout=b"", stderr=b""),
            ProcessRunResult(returncode=0, stdout=_png(color=(0, 0, 0)), stderr=b""),
        ]
    )
    with pytest.raises(FrameNestMediaAnalysisError, match=FRAME_EXTRACTION_FAILED_MESSAGE):
        extract_and_compose_contact_sheet(
            runner,
            ffmpeg_executable="/usr/bin/ffmpeg",
            media_path="/tmp/synthetic.mp4",
            duration_ms=100_000,
        )
    # Failure occurs during local frame preparation; no contact sheet exists to submit.


# --- Representation ----------------------------------------------------------


def test_five_widescreen_cells_use_three_by_two_layout_and_bounds() -> None:
    frames = tuple(
        build_representative_frame(
            timestamp_ms=index * 1_000,
            payload=_widescreen_png(color=(20 + index * 30, 80, 160)),
        )
        for index in range(5)
    )
    sheet = compose_contact_sheet(frames)
    assert sheet.source_frame_count == 5
    assert sheet.mime_type == "image/jpeg"
    assert max(sheet.width, sheet.height) <= CONTACT_SHEET_MAX_LONG_EDGE == 1280
    # 3 columns × 2 rows for five cells; each cell long edge capped at 420.
    assert sheet.width <= 3 * CONTACT_SHEET_CELL_MAX_EDGE
    assert sheet.height <= 2 * CONTACT_SHEET_CELL_MAX_EDGE
    assert sheet.width == 3 * CONTACT_SHEET_CELL_MAX_EDGE
    assert sheet.height == 2 * 236  # 1280x720 cells scaled to long-edge 420
    assert CONTACT_SHEET_CELL_MAX_EDGE == 420
    assert CONTACT_SHEET_JPEG_QUALITY == 82
    assert CONTACT_SHEET_DERIVATIVE_STRATEGY == "bounded_contact_sheet_jpeg_v1"

    with Image.open(io.BytesIO(sheet.payload)) as image:
        assert image.format == "JPEG"
        assert image.size == (sheet.width, sheet.height)


def test_contact_sheet_is_single_image_derivative() -> None:
    frames = tuple(
        build_representative_frame(
            timestamp_ms=index * 500,
            payload=_png(color=(30 + index * 25, 90, 140)),
        )
        for index in range(5)
    )
    sheet = compose_contact_sheet(frames)
    request = MovieIdentificationRequest(
        basename="film.mp4",
        contact_sheet=sheet,
        hints=LocalMovieHints(
            filename_stem="film",
            container_title=None,
            duration_ms=TARGET_DURATION_MS,
            width=1280,
            height=720,
        ),
    )
    body = build_nvidia_movie_identification_body(request, model_id="test-model")
    images = [
        part for part in body["messages"][0]["content"] if part.get("type") == "image_url"
    ]
    assert len(images) == 1


def test_temporary_artifacts_are_cleaned_up() -> None:
    usable = [
        _png(color=(40 + index * 20, 100, 150)) for index in range(5)
    ]
    runner = _ScriptedRunner(
        [ProcessRunResult(returncode=0, stdout=payload, stderr=b"") for payload in usable]
    )
    before = set(Path(tempfile.gettempdir()).glob("framenest-contact-*"))
    sheet, _warnings = extract_and_compose_contact_sheet(
        runner,
        ffmpeg_executable="/usr/bin/ffmpeg",
        media_path="/tmp/synthetic.mp4",
        duration_ms=100_000,
    )
    after = set(Path(tempfile.gettempdir()).glob("framenest-contact-*"))
    assert sheet.source_frame_count == 5
    assert after == before


# --- Integration / non-drift -------------------------------------------------


def test_generic_media_analysis_sampling_unchanged() -> None:
    assert compute_target_timestamps_ms(1000) == (100, 500, 900)
    assert compute_target_timestamps_ms(10_000) == (1_000, 5_000, 9_000)


def test_nvidia_movie_request_reasoning_and_prompt_unchanged() -> None:
    frames = tuple(
        build_representative_frame(
            timestamp_ms=index * 100,
            payload=_png(color=(40 + index * 20, 120, 80)),
        )
        for index in range(3)
    )
    sheet = compose_contact_sheet(frames)
    request = MovieIdentificationRequest(
        basename="film.mp4",
        contact_sheet=sheet,
        hints=LocalMovieHints(
            filename_stem="film",
            container_title=None,
            duration_ms=1000,
            width=64,
            height=36,
        ),
    )
    body = build_nvidia_movie_identification_body(request, model_id="test-model")
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["reasoning_budget"] == MOVIE_IDENTIFICATION_REASONING_BUDGET == 2048
    assert body["max_tokens"] == MOVIE_IDENTIFICATION_MAX_TOKENS == 4096
    assert body["temperature"] == MOVIE_IDENTIFICATION_TEMPERATURE == 0.6
    assert body["top_p"] == MOVIE_IDENTIFICATION_TOP_P == 0.95
    assert "top_k" not in body
    assert MOVIE_IDENTIFICATION_PROMPT_VERSION == "framenest-movie-identification-prompt-v2"
    images = [
        part for part in body["messages"][0]["content"] if part.get("type") == "image_url"
    ]
    assert len(images) == 1


def test_structured_identified_and_unknown_parsing_unchanged() -> None:
    identified = parse_movie_identification_payload(
        {
            "identified_title": "Synthetic Film",
            "release_year": 1999,
            "identification_status": "identified",
            "confidence": "high",
            "candidate_titles": [],
            "genres": ["Documentary"],
            "description": "A synthetic identification.",
            "tags": ["Test"],
            "evidence_summary": "Visible title card.",
        },
        provider_id="nvidia-nim",
        model_id="test-model",
        derivative_count=1,
    )
    assert identified.identified_title == "Synthetic Film"

    with pytest.raises(Exception):
        parse_movie_identification_payload(
            {
                "identified_title": "Guess",
                "release_year": None,
                "identification_status": "unknown",
                "confidence": "unknown",
                "candidate_titles": [],
                "genres": [],
                "description": "Unknown.",
                "tags": [],
                "evidence_summary": "Insufficient evidence.",
            },
            provider_id="nvidia-nim",
            model_id="test-model",
            derivative_count=1,
        )


def test_movie_selector_is_distinct_from_generic_sampling() -> None:
    movie = compute_movie_identification_timestamps_ms(10_000)
    generic = compute_target_timestamps_ms(10_000)
    assert movie == (200, 2_200, 4_500, 7_000, 9_600)
    assert generic == (1_000, 5_000, 9_000)
    assert movie != generic
