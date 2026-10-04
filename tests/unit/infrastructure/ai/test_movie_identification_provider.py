"""Fake-provider accounting for movie identification versus generic analysis."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image

from kronika.application.media_analysis import TechnicalMetadata, build_representative_frame
from kronika.application.movie_identification import (
    MovieIdentificationRequest,
    MovieIdentificationSuggestion,
    parse_movie_identification_payload,
)
from kronika.infrastructure.ai.nvidia_nim import (
    build_nvidia_movie_identification_body,
    build_nvidia_request_body,
)
from kronika.application.movie_identification import LocalMovieHints
from kronika.infrastructure.media_analysis.contact_sheet import (
    compose_contact_sheet,
)
from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_suggestion import MediaSuggestionRequest, PROMPT_VERSION


def _png(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (48, 32), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class FakeMovieProvider:
    calls: list[object] = field(default_factory=list)
    fail_before_submit: bool = False
    fail_after_submit: bool = False
    payload: dict[str, object] | None = None

    def identify_movie(self, request: MovieIdentificationRequest) -> MovieIdentificationSuggestion:
        if self.fail_before_submit:
            raise RuntimeError("pre-submit failure")
        self.calls.append(request)
        if self.fail_after_submit:
            raise RuntimeError("post-submit failure")
        return parse_movie_identification_payload(
            self.payload
            or {
                "identified_title": None,
                "release_year": None,
                "identification_status": "unknown",
                "confidence": "unknown",
                "candidate_titles": [],
                "genres": [],
                "description": "Unknown film.",
                "tags": [],
                "evidence_summary": "Insufficient evidence.",
            },
            provider_id="fake",
            model_id="fake-model",
            derivative_count=1,
        )


def test_fake_provider_single_call_and_no_path_leak() -> None:
    provider = FakeMovieProvider(
        payload={
            "identified_title": "Synthetic Movie",
            "release_year": 2001,
            "identification_status": "identified",
            "confidence": "high",
            "candidate_titles": [],
            "genres": ["Drama"],
            "description": "Synthetic confident identification.",
            "tags": ["Cinema"],
            "evidence_summary": "Opening title card.",
        }
    )
    frames = tuple(
        build_representative_frame(timestamp_ms=i * 100, payload=_png((30 + i * 40, 90, 120)))
        for i in range(3)
    )
    sheet = compose_contact_sheet(frames)
    request = MovieIdentificationRequest(
        basename="synthetic.mp4",
        contact_sheet=sheet,
        hints=LocalMovieHints(
            filename_stem="synthetic",
            container_title=None,
            duration_ms=3000,
            width=48,
            height=32,
        ),
    )
    body = build_nvidia_movie_identification_body(request, model_id="fake-model")
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["reasoning_budget"] == 2048
    assert body["max_tokens"] == 4096
    assert body["temperature"] == 0.6
    assert body["top_p"] == 0.95
    assert "top_k" not in body
    assert "thinking_token_budget" not in body
    assert body["stream"] is False
    assert sum(1 for part in body["messages"][0]["content"] if part.get("type") == "image_url") == 1
    assert "/home/" not in str(body)
    assert "/srv/" not in str(body)

    suggestion = provider.identify_movie(request)
    assert len(provider.calls) == 1
    assert suggestion.identified_title == "Synthetic Movie"

    # Idempotent second local call is a separate owner request in tests; count stays explicit.
    provider.identify_movie(request)
    assert len(provider.calls) == 2


def test_generic_body_keeps_thinking_false() -> None:
    frame = build_representative_frame(timestamp_ms=0, payload=_png((10, 20, 30)))
    request = MediaSuggestionRequest(
        basename="clip.mp4",
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=TechnicalMetadata(
            duration_ms=1000,
            width=48,
            height=32,
            video_codec="h264",
            container_formats=("mp4",),
            has_audio=False,
        ),
        representative_frames=(frame,),
        prompt_version=PROMPT_VERSION,
    )
    body = build_nvidia_request_body(request, model_id="fake-model")
    assert body["chat_template_kwargs"]["enable_thinking"] is False
