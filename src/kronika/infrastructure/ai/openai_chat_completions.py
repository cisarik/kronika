"""Generic OpenAI-compatible chat-completions media suggestion adapter."""

from __future__ import annotations

import base64
import json
from typing import Any

from kronika.application.media_suggestion import (
    FrameNestMediaSuggestionError,
    MediaSuggestion,
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
    MediaSuggestionRequest,
    SUGGESTION_PROVIDER_AUTH_MESSAGE,
    SUGGESTION_PROVIDER_FAILED_MESSAGE,
    SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE,
    SUGGESTION_PROVIDER_MODEL_UNAVAILABLE_MESSAGE,
    SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE,
    SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE,
)
from kronika.infrastructure.ai.constants import (
    MAX_REQUEST_BODY_BYTES,
    MAX_RESPONSE_BODY_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    SHARED_USER_AGENT,
    TEMPERATURE,
)
from kronika.infrastructure.ai.credentials import GenericAiProviderCredential
from kronika.infrastructure.ai.image_derivative import (
    FrameNestImageDerivativeError,
    PillowVlmImageDerivativeEncoder,
    VLM_JPEG_AGGREGATE_MAX_BYTES,
    VLM_JPEG_MAX_FRAMES,
    VlmImageDerivative,
    VlmImageDerivativeEncoder,
)
from kronika.infrastructure.ai.nvidia_nim import (
    JsonTransport,
    _format_timestamp_ms,
    build_media_suggestion,
    extract_message_content,
    parse_suggestion_content_text,
)
from kronika.infrastructure.ai.prompts import MEDIA_SUGGESTION_PROMPT
from kronika.infrastructure.ai.transport import (
    HttpsJsonTransport,
    HttpsTransportError,
    TRANSPORT_AUTH_REJECTED_MESSAGE,
    TRANSPORT_INVALID_RESPONSE_MESSAGE,
    TRANSPORT_MODEL_UNAVAILABLE_MESSAGE,
    TRANSPORT_RATE_LIMITED_MESSAGE,
    TRANSPORT_UNAVAILABLE_MESSAGE,
)

CHAT_COMPLETIONS_PATH = "/chat/completions"
MAX_SUGGESTION_TOKENS = 1024
CONNECTION_TEST_MAX_TOKENS = 8
VISION_PROBE_MAX_TOKENS = 16


def _metadata_summary(request: MediaSuggestionRequest) -> str:
    metadata = request.technical_metadata
    duration = "unknown" if metadata.duration_ms is None else str(metadata.duration_ms)
    containers = ", ".join(metadata.container_formats)
    return (
        f"Duration milliseconds: {duration}\n"
        f"Dimensions: {metadata.width}x{metadata.height}\n"
        f"Video codec: {metadata.video_codec}\n"
        f"Container formats: {containers}\n"
        f"Audio present: {metadata.has_audio}"
    )


def _frame_labels(request: MediaSuggestionRequest) -> str:
    total = len(request.representative_frames)
    lines: list[str] = []
    for index, frame in enumerate(request.representative_frames, start=1):
        lines.append(f"Representative frame {index} of {total}")
        lines.append(f"Timestamp: {_format_timestamp_ms(frame.timestamp_ms)}")
    return "\n".join(lines)


def _derive_vlm_images(
    request: MediaSuggestionRequest,
    image_encoder: VlmImageDerivativeEncoder,
) -> tuple[VlmImageDerivative, ...]:
    if len(request.representative_frames) > VLM_JPEG_MAX_FRAMES:
        raise FrameNestImageDerivativeError("VLM image derivative failed.")
    derivatives = tuple(image_encoder.encode_frame(frame) for frame in request.representative_frames)
    aggregate_size = sum(derivative.byte_size for derivative in derivatives)
    if aggregate_size > VLM_JPEG_AGGREGATE_MAX_BYTES:
        raise FrameNestImageDerivativeError("VLM image derivative failed.")
    return derivatives


def build_chat_completions_suggestion_body(
    request: MediaSuggestionRequest,
    *,
    model_id: str,
    image_encoder: VlmImageDerivativeEncoder | None = None,
) -> dict[str, Any]:
    """Build one OpenAI-compatible suggestion request body."""
    resolved_encoder = image_encoder or PillowVlmImageDerivativeEncoder()
    derivatives = _derive_vlm_images(request, resolved_encoder)
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "\n\n".join(
                [
                    MEDIA_SUGGESTION_PROMPT,
                    "Return only the requested JSON object.",
                    f"Filename basename: {request.basename}",
                    f"Candidate kind: {request.candidate_kind.value}",
                    _metadata_summary(request),
                    _frame_labels(request),
                ]
            ),
        }
    ]
    for derivative in derivatives:
        encoded = base64.b64encode(derivative.payload).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{derivative.mime_type};base64,{encoded}"},
            }
        )
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_SUGGESTION_TOKENS,
        "response_format": {"type": "json_object"},
    }


def build_chat_completions_connection_test_body(*, model_id: str) -> dict[str, Any]:
    """Build one text-only connection test request body."""
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": "Return the single word ok."}],
        "stream": False,
        "temperature": 0,
        "max_tokens": CONNECTION_TEST_MAX_TOKENS,
    }


def build_chat_completions_vision_probe_body(
    *,
    model_id: str,
    prompt: str,
    image: bytes,
) -> dict[str, Any]:
    """Build one single-image vision probe request body without response_format."""
    derivative = PillowVlmImageDerivativeEncoder().encode_png_bytes(image)
    encoded = base64.b64encode(derivative.payload).decode("ascii")
    return {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{derivative.mime_type};base64,{encoded}",
                        },
                    },
                ],
            }
        ],
        "stream": False,
        "temperature": 0,
        "max_tokens": VISION_PROBE_MAX_TOKENS,
    }


def _extension_from_basename(basename: str) -> str:
    if "." not in basename:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return "." + basename.rsplit(".", 1)[-1].lower()


def _decode_json_body(response: object) -> dict[str, Any]:
    body = getattr(response, "body", response)
    if not isinstance(body, (bytes, bytearray)):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        ) from None
    if not isinstance(payload, dict):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return payload


def _response_status_code(response: object) -> int:
    status_code = getattr(response, "status_code", 200)
    if not isinstance(status_code, int):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return status_code


def _status_error(status_code: int) -> Exception:
    if status_code in {401, 403}:
        return MediaSuggestionProviderAuthError(SUGGESTION_PROVIDER_AUTH_MESSAGE)
    if status_code == 429:
        return MediaSuggestionProviderRateLimitedError(SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE)
    if status_code == 404:
        return MediaSuggestionProviderModelUnavailableError(
            SUGGESTION_PROVIDER_MODEL_UNAVAILABLE_MESSAGE
        )
    if status_code >= 500:
        return MediaSuggestionProviderUnavailableError(SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE)
    return MediaSuggestionProviderInvalidResponseError(SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE)


def _map_transport_error(exc: HttpsTransportError) -> Exception:
    message = str(exc)
    if message == TRANSPORT_AUTH_REJECTED_MESSAGE:
        return MediaSuggestionProviderAuthError(SUGGESTION_PROVIDER_AUTH_MESSAGE)
    if message == TRANSPORT_RATE_LIMITED_MESSAGE:
        return MediaSuggestionProviderRateLimitedError(SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE)
    if message == TRANSPORT_MODEL_UNAVAILABLE_MESSAGE:
        return MediaSuggestionProviderModelUnavailableError(
            SUGGESTION_PROVIDER_MODEL_UNAVAILABLE_MESSAGE
        )
    if message in {
        TRANSPORT_UNAVAILABLE_MESSAGE,
        TRANSPORT_INVALID_RESPONSE_MESSAGE,
    }:
        return MediaSuggestionProviderUnavailableError(SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE)
    return MediaSuggestionProviderFailedError(SUGGESTION_PROVIDER_FAILED_MESSAGE)


class OpenAiChatCompletionsMediaSuggestionProvider:
    """OpenAI-compatible chat-completions adapter for media suggestion."""

    def __init__(
        self,
        credential: GenericAiProviderCredential,
        *,
        base_url: str,
        provider_id: str,
        model_id: str,
        transport: JsonTransport | None = None,
        image_encoder: VlmImageDerivativeEncoder | None = None,
    ) -> None:
        self._credential = credential
        self._transport = transport or HttpsJsonTransport(
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            max_response_bytes=MAX_RESPONSE_BODY_BYTES,
        )
        self._base_url = base_url
        self._model_id = model_id
        self._provider_id = provider_id
        self._image_encoder = image_encoder or PillowVlmImageDerivativeEncoder()

    def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
        required_extension = _extension_from_basename(request.basename)
        try:
            body_dict = build_chat_completions_suggestion_body(
                request,
                model_id=self._model_id,
                image_encoder=self._image_encoder,
            )
            body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except FrameNestImageDerivativeError:
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None
        payload = self._post_and_decode(body)
        content_text = extract_message_content(payload)
        parsed = parse_suggestion_content_text(content_text)
        try:
            return build_media_suggestion(
                parsed,
                required_extension=required_extension,
                provider_id=self._provider_id,
                model_id=self._model_id,
            )
        except FrameNestMediaSuggestionError:
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None

    def test_connection(self) -> None:
        body_dict = build_chat_completions_connection_test_body(model_id=self._model_id)
        body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        payload = self._post_and_decode(body)
        extract_message_content(payload)

    def probe_vision(self, *, prompt: str, image_png: bytes) -> str:
        """Run exactly one bounded single-image vision probe request."""
        try:
            body_dict = build_chat_completions_vision_probe_body(
                model_id=self._model_id,
                prompt=prompt,
                image=image_png,
            )
            body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (FrameNestImageDerivativeError, TypeError):
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None
        payload = self._post_and_decode(body)
        return extract_message_content(payload)

    def _post_and_decode(self, body: bytes) -> dict[str, Any]:
        headers = {
            "Authorization": self._credential.authorization_header(),
            "Content-Type": "application/json",
            "User-Agent": SHARED_USER_AGENT,
        }
        try:
            response = self._transport.post_json(
                self._base_url + CHAT_COMPLETIONS_PATH,
                headers=headers,
                body=body,
                max_request_bytes=MAX_REQUEST_BODY_BYTES,
            )
            status_code = _response_status_code(response)
            if status_code != 200:
                raise _status_error(status_code)
            return _decode_json_body(response)
        except HttpsTransportError as exc:
            raise _map_transport_error(exc) from None
        except (
            MediaSuggestionProviderAuthError,
            MediaSuggestionProviderInvalidResponseError,
            MediaSuggestionProviderModelUnavailableError,
            MediaSuggestionProviderRateLimitedError,
            MediaSuggestionProviderUnavailableError,
        ):
            raise
        except Exception:
            raise MediaSuggestionProviderFailedError(SUGGESTION_PROVIDER_FAILED_MESSAGE) from None

    def __repr__(self) -> str:
        return "OpenAiChatCompletionsMediaSuggestionProvider(<redacted>)"
