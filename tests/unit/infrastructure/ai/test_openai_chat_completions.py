"""Unit tests for the generic OpenAI-compatible chat-completions adapter."""

from __future__ import annotations

import json

import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    PNG_SIGNATURE,
    TechnicalMetadata,
    build_representative_frame,
)
from kronika.application.media_suggestion import (
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.infrastructure.ai.constants import (
    MAX_REQUEST_BODY_BYTES,
    MAX_RESPONSE_BODY_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    SHARED_USER_AGENT,
)
from kronika.infrastructure.ai.credentials import GenericAiProviderCredential
from kronika.infrastructure.ai.image_derivative import VlmImageDerivative
from kronika.infrastructure.ai.openai_chat_completions import (
    VISION_PROBE_MAX_TOKENS,
    OpenAiChatCompletionsMediaSuggestionProvider,
    build_chat_completions_connection_test_body,
    build_chat_completions_suggestion_body,
    build_chat_completions_vision_probe_body,
)
from kronika.infrastructure.ai.transport import HttpsJsonResponse
from kronika.infrastructure.ai.vision_probe import (
    VISION_PROBE_PROMPT,
    load_vision_probe_fixture,
)

BASE_URL = "https://opencode.ai/zen/go/v1"
CHAT_COMPLETIONS_URL = BASE_URL + "/chat/completions"
MODEL_ID = "deepseek-v4-flash-vision-exp"
CREDENTIAL_SENTINEL = "synthetic-declared-credential"


class _Transport:
    def __init__(self, response: HttpsJsonResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], bytes, int]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> HttpsJsonResponse:
        self.calls.append((url, headers, body, max_request_bytes))
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
        representative_frames=(
            build_representative_frame(
                timestamp_ms=0,
                payload=PNG_SIGNATURE + b"frame",
            ),
        ),
        prompt_version=PROMPT_VERSION,
    )


def _provider(transport: _Transport) -> OpenAiChatCompletionsMediaSuggestionProvider:
    return OpenAiChatCompletionsMediaSuggestionProvider(
        GenericAiProviderCredential(CREDENTIAL_SENTINEL),
        base_url=BASE_URL,
        provider_id="opencode-go",
        model_id=MODEL_ID,
        transport=transport,
        image_encoder=_ImageEncoder(),
    )


def _success_response(content: str) -> HttpsJsonResponse:
    return HttpsJsonResponse(
        status_code=200,
        body=json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"),
    )


def _suggestion_content() -> str:
    return json.dumps(
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


def test_connection_test_is_text_only_single_call_and_carries_user_agent() -> None:
    transport = _Transport(_success_response("ok"))
    provider = _provider(transport)

    provider.test_connection()

    assert len(transport.calls) == 1
    url, headers, body, max_request_bytes = transport.calls[0]
    assert url == CHAT_COMPLETIONS_URL
    assert headers["Authorization"] == f"Bearer {CREDENTIAL_SENTINEL}"
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == SHARED_USER_AGENT
    assert max_request_bytes == MAX_REQUEST_BODY_BYTES
    for name, value in headers.items():
        if name == "Authorization":
            continue
        assert CREDENTIAL_SENTINEL not in value
    payload = json.loads(body)
    assert payload == build_chat_completions_connection_test_body(model_id=MODEL_ID)
    assert payload["messages"] == [{"role": "user", "content": "Return the single word ok."}]
    assert "image_url" not in body.decode("utf-8")
    assert "data:" not in body.decode("utf-8")


def test_suggestion_request_frames_images_and_keeps_response_format() -> None:
    transport = _Transport(_success_response(_suggestion_content()))
    provider = _provider(transport)

    suggestion = provider.suggest(_request())

    assert len(transport.calls) == 1
    url, headers, body, _max_request_bytes = transport.calls[0]
    assert url == CHAT_COMPLETIONS_URL
    assert headers["User-Agent"] == SHARED_USER_AGENT
    text = body.decode("utf-8")
    assert CREDENTIAL_SENTINEL not in text
    assert "data:image/jpeg;base64" in text
    assert "clip.mp4" in text
    assert "/Users/" not in text
    payload = json.loads(body)
    assert payload["model"] == MODEL_ID
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["content"][0]["type"] == "text"
    assert payload["messages"][0]["content"][1]["type"] == "image_url"
    assert suggestion.provider_id == "opencode-go"
    assert suggestion.model_id == MODEL_ID


def test_suggestion_body_builder_matches_the_adapter_request() -> None:
    body = build_chat_completions_suggestion_body(
        _request(),
        model_id=MODEL_ID,
        image_encoder=_ImageEncoder(),
    )

    assert body["model"] == MODEL_ID
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


def test_request_url_is_base_url_plus_chat_completions() -> None:
    transport = _Transport(_success_response("ok"))
    provider = _provider(transport)

    provider.test_connection()

    assert transport.calls[0][0] == BASE_URL + "/chat/completions"


def test_default_transport_is_bounded_and_timed_out() -> None:
    provider = OpenAiChatCompletionsMediaSuggestionProvider(
        GenericAiProviderCredential(CREDENTIAL_SENTINEL),
        base_url=BASE_URL,
        provider_id="opencode-go",
        model_id=MODEL_ID,
    )

    assert provider._transport._timeout_seconds == REQUEST_TIMEOUT_SECONDS
    assert provider._transport._max_response_bytes == MAX_RESPONSE_BODY_BYTES


def test_repr_is_redacted() -> None:
    provider = _provider(_Transport(_success_response("ok")))

    assert repr(provider) == "OpenAiChatCompletionsMediaSuggestionProvider(<redacted>)"
    assert CREDENTIAL_SENTINEL not in repr(provider)


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_status_maps_to_authentication_error(status_code: int) -> None:
    transport = _Transport(HttpsJsonResponse(status_code=status_code, body=b'{"error":"raw"}'))
    provider = _provider(transport)

    with pytest.raises(MediaSuggestionProviderAuthError) as exc_info:
        provider.test_connection()

    assert "raw" not in str(exc_info.value)
    assert len(transport.calls) == 1


def test_rate_limit_status_maps_to_rate_limited() -> None:
    provider = _provider(
        _Transport(HttpsJsonResponse(status_code=429, body=b'{"error":"raw"}'))
    )

    with pytest.raises(MediaSuggestionProviderRateLimitedError):
        provider.test_connection()


def test_missing_model_status_maps_to_model_unavailable() -> None:
    provider = _provider(
        _Transport(HttpsJsonResponse(status_code=404, body=b'{"error":"raw"}'))
    )

    with pytest.raises(MediaSuggestionProviderModelUnavailableError):
        provider.test_connection()


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_server_status_maps_to_provider_unavailable(status_code: int) -> None:
    provider = _provider(
        _Transport(HttpsJsonResponse(status_code=status_code, body=b'{"error":"raw"}'))
    )

    with pytest.raises(MediaSuggestionProviderUnavailableError):
        provider.test_connection()


@pytest.mark.parametrize("status_code", [400, 422])
def test_other_client_status_maps_to_invalid_response(status_code: int) -> None:
    provider = _provider(
        _Transport(HttpsJsonResponse(status_code=status_code, body=b"{}"))
    )

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.test_connection()


def test_bad_json_body_maps_to_invalid_response() -> None:
    provider = _provider(_Transport(HttpsJsonResponse(status_code=200, body=b"{not json")))

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.test_connection()


def test_transport_failure_maps_to_sanitized_error() -> None:
    class _FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(self, url: str, *, headers: dict[str, str], body: bytes, max_request_bytes: int) -> object:
            self.calls += 1
            raise OSError("raw transport failure")

    transport = _FailingTransport()
    provider = OpenAiChatCompletionsMediaSuggestionProvider(
        GenericAiProviderCredential(CREDENTIAL_SENTINEL),
        base_url=BASE_URL,
        provider_id="opencode-go",
        model_id=MODEL_ID,
        transport=transport,
    )

    with pytest.raises(MediaSuggestionProviderFailedError) as exc_info:
        provider.test_connection()

    assert transport.calls == 1
    assert "raw" not in str(exc_info.value)


def test_credential_value_never_appears_in_request_body() -> None:
    transport = _Transport(_success_response(_suggestion_content()))
    provider = _provider(transport)

    provider.suggest(_request())

    body = transport.calls[0][2]
    assert CREDENTIAL_SENTINEL.encode("utf-8") not in body


def test_vision_probe_body_uses_single_image_and_no_response_format() -> None:
    body = build_chat_completions_vision_probe_body(
        model_id=MODEL_ID,
        prompt=VISION_PROBE_PROMPT,
        image=load_vision_probe_fixture(),
    )

    assert body["model"] == MODEL_ID
    assert body["stream"] is False
    assert body["temperature"] == 0
    assert body["max_tokens"] == VISION_PROBE_MAX_TOKENS
    assert "response_format" not in body
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": VISION_PROBE_PROMPT}
    assert len(content) == 2
    assert content[1]["type"] == "image_url"
    encoded = json.dumps(body)
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "data:image/png" not in encoded
    assert "/Users/" not in encoded


def test_probe_vision_performs_one_bounded_call_and_returns_content() -> None:
    transport = _Transport(_success_response("red"))
    provider = _provider(transport)

    text = provider.probe_vision(
        prompt=VISION_PROBE_PROMPT,
        image_png=load_vision_probe_fixture(),
    )

    assert text == "red"
    assert len(transport.calls) == 1
    url, headers, body, max_request_bytes = transport.calls[0]
    assert url == CHAT_COMPLETIONS_URL
    assert headers["User-Agent"] == SHARED_USER_AGENT
    assert max_request_bytes == MAX_REQUEST_BODY_BYTES
    assert CREDENTIAL_SENTINEL not in body.decode("utf-8")


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (403, MediaSuggestionProviderAuthError),
        (429, MediaSuggestionProviderRateLimitedError),
        (404, MediaSuggestionProviderModelUnavailableError),
        (500, MediaSuggestionProviderUnavailableError),
        (400, MediaSuggestionProviderInvalidResponseError),
    ],
)
def test_probe_vision_maps_status_errors(
    status_code: int,
    expected_error: type[Exception],
) -> None:
    transport = _Transport(HttpsJsonResponse(status_code=status_code, body=b'{"error":"raw"}'))
    provider = _provider(transport)

    with pytest.raises(expected_error):
        provider.probe_vision(prompt=VISION_PROBE_PROMPT, image_png=load_vision_probe_fixture())

    assert len(transport.calls) == 1


def test_probe_vision_rejects_invalid_image_without_call() -> None:
    transport = _Transport(_success_response("red"))
    provider = _provider(transport)

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.probe_vision(prompt=VISION_PROBE_PROMPT, image_png=b"not a png")

    assert transport.calls == []


def test_probe_vision_rejects_bad_json_response() -> None:
    transport = _Transport(HttpsJsonResponse(status_code=200, body=b"{not json"))
    provider = _provider(transport)

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.probe_vision(prompt=VISION_PROBE_PROMPT, image_png=load_vision_probe_fixture())
