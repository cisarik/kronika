"""Unit tests for the NVIDIA NIM media suggestion adapter."""

from __future__ import annotations

import base64
import json

import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    PNG_SIGNATURE,
    RepresentativeFrame,
    TechnicalMetadata,
    build_representative_frame,
)
from kronika.application.media_suggestion import (
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderTruncatedResponseError,
    MediaSuggestionProviderUnavailableError,
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.infrastructure.ai.constants import (
    DEFAULT_MODEL_ID,
    NVIDIA_CHAT_COMPLETIONS_URL,
    TEMPERATURE,
    TOP_K,
)
from kronika.infrastructure.ai.credentials import NvidiaApiCredential
from kronika.infrastructure.ai.image_derivative import (
    FrameNestImageDerivativeError,
    VlmImageDerivative,
)
from kronika.infrastructure.ai.nvidia_nim import (
    NvidiaNimMediaSuggestionProvider,
    build_nvidia_connection_test_body,
    build_nvidia_request_body,
    extract_message_content,
    parse_suggestion_content_text,
)
from kronika.infrastructure.ai.transport import (
    HttpsJsonResponse,
    HttpsTransportError,
    TRANSPORT_AUTH_REJECTED_MESSAGE,
    TRANSPORT_INVALID_RESPONSE_MESSAGE,
    TRANSPORT_MODEL_UNAVAILABLE_MESSAGE,
    TRANSPORT_RATE_LIMITED_MESSAGE,
    TRANSPORT_UNAVAILABLE_MESSAGE,
)
from kronika.infrastructure.ai.vision_probe import (
    VISION_PROBE_PROMPT,
    load_vision_probe_fixture,
)

_SECRET = "test-nvidia-secret-value"
_SENSITIVE_PATH = "/sensitive-example/private-media.mp4"
_VALID_PNG = PNG_SIGNATURE + b"png"
_OTHER_VALID_PNG = PNG_SIGNATURE + b"other-png"
_THIRD_VALID_PNG = PNG_SIGNATURE + b"third-png"
_VALID_JPEG = b"\xff\xd8jpeg-one\xff\xd9"
_OTHER_VALID_JPEG = b"\xff\xd8jpeg-two\xff\xd9"
_THIRD_VALID_JPEG = b"\xff\xd8jpeg-three\xff\xd9"
_REQUEST_ID = "req_123-abc"
_STATUS_URL = f"https://integrate.api.nvidia.com/v1/status/{_REQUEST_ID}"


class _FakeTransport:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: bytes | None = None,
        error: Exception | None = None,
        get_responses: tuple[HttpsJsonResponse | Exception, ...] = (),
    ) -> None:
        self.post_response = HttpsJsonResponse(
            status_code=status_code,
            body=body if body is not None else b"",
        )
        self.post_error = error
        self.get_responses = list(get_responses)
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_body: bytes | None = None
        self.post_calls: list[tuple[str, dict[str, str], bytes]] = []
        self.get_calls: list[tuple[str, dict[str, str]]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> HttpsJsonResponse:
        self.last_url = url
        self.last_headers = headers
        self.last_body = body
        self.post_calls.append((url, dict(headers), body))
        if self.post_error is not None:
            raise self.post_error
        return self.post_response

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> HttpsJsonResponse:
        self.get_calls.append((url, dict(headers)))
        assert self.get_responses
        response = self.get_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeImageEncoder:
    def __init__(self, payloads: tuple[bytes, ...] = (_VALID_JPEG,)) -> None:
        self.payloads = list(payloads)
        self.calls: list[RepresentativeFrame] = []

    def encode_frame(self, frame: RepresentativeFrame) -> VlmImageDerivative:
        self.calls.append(frame)
        payload = self.payloads.pop(0) if self.payloads else _VALID_JPEG
        return VlmImageDerivative.from_payload(
            width=64,
            height=48,
            mime_type="image/jpeg",
            payload=payload,
        )


class _FailingImageEncoder:
    def encode_frame(self, frame: RepresentativeFrame) -> VlmImageDerivative:
        raise FrameNestImageDerivativeError("VLM image derivative failed.")


def _sample_request(
    frame_payloads: tuple[bytes, ...] = (_VALID_PNG,),
) -> MediaSuggestionRequest:
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
        representative_frames=tuple(
            build_representative_frame(timestamp_ms=index * 500, payload=payload)
            for index, payload in enumerate(frame_payloads)
        ),
        prompt_version=PROMPT_VERSION,
    )


def _provider_response_payload(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": content}}]},
        separators=(",", ":"),
    ).encode("utf-8")


def _valid_suggestion_json() -> str:
    return json.dumps(
        {
            "title": "Evening clip",
            "description": "A short evening scene with warm light.",
            "collection": "Home",
            "tags": ["evening", "clip"],
            "suggested_filename": "evening-clip.mp4",
            "confidence": 0.72,
            "evidence": ["Warm light is visible in the frame."],
            "uncertainties": ["Exact location is unknown."],
        },
        separators=(",", ":"),
    )


def test_build_request_uses_exact_endpoint_and_data_urls() -> None:
    request = _sample_request((_VALID_PNG, _OTHER_VALID_PNG, _THIRD_VALID_PNG))
    encoder = _FakeImageEncoder((_VALID_JPEG, _OTHER_VALID_JPEG, _THIRD_VALID_JPEG))
    body = build_nvidia_request_body(request, model_id=DEFAULT_MODEL_ID, image_encoder=encoder)
    encoded_frames = [
        base64.b64encode(payload).decode("ascii")
        for payload in (_VALID_JPEG, _OTHER_VALID_JPEG, _THIRD_VALID_JPEG)
    ]
    assert body["model"] == DEFAULT_MODEL_ID
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["top_k"] == 1
    assert body["max_tokens"] == 1024
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert "/no_think" not in json.dumps(body)
    assert "response_format" not in body
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "kronika-media-suggestion-v4" in content[0]["text"]
    assert "Never return more than five." in content[0]["text"]
    assert "Representative frame 1 of 3" in content[0]["text"]
    assert "Timestamp: 00:00:00.000" in content[0]["text"]
    assert "Representative frame 3 of 3" in content[0]["text"]
    assert "Timestamp: 00:00:01.000" in content[0]["text"]
    body_json = json.dumps(body)
    assert _SENSITIVE_PATH not in body_json
    assert _SECRET not in body_json
    assert "Authorization" not in body_json
    assert "Bearer" not in body_json
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert len(image_parts) == 3
    assert [
        part["image_url"]["url"] for part in image_parts
    ] == [f"data:image/jpeg;base64,{encoded}" for encoded in encoded_frames]
    assert "data:image/png" not in body_json
    assert len(encoder.calls) == 3


def test_connection_test_uses_accepted_non_thinking_text_contract() -> None:
    body = build_nvidia_connection_test_body(model_id=DEFAULT_MODEL_ID)

    assert body == {
        "model": DEFAULT_MODEL_ID,
        "messages": [{"role": "user", "content": "Return the single word ok."}],
        "stream": False,
        "temperature": TEMPERATURE,
        "top_k": TOP_K,
        "max_tokens": 8,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    encoded = json.dumps(body)
    assert "image_url" not in encoded
    assert "data:" not in encoded
    assert "audio" not in encoded
    assert "video" not in encoded
    assert "metadata" not in encoded
    assert "filename" not in encoded
    assert "catalog" not in encoded


def test_connection_test_sends_text_only_body_without_media_fields() -> None:
    transport = _FakeTransport(body=_provider_response_payload("ok"))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        model_id=DEFAULT_MODEL_ID,
    )

    provider.test_connection()

    assert len(transport.post_calls) == 1
    assert transport.get_calls == []
    url, headers, raw_body = transport.post_calls[0]
    assert url == NVIDIA_CHAT_COMPLETIONS_URL
    assert headers["Authorization"] == f"Bearer {_SECRET}"
    payload = json.loads(raw_body)
    assert payload["model"] == DEFAULT_MODEL_ID
    assert payload["messages"] == [{"role": "user", "content": "Return the single word ok."}]
    assert payload["stream"] is False
    assert payload["temperature"] == TEMPERATURE
    assert payload["top_k"] == TOP_K
    assert payload["max_tokens"] == 8
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "image_url" not in raw_body.decode("utf-8")
    assert "data:" not in raw_body.decode("utf-8")


def test_probe_vision_sends_single_shared_probe_request_and_returns_content() -> None:
    transport = _FakeTransport(body=_provider_response_payload("red"))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        model_id=DEFAULT_MODEL_ID,
    )

    text = provider.probe_vision(
        prompt=VISION_PROBE_PROMPT,
        image_png=load_vision_probe_fixture(),
    )

    assert text == "red"
    assert len(transport.post_calls) == 1
    assert transport.get_calls == []
    url, headers, raw_body = transport.post_calls[0]
    assert url == NVIDIA_CHAT_COMPLETIONS_URL
    assert headers["Authorization"] == f"Bearer {_SECRET}"
    assert "User-Agent" not in headers
    payload = json.loads(raw_body)
    assert payload["model"] == DEFAULT_MODEL_ID
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    assert "response_format" not in payload
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": VISION_PROBE_PROMPT}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "data:image/png" not in raw_body.decode("utf-8")


def test_provider_uses_immediate_200_without_status_polling() -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )
    suggestion = provider.suggest(_sample_request())
    assert suggestion.title == "Evening clip"
    assert len(transport.post_calls) == 1
    assert transport.get_calls == []


def test_provider_polls_pending_202_without_resending_frames() -> None:
    transport = _FakeTransport(
        status_code=202,
        body=json.dumps({"requestId": _REQUEST_ID}, separators=(",", ":")).encode("utf-8"),
        get_responses=(
            HttpsJsonResponse(status_code=202, body=b"{}"),
            HttpsJsonResponse(status_code=200, body=_provider_response_payload(_valid_suggestion_json())),
        ),
    )
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder((_VALID_JPEG, _OTHER_VALID_JPEG)),
        monotonic_clock=iter((0.0, 0.0, 0.5, 0.5)).__next__,
        sleep=lambda _seconds: None,
    )
    suggestion = provider.suggest(_sample_request((_VALID_PNG, _OTHER_VALID_PNG)))
    assert suggestion.suggested_filename == "evening-clip.mp4"
    assert len(transport.post_calls) == 1
    assert len(transport.get_calls) == 2
    assert transport.get_calls == [
        (_STATUS_URL, {"Authorization": f"Bearer {_SECRET}"}),
        (_STATUS_URL, {"Authorization": f"Bearer {_SECRET}"}),
    ]
    assert _REQUEST_ID not in str(suggestion)
    assert all(_SECRET not in url for url, _headers in transport.get_calls)
    assert all(b"data:image/jpeg" not in json.dumps(call).encode("utf-8") for call in transport.get_calls)


@pytest.mark.parametrize(
    "pending_payload",
    [
        {},
        {"requestId": 123},
        {"requestId": ""},
        {"requestId": "a" * 129},
        {"requestId": "../unsafe"},
        {"requestId": _REQUEST_ID, "extra": "value"},
    ],
)
def test_provider_rejects_invalid_pending_envelopes_without_leaking_id(
    pending_payload: dict[str, object],
) -> None:
    transport = _FakeTransport(
        status_code=202,
        body=json.dumps(pending_payload, separators=(",", ":")).encode("utf-8"),
    )
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )
    with pytest.raises(MediaSuggestionProviderInvalidResponseError) as exc_info:
        provider.suggest(_sample_request())
    assert str(exc_info.value) == "Media suggestion provider response was invalid."
    assert _REQUEST_ID not in str(exc_info.value)
    assert transport.get_calls == []


def test_provider_times_out_pending_status_without_leaking_request_id() -> None:
    from kronika.application.media_suggestion import (
        MediaSuggestionProviderPendingTimeoutError,
    )

    transport = _FakeTransport(
        status_code=202,
        body=json.dumps({"requestId": _REQUEST_ID}, separators=(",", ":")).encode("utf-8"),
        get_responses=(
            HttpsJsonResponse(status_code=202, body=b"{}"),
            HttpsJsonResponse(status_code=202, body=b"{}"),
        ),
    )
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
        pending_timeout_seconds=0.5,
        monotonic_clock=iter((0.0, 0.0, 0.6)).__next__,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(MediaSuggestionProviderPendingTimeoutError) as exc_info:
        provider.suggest(_sample_request())
    assert str(exc_info.value) == "Media suggestion provider pending result timed out."
    assert _REQUEST_ID not in str(exc_info.value)
    assert len(transport.post_calls) == 1
    assert len(transport.get_calls) == 1


@pytest.mark.parametrize(
    ("get_response", "expected"),
    [
        (
            HttpsTransportError(TRANSPORT_AUTH_REJECTED_MESSAGE),
            MediaSuggestionProviderAuthError,
        ),
        (
            HttpsTransportError(TRANSPORT_RATE_LIMITED_MESSAGE),
            MediaSuggestionProviderRateLimitedError,
        ),
        (
            HttpsTransportError(TRANSPORT_UNAVAILABLE_MESSAGE),
            MediaSuggestionProviderUnavailableError,
        ),
        (
            HttpsJsonResponse(status_code=200, body=b"not-json"),
            MediaSuggestionProviderFailedError,
        ),
    ],
)
def test_provider_maps_pending_poll_failures(
    get_response: HttpsJsonResponse | Exception,
    expected: type[Exception],
) -> None:
    transport = _FakeTransport(
        status_code=202,
        body=json.dumps({"requestId": _REQUEST_ID}, separators=(",", ":")).encode("utf-8"),
        get_responses=(get_response,),
    )
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
        monotonic_clock=iter((0.0, 0.0)).__next__,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(expected) as exc_info:
        provider.suggest(_sample_request())
    assert _SECRET not in str(exc_info.value)
    assert _REQUEST_ID not in str(exc_info.value)
    assert "not-json" not in str(exc_info.value)


def test_provider_sends_authorization_without_exposing_secret() -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )
    suggestion = provider.suggest(_sample_request())
    assert transport.last_url == NVIDIA_CHAT_COMPLETIONS_URL
    assert transport.last_headers is not None
    assert transport.last_headers["Authorization"] == f"Bearer {_SECRET}"
    assert suggestion.provider_id == "nvidia-nim"
    assert suggestion.model_id == DEFAULT_MODEL_ID
    assert _SECRET not in repr(provider)
    assert _SECRET not in suggestion.title


def test_provider_parses_exact_fenced_json() -> None:
    fenced = f"```json\n{_valid_suggestion_json()}\n```"
    transport = _FakeTransport(body=_provider_response_payload(fenced))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )
    suggestion = provider.suggest(_sample_request())
    assert suggestion.suggested_filename == "evening-clip.mp4"


def test_parser_accepts_one_bounded_json_object_with_trivial_final_answer_prose() -> None:
    parsed = parse_suggestion_content_text(f"Final answer:\n{_valid_suggestion_json()}\nDone.")

    assert parsed["title"] == "Evening clip"


def test_parser_rejects_multiple_candidate_objects() -> None:
    text = f"{_valid_suggestion_json()}\n{_valid_suggestion_json()}"

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        parse_suggestion_content_text(text)


def test_provider_rejects_unknown_fields_and_wrong_extension() -> None:
    bad_object = json.loads(_valid_suggestion_json())
    bad_object["extra"] = "value"
    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        parse_suggestion_content_text(json.dumps(bad_object))
    wrong_extension = json.loads(_valid_suggestion_json())
    wrong_extension["suggested_filename"] = "evening-clip.mov"
    transport = _FakeTransport(
        body=_provider_response_payload(json.dumps(wrong_extension, separators=(",", ":")))
    )
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )
    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.suggest(_sample_request())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HttpsTransportError(TRANSPORT_AUTH_REJECTED_MESSAGE), MediaSuggestionProviderAuthError),
        (HttpsTransportError(TRANSPORT_RATE_LIMITED_MESSAGE), MediaSuggestionProviderRateLimitedError),
        (
            HttpsTransportError(TRANSPORT_MODEL_UNAVAILABLE_MESSAGE),
            MediaSuggestionProviderModelUnavailableError,
        ),
        (HttpsTransportError(TRANSPORT_UNAVAILABLE_MESSAGE), MediaSuggestionProviderUnavailableError),
        (
            HttpsTransportError(TRANSPORT_INVALID_RESPONSE_MESSAGE),
            MediaSuggestionProviderInvalidResponseError,
        ),
    ],
)
def test_provider_maps_transport_failures(error: Exception, expected: type[Exception]) -> None:
    transport = _FakeTransport(error=error)
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )
    with pytest.raises(expected):
        provider.suggest(_sample_request())


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, MediaSuggestionProviderAuthError),
        (403, MediaSuggestionProviderAuthError),
        (404, MediaSuggestionProviderModelUnavailableError),
        (429, MediaSuggestionProviderRateLimitedError),
        (500, MediaSuggestionProviderUnavailableError),
        (503, MediaSuggestionProviderUnavailableError),
    ],
)
def test_connection_test_maps_http_statuses_without_raw_body(
    status_code: int,
    expected: type[Exception],
) -> None:
    transport = _FakeTransport(status_code=status_code, body=b'{"error":"raw provider body"}')
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
    )

    with pytest.raises(expected) as exc_info:
        provider.test_connection()

    assert "raw provider body" not in str(exc_info.value)


@pytest.mark.parametrize(
    "transport_message",
    [
        pytest.param(TRANSPORT_UNAVAILABLE_MESSAGE, id="dns"),
        pytest.param(TRANSPORT_UNAVAILABLE_MESSAGE, id="tcp"),
        pytest.param(TRANSPORT_UNAVAILABLE_MESSAGE, id="tls"),
        pytest.param(TRANSPORT_UNAVAILABLE_MESSAGE, id="timeout"),
    ],
)
def test_connection_test_maps_dns_tcp_tls_and_timeout_unavailable_to_provider_unreachable(
    transport_message: str,
) -> None:
    transport = _FakeTransport(error=HttpsTransportError(transport_message))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
    )

    with pytest.raises(MediaSuggestionProviderUnavailableError):
        provider.test_connection()


def test_connection_test_maps_invalid_transport_response_to_invalid_response() -> None:
    transport = _FakeTransport(
        error=HttpsTransportError(TRANSPORT_INVALID_RESPONSE_MESSAGE),
    )
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
    )

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.test_connection()


def test_extract_message_content_rejects_missing_choice() -> None:
    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        extract_message_content({"choices": []})


def test_default_provider_sends_jpeg_not_png() -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder((_VALID_JPEG,)),
    )

    provider.suggest(_sample_request((_VALID_PNG,)))

    assert transport.last_body is not None
    body = transport.last_body.decode("utf-8")
    assert "data:image/jpeg;base64," in body
    assert "data:image/png;base64," not in body
    assert base64.b64encode(_VALID_PNG).decode("ascii") not in body


def test_provider_enforces_derivative_failures_as_invalid_response() -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FailingImageEncoder(),
    )

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.suggest(_sample_request())

    assert transport.post_calls == []


def test_provider_rejects_more_than_three_images_before_transport() -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder((_VALID_JPEG,) * 4),
    )

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.suggest(_sample_request((_VALID_PNG, _OTHER_VALID_PNG, _THIRD_VALID_PNG, _VALID_PNG)))

    assert transport.post_calls == []


def test_provider_enforces_aggregate_derivative_bound_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder((_VALID_JPEG, _OTHER_VALID_JPEG)),
    )
    monkeypatch.setattr("kronika.infrastructure.ai.nvidia_nim.VLM_JPEG_AGGREGATE_MAX_BYTES", 1)

    with pytest.raises(MediaSuggestionProviderInvalidResponseError):
        provider.suggest(_sample_request((_VALID_PNG, _OTHER_VALID_PNG)))

    assert transport.post_calls == []


def test_reasoning_content_is_not_used_as_final_content() -> None:
    payload = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": None,
                    "reasoning_content": _valid_suggestion_json(),
                },
            }
        ]
    }
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        _FakeTransport(body=json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        image_encoder=_FakeImageEncoder(),
    )

    with pytest.raises(MediaSuggestionProviderInvalidResponseError) as exc_info:
        provider.suggest(_sample_request())
    assert type(exc_info.value) is MediaSuggestionProviderInvalidResponseError
    assert not isinstance(exc_info.value, MediaSuggestionProviderTruncatedResponseError)

def test_valid_v2_json_produces_validated_suggestion() -> None:
    transport = _FakeTransport(body=_provider_response_payload(_valid_suggestion_json()))
    provider = NvidiaNimMediaSuggestionProvider(
        NvidiaApiCredential(_SECRET),
        transport,
        image_encoder=_FakeImageEncoder(),
    )

    suggestion = provider.suggest(_sample_request())

    assert suggestion.prompt_version == "kronika-media-suggestion-v4"
    assert suggestion.title == "Evening clip"
