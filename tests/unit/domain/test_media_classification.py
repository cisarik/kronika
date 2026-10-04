"""Unit tests for content classification and movie-identification contracts."""

from __future__ import annotations

import io

from PIL import Image
import pytest

from kronika.application.movie_identification import (
    FrameNestMovieIdentificationError,
    parse_movie_identification_payload,
)
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    IdentificationConfidence,
    MovieGenre,
    MovieIdentificationStatus,
)
from kronika.domain.media_metadata import MediaMetadata, normalize_genres_for_category
from kronika.domain.identities import MediaId
from kronika.infrastructure.ai.nvidia_nim import (
    build_nvidia_movie_identification_body,
    build_nvidia_request_body,
)
from kronika.infrastructure.media_analysis.contact_sheet import (
    compose_contact_sheet,
    compute_movie_identification_timestamps_ms,
    is_near_black_png,
    sanitize_local_hint,
)
from kronika.application.media_analysis import build_representative_frame
from kronika.application.movie_identification import MovieIdentificationRequest, LocalMovieHints
from kronika.application.media_suggestion import (
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import TechnicalMetadata


def _solid_png(*, color: tuple[int, int, int], size: tuple[int, int] = (64, 48)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_content_category_and_source_are_orthogonal() -> None:
    assert ContentCategory.MEME.value == "meme"
    assert ContentCategory.MOVIE.value == "movie"
    assert ContentCategory.GENERAL.value == "general"
    assert AcquisitionSource.YOUTUBE_MANUAL_CLAIM.value == "youtube_manual_claim"
    assert AcquisitionSource.UNKNOWN.value == "unknown"


def test_genres_cleared_when_not_movie() -> None:
    genres = (MovieGenre.DRAMA, MovieGenre.COMEDY)
    assert normalize_genres_for_category(ContentCategory.MOVIE, genres) == genres
    assert normalize_genres_for_category(ContentCategory.MEME, genres) == ()


def test_media_metadata_rejects_genres_without_movie_category() -> None:
    with pytest.raises(Exception):
        MediaMetadata(
            media_id=MediaId.new(),
            display_title=None,
            description=None,
            tag_keys=(),
            created_at_ms=1,
            updated_at_ms=1,
            content_category=ContentCategory.GENERAL,
            genre_keys=(MovieGenre.DRAMA,),
        )


def test_movie_timestamps_include_early_and_late() -> None:
    targets = compute_movie_identification_timestamps_ms(100_000)
    assert targets[0] < targets[-1]
    assert targets[0] < 10_000
    assert targets[-1] > 80_000
    assert len(targets) == 5


def test_near_black_and_contact_sheet_bounds() -> None:
    black = _solid_png(color=(0, 0, 0))
    colorful = _solid_png(color=(200, 40, 80))
    assert is_near_black_png(black) is True
    assert is_near_black_png(colorful) is False
    frames = tuple(
        build_representative_frame(timestamp_ms=index * 1000, payload=_solid_png(color=(20 + index * 30, 80, 160)))
        for index in range(4)
    )
    sheet = compose_contact_sheet(frames)
    assert sheet.mime_type == "image/jpeg"
    assert sheet.byte_size <= 1_572_864
    assert sheet.source_frame_count == 4
    assert max(sheet.width, sheet.height) <= 1280


def test_sanitize_local_hint_rejects_paths() -> None:
    assert sanitize_local_hint("Blade Runner") == "Blade Runner"
    assert sanitize_local_hint("/srv/media/secret.mp4") is None
    assert sanitize_local_hint("..\\evil") is None


def test_movie_identification_payload_validation() -> None:
    suggestion = parse_movie_identification_payload(
        {
            "identified_title": "Synthetic Film",
            "release_year": 1999,
            "identification_status": "identified",
            "confidence": "high",
            "candidate_titles": [],
            "genres": ["Sci-Fi", "Thriller"],
            "description": "A synthetic identification.",
            "tags": ["Test"],
            "evidence_summary": "Visible title card.",
        },
        provider_id="nvidia-nim",
        model_id="test-model",
        derivative_count=1,
    )
    assert suggestion.identification_status is MovieIdentificationStatus.IDENTIFIED
    assert suggestion.confidence is IdentificationConfidence.HIGH

    with pytest.raises(FrameNestMovieIdentificationError):
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

    invalid_payloads = [
        {
            "identified_title": None,
            "release_year": "1999",
            "identification_status": "unknown",
            "confidence": "unknown",
            "candidate_titles": [],
            "genres": [],
            "description": "Unknown.",
            "tags": [],
            "evidence_summary": "Insufficient evidence.",
        },
        {
            "identified_title": None,
            "release_year": None,
            "identification_status": "unknown",
            "confidence": "unknown",
            "candidate_titles": [123],
            "genres": [],
            "description": "Unknown.",
            "tags": [],
            "evidence_summary": "Insufficient evidence.",
        },
        {
            "identified_title": None,
            "release_year": None,
            "identification_status": "unknown",
            "confidence": "unknown",
            "candidate_titles": [],
            "genres": [],
            "description": "Unknown.",
            "tags": [],
        },
    ]
    for payload in invalid_payloads:
        with pytest.raises(FrameNestMovieIdentificationError):
            parse_movie_identification_payload(
                payload,
                provider_id="nvidia-nim",
                model_id="test-model",
                derivative_count=1,
            )

    with pytest.raises(FrameNestMovieIdentificationError):
        parse_movie_identification_payload(
            {
                "identified_title": None,
                "release_year": None,
                "identification_status": "unknown",
                "confidence": "maybe",
                "candidate_titles": [],
                "genres": ["NotAGenre"],
                "description": "Bad.",
                "tags": [],
                "evidence_summary": "Bad.",
            },
            provider_id="nvidia-nim",
            model_id="test-model",
            derivative_count=1,
        )


def test_nvidia_generic_reasoning_off_movie_reasoning_on() -> None:
    frames = tuple(
        build_representative_frame(timestamp_ms=0, payload=_solid_png(color=(90, 120, 40)))
        for _ in range(1)
    )
    request = MediaSuggestionRequest(
        basename="clip.mp4",
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=TechnicalMetadata(
            duration_ms=1000,
            width=64,
            height=48,
            video_codec="h264",
            container_formats=("mp4",),
            has_audio=False,
        ),
        representative_frames=frames,
        prompt_version=PROMPT_VERSION,
    )
    generic = build_nvidia_request_body(request, model_id="test-model")
    assert generic["chat_template_kwargs"] == {"enable_thinking": False}

    colorful = _solid_png(color=(40, 160, 90))
    sheet_frames = tuple(
        build_representative_frame(timestamp_ms=i * 10, payload=_solid_png(color=(40 + i * 20, 160, 90)))
        for i in range(3)
    )
    sheet = compose_contact_sheet(sheet_frames)
    movie_request = MovieIdentificationRequest(
        basename="film.mp4",
        contact_sheet=sheet,
        hints=LocalMovieHints(
            filename_stem="film",
            container_title=None,
            duration_ms=1000,
            width=64,
            height=48,
        ),
    )
    movie_body = build_nvidia_movie_identification_body(movie_request, model_id="test-model")
    assert movie_body["chat_template_kwargs"] == {"enable_thinking": True}
    assert movie_body["reasoning_budget"] == 2048
    assert movie_body["max_tokens"] == 4096
    assert movie_body["temperature"] == 0.6
    assert movie_body["top_p"] == 0.95
    assert "top_k" not in movie_body
    assert "thinking_token_budget" not in movie_body
    images = [
        part for part in movie_body["messages"][0]["content"] if part.get("type") == "image_url"
    ]
    assert len(images) == 1
    assert "/srv/" not in str(movie_body)
    assert "Filename stem: film" in str(movie_body)
