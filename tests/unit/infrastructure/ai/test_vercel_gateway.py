"""Unit tests for the Vercel AI Gateway media suggestion adapter."""

from __future__ import annotations

import json

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import TechnicalMetadata, build_representative_frame
from kronika.application.media_analysis import PNG_SIGNATURE
from kronika.application.media_suggestion import (
    MediaSuggestionProviderAuthError,
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.infrastructure.ai.constants import (
    VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL,
    VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
)
from kronika.infrastructure.ai.credentials import VercelAiGatewayCredential
from kronika.infrastructure.ai.image_derivative import VlmImageDerivative
from kronika.infrastructure.ai.transport import HttpsJsonResponse
from kronika.infrastructure.ai.vercel_gateway import VercelAiGatewayMediaSuggestionProvider


class _Transport:
    def __init__(self, response: HttpsJsonResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> HttpsJsonResponse:
        self.calls.append((url, headers, body))
        return self.response


class _ImageEncoder:
    def encode_frame(self, frame: object) -> VlmImageDerivative:
        return VlmImageDerivative.from_payload(
            width=64,
            height=48,
            mime_type="image/jpeg",
            payload=b"\xff\xd8jpeg\xff\xd9",
        )


def _request() -> MediaSuggestionRequest:
    return MediaSuggestionRequest(
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
        representative_frames=(build_representative_frame(timestamp_ms=0, payload=PNG_SIGNATURE + b"frame"),),
        prompt_version=PROMPT_VERSION,
    )


def test_connection_test_is_text_only_and_uses_default_model() -> None:
    transport = _Transport(
        HttpsJsonResponse(status_code=200, body=b'{"choices":[{"message":{"content":"ok"}}]}')
    )
    provider = VercelAiGatewayMediaSuggestionProvider(
        VercelAiGatewayCredential("secret"),
        transport=transport,
    )

    provider.test_connection()

    assert len(transport.calls) == 1
    url, headers, body = transport.calls[0]
    assert url == VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL
    assert headers["Authorization"] == "Bearer secret"
    payload = json.loads(body)
    assert payload["model"] == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert payload["messages"] == [{"role": "user", "content": "Return the single word ok."}]
    assert "image_url" not in body.decode("utf-8")
    assert "data:" not in body.decode("utf-8")


def test_suggest_uses_validated_json_contract_and_no_absolute_path() -> None:
    response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "title": "Clip title",
                            "description": "Clip description",
                            "collection": "Clips",
                            "tags": ["Clip"],
                            "suggested_filename": "clip-title.mp4",
                            "confidence": 0.8,
                            "evidence": ["Visible motion"],
                            "uncertainties": ["Context unknown"],
                        }
                    )
                }
            }
        ]
    }
    transport = _Transport(
        HttpsJsonResponse(status_code=200, body=json.dumps(response_body).encode("utf-8"))
    )
    provider = VercelAiGatewayMediaSuggestionProvider(
        VercelAiGatewayCredential("secret"),
        transport=transport,
        image_encoder=_ImageEncoder(),
    )

    suggestion = provider.suggest(_request())

    assert suggestion.provider_id == "vercel-ai-gateway"
    assert suggestion.model_id == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    body = transport.calls[0][2].decode("utf-8")
    assert "clip.mp4" in body
    assert "/Users/" not in body
    assert "data:image/jpeg;base64" in body


def test_auth_failure_is_sanitized() -> None:
    transport = _Transport(HttpsJsonResponse(status_code=401, body=b'{"error":"raw"}'))
    provider = VercelAiGatewayMediaSuggestionProvider(
        VercelAiGatewayCredential("secret"),
        transport=transport,
    )

    try:
        provider.test_connection()
    except MediaSuggestionProviderAuthError as exc:
        assert "raw" not in str(exc)
    else:
        raise AssertionError("expected auth error")
