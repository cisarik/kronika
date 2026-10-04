"""NVIDIA NIM media suggestion adapter."""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Callable, Protocol

from kronika.application.media_suggestion import (
    FrameNestMediaSuggestionError,
    MediaSuggestion,
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderEmptyResponseError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderPendingTimeoutError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderRefusalError,
    MediaSuggestionProviderTruncatedResponseError,
    MediaSuggestionProviderUnavailableError,
    MediaSuggestionRequest,
    PROMPT_VERSION,
    SUGGESTION_PROVIDER_AUTH_MESSAGE,
    SUGGESTION_PROVIDER_EMPTY_RESPONSE_MESSAGE,
    SUGGESTION_PROVIDER_FAILED_MESSAGE,
    SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE,
    SUGGESTION_PROVIDER_MODEL_UNAVAILABLE_MESSAGE,
    SUGGESTION_PROVIDER_PENDING_TIMEOUT_MESSAGE,
    SUGGESTION_PROVIDER_RATE_LIMITED_MESSAGE,
    SUGGESTION_PROVIDER_REFUSAL_MESSAGE,
    SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE,
    validate_suggested_filename,
)
from kronika.infrastructure.ai.constants import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER_ID,
    MAX_REQUEST_BODY_BYTES,
    MAX_RESPONSE_BODY_BYTES,
    NVIDIA_CHAT_COMPLETIONS_URL,
    REQUEST_TIMEOUT_SECONDS,
    TEMPERATURE,
    MAX_TOKENS,
    TOP_K,
)
from kronika.infrastructure.ai.credentials import NvidiaApiCredential
from kronika.infrastructure.ai.image_derivative import (
    FrameNestImageDerivativeError,
    PillowVlmImageDerivativeEncoder,
    VLM_JPEG_AGGREGATE_MAX_BYTES,
    VLM_JPEG_MAX_FRAMES,
    VlmImageDerivative,
    VlmImageDerivativeEncoder,
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
from kronika.structured_logging import LogLevel, get_logger

_JSON_FENCE_PATTERN = re.compile(r"^```json\s*\n(?P<body>.*)\n```\s*$", re.DOTALL)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STRUCTURAL_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_NVIDIA_STATUS_URL_PREFIX = "https://integrate.api.nvidia.com/v1/status/"
_PENDING_POLL_INTERVAL_SECONDS = 1.0
_PENDING_TIMEOUT_SECONDS = 120.0
MOVIE_STRUCTURED_OUTPUT_COMPATIBILITY_MODE = "nemotron_embedded_think_json_v1"
_MOVIE_IDENTIFICATION_KEYS = frozenset(
    {
        "identified_title",
        "release_year",
        "identification_status",
        "confidence",
        "candidate_titles",
        "genres",
        "description",
        "tags",
        "evidence_summary",
    }
)
_SAFE_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls"})
LOGGER = get_logger("nvidia_nim")
_ALLOWED_SUGGESTION_KEYS = frozenset(
    {
        "title",
        "description",
        "collection",
        "tags",
        "suggested_filename",
        "confidence",
        "evidence",
        "uncertainties",
    }
)


def _format_timestamp_ms(timestamp_ms: int) -> str:
    hours = timestamp_ms // 3_600_000
    remainder = timestamp_ms % 3_600_000
    minutes = remainder // 60_000
    remainder %= 60_000
    seconds = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> object:
        """Post one bounded JSON request."""

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> object:
        """Get one bounded JSON response."""


def _extension_from_basename(basename: str) -> str:
    if "." not in basename:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return "." + basename.rsplit(".", 1)[-1].lower()


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


def build_nvidia_request_body(
    request: MediaSuggestionRequest,
    *,
    model_id: str,
    image_encoder: VlmImageDerivativeEncoder | None = None,
) -> dict[str, Any]:
    """Build the NVIDIA chat-completions request body for one suggestion request."""
    resolved_encoder = image_encoder or PillowVlmImageDerivativeEncoder()
    derivatives = _derive_vlm_images(request, resolved_encoder)
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "\n\n".join(
                [
                    MEDIA_SUGGESTION_PROMPT,
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
        "messages": [
            {"role": "user", "content": content},
        ],
        "stream": False,
        "temperature": TEMPERATURE,
        "top_k": TOP_K,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def build_nvidia_movie_identification_body(
    request: object,
    *,
    model_id: str,
) -> dict[str, Any]:
    """Build one NVIDIA request for movie identification with reasoning enabled."""
    from kronika.application.movie_identification import movie_identification_prompt
    from kronika.domain.media_classification import (
        MOVIE_IDENTIFICATION_MAX_TOKENS,
        MOVIE_IDENTIFICATION_REASONING_BUDGET,
        MOVIE_IDENTIFICATION_TEMPERATURE,
        MOVIE_IDENTIFICATION_TOP_P,
    )

    if MOVIE_IDENTIFICATION_MAX_TOKENS <= MOVIE_IDENTIFICATION_REASONING_BUDGET:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    encoded = base64.b64encode(request.contact_sheet.payload).decode("ascii")
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": movie_identification_prompt(hints=request.hints),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{request.contact_sheet.mime_type};base64,{encoded}",
            },
        },
    ]
    # integrate.api.nvidia.com: Thinking-mode sampling + top-level reasoning_budget.
    # Do not duplicate nested/budget aliases or instruct-mode top_k.
    return {
        "model": model_id,
        "messages": [
            {"role": "user", "content": content},
        ],
        "stream": False,
        "temperature": MOVIE_IDENTIFICATION_TEMPERATURE,
        "top_p": MOVIE_IDENTIFICATION_TOP_P,
        "max_tokens": MOVIE_IDENTIFICATION_MAX_TOKENS,
        "reasoning_budget": MOVIE_IDENTIFICATION_REASONING_BUDGET,
        "chat_template_kwargs": {
            "enable_thinking": True,
        },
    }


def build_nvidia_connection_test_body(*, model_id: str) -> dict[str, Any]:
    """Build a text-only NVIDIA provider connection test request."""
    return {
        "model": model_id,
        "messages": [
            {"role": "user", "content": "Return the single word ok."},
        ],
        "stream": False,
        "temperature": TEMPERATURE,
        "top_k": TOP_K,
        "max_tokens": 8,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def classify_chat_completion_choice(payload: dict[str, Any]) -> dict[str, Any]:
    """Return sanitized structural facts about one chat-completions choice.

    Never returns prompt text, reasoning traces, or media payloads.
    """
    facts: dict[str, Any] = {
        "parser_stage": "provider_envelope",
        "choice_count": None,
        "selected_choice_index": None,
        "finish_reason": None,
        "content_repr": None,
        "final_content_length_bucket": None,
        "final_content_empty": None,
        "has_reasoning_field": None,
        "reasoning_repr": None,
        "has_refusal_field": None,
        "top_level_keys": _bounded_key_names(payload),
        "message_keys": None,
        "detected_wrapper_category": "not_inspected",
        "json_decode_stage": "not_started",
        "decoded_top_level_type": None,
        "decoded_top_level_keys": None,
        "schema_validation_stage": "not_started",
        "schema_error_category": None,
    }
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return facts
    facts["choice_count"] = len(choices)
    if not choices:
        facts["parser_stage"] = "choice_selection"
        return facts
    if len(choices) != 1:
        facts["parser_stage"] = "choice_selection"
        facts["detected_wrapper_category"] = "multiple_choices"
        return facts
    first = choices[0]
    if not isinstance(first, dict):
        facts["parser_stage"] = "choice_selection"
        return facts
    facts["selected_choice_index"] = 0
    finish_reason = first.get("finish_reason")
    facts["finish_reason"] = _safe_finish_reason(finish_reason)
    message = first.get("message")
    if not isinstance(message, dict):
        facts["parser_stage"] = "final_content_extraction"
        return facts
    facts["message_keys"] = _bounded_key_names(message)
    content = message.get("content")
    reasoning_values = [
        message[key]
        for key in ("reasoning", "reasoning_content")
        if key in message and message[key] not in (None, "")
    ]
    facts["has_reasoning_field"] = bool(reasoning_values)
    facts["reasoning_repr"] = (
        _representation_type(reasoning_values[0]) if reasoning_values else None
    )
    refusal = message.get("refusal")
    facts["has_refusal_field"] = isinstance(refusal, str) and bool(refusal.strip())
    if content is None:
        content_repr = "null"
        final_empty = True
    elif isinstance(content, str):
        content_repr = "string"
        final_empty = not bool(content.strip())
    elif isinstance(content, list):
        content_repr = "array"
        final_empty = len(content) == 0
    else:
        content_repr = _representation_type(content)
        final_empty = True
    facts["parser_stage"] = "final_content_extraction"
    facts["content_repr"] = content_repr
    facts["final_content_empty"] = final_empty
    facts["final_content_length_bucket"] = _content_length_bucket(content)
    facts["detected_wrapper_category"] = _detect_content_wrapper(content)
    return facts


def _bounded_key_names(mapping: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for raw_key in sorted(mapping.keys(), key=lambda key: str(key)):
        if len(names) == 16:
            names.append("__additional_keys__")
            break
        if isinstance(raw_key, str) and _STRUCTURAL_KEY_PATTERN.fullmatch(raw_key):
            names.append(raw_key)
        else:
            names.append("__other_key__")
    return names


def _safe_finish_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value in _SAFE_FINISH_REASONS else "other"


def _representation_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return "other"


def _content_length_bucket(content: object) -> str | None:
    if isinstance(content, str):
        length = len(content)
    elif isinstance(content, list):
        length = len(content)
    else:
        return None
    if length == 0:
        return "empty"
    if length <= 256:
        return "1-256"
    if length <= 1024:
        return "257-1024"
    if length <= 4096:
        return "1025-4096"
    return "4097+"


def _detect_content_wrapper(content: object) -> str:
    if content is None:
        return "null_content"
    if isinstance(content, list):
        return "content_list"
    if not isinstance(content, str):
        return "unsupported_content_type"
    candidate = content.strip()
    if not candidate:
        return "empty_content"
    if candidate.startswith("<think>"):
        if candidate.count("<think>") == 1 and candidate.count("</think>") == 1:
            return "embedded_think_prefix"
        return "ambiguous_reasoning_tags"
    if "<think>" in candidate or "</think>" in candidate:
        return "ambiguous_reasoning_tags"
    if _JSON_FENCE_PATTERN.fullmatch(candidate):
        return "json_fence"
    if candidate.startswith("{") and candidate.endswith("}"):
        return "raw_json_object"
    return "prose_or_ambiguous"


def extract_message_content(payload: dict[str, Any]) -> str:
    """Extract provider message content from one chat-completions envelope."""
    facts = classify_chat_completion_choice(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    first = choices[0]
    if not isinstance(first, dict):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    message = first.get("message")
    if not isinstance(message, dict):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        if facts.get("finish_reason") == "length":
            raise MediaSuggestionProviderTruncatedResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            )
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return content.strip()


def extract_movie_message_content(
    payload: dict[str, Any],
    *,
    facts: dict[str, Any] | None = None,
) -> str:
    """Extract one narrow movie final answer without consuming reasoning text."""
    structural = facts if facts is not None else classify_chat_completion_choice(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        structural["parser_stage"] = "choice_selection"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    first = choices[0]
    if not isinstance(first, dict):
        structural["parser_stage"] = "choice_selection"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    message = first.get("message")
    if not isinstance(message, dict):
        structural["parser_stage"] = "final_content_extraction"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        structural["parser_stage"] = "final_content_extraction"
        structural["detected_wrapper_category"] = "refusal"
        raise MediaSuggestionProviderRefusalError(SUGGESTION_PROVIDER_REFUSAL_MESSAGE)
    content = message.get("content")
    if content is None or (isinstance(content, str) and not content.strip()):
        structural["parser_stage"] = "final_content_extraction"
        if structural.get("finish_reason") == "length":
            raise MediaSuggestionProviderTruncatedResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            )
        raise MediaSuggestionProviderEmptyResponseError(
            SUGGESTION_PROVIDER_EMPTY_RESPONSE_MESSAGE
        )
    if not isinstance(content, str):
        structural["parser_stage"] = "final_content_extraction"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    if structural.get("finish_reason") == "length":
        structural["parser_stage"] = "final_content_extraction"
        raise MediaSuggestionProviderTruncatedResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )

    candidate = content.strip()
    wrapper = _detect_content_wrapper(candidate)
    structural["detected_wrapper_category"] = wrapper
    if candidate.startswith("<think>"):
        if candidate.count("<think>") != 1 or candidate.count("</think>") != 1:
            structural["detected_wrapper_category"] = "ambiguous_reasoning_tags"
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            )
        closing_tag_index = candidate.find("</think>")
        final_answer = candidate[closing_tag_index + len("</think>") :]
        if closing_tag_index < 0 or not final_answer.strip():
            structural["detected_wrapper_category"] = "embedded_think_without_final"
            if structural.get("finish_reason") == "length":
                raise MediaSuggestionProviderTruncatedResponseError(
                    SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
                )
            raise MediaSuggestionProviderEmptyResponseError(
                SUGGESTION_PROVIDER_EMPTY_RESPONSE_MESSAGE
            )
        candidate = final_answer.strip()
        if "<think>" in candidate or "</think>" in candidate:
            structural["detected_wrapper_category"] = "ambiguous_reasoning_tags"
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            )
        structural["detected_wrapper_category"] = "embedded_think_prefix"
    elif "<think>" in candidate or "</think>" in candidate:
        structural["detected_wrapper_category"] = "ambiguous_reasoning_tags"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )

    fence_match = _JSON_FENCE_PATTERN.fullmatch(candidate)
    if fence_match:
        candidate = fence_match.group("body").strip()
        prior_wrapper = structural["detected_wrapper_category"]
        structural["detected_wrapper_category"] = (
            "embedded_think_json_fence"
            if prior_wrapper == "embedded_think_prefix"
            else "json_fence"
        )
    elif not (candidate.startswith("{") and candidate.endswith("}")):
        structural["detected_wrapper_category"] = "prose_or_ambiguous"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    structural["parser_stage"] = "json_decoding"
    return candidate


def parse_movie_json_object_content_text(
    text: str,
    *,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decode one already-normalized movie JSON object, failing closed."""
    structural = facts if facts is not None else {}
    structural["json_decode_stage"] = "started"
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, UnicodeError):
        structural["json_decode_stage"] = "failed"
        structural["decoded_top_level_type"] = None
        structural["parser_stage"] = "json_decoding"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        ) from None
    structural["json_decode_stage"] = "succeeded"
    structural["decoded_top_level_type"] = _representation_type(parsed)
    if not isinstance(parsed, dict):
        structural["decoded_top_level_keys"] = None
        structural["parser_stage"] = "json_decoding"
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    structural["decoded_top_level_keys"] = _bounded_key_names(parsed)
    structural["parser_stage"] = "schema_validation"
    return parsed


def _movie_schema_error_category(payload: dict[str, Any]) -> str | None:
    keys = set(payload)
    if keys - _MOVIE_IDENTIFICATION_KEYS:
        return "extra_fields"
    if _MOVIE_IDENTIFICATION_KEYS - keys:
        return "missing_fields"
    if payload["identified_title"] is not None and not isinstance(
        payload["identified_title"], str
    ):
        return "field_type"
    release_year = payload["release_year"]
    if release_year is not None and (
        not isinstance(release_year, int) or isinstance(release_year, bool)
    ):
        return "field_type"
    for name in ("identification_status", "confidence", "description", "evidence_summary"):
        if not isinstance(payload[name], str):
            return "field_type"
    for name in ("candidate_titles", "genres", "tags"):
        value = payload[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return "field_type"
    return None


def parse_suggestion_content_text(text: str) -> dict[str, Any]:
    """Parse one raw or fenced JSON suggestion object."""
    parsed = parse_json_object_content_text(text)
    if set(parsed.keys()) != _ALLOWED_SUGGESTION_KEYS:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return parsed


def parse_json_object_content_text(text: str) -> dict[str, Any]:
    """Parse one raw or fenced JSON object without schema-key validation."""
    candidate = text.strip()
    fence_match = _JSON_FENCE_PATTERN.fullmatch(candidate)
    if fence_match:
        candidate = fence_match.group("body").strip()
    else:
        candidate = _extract_single_json_object(candidate)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        ) from None
    if not isinstance(parsed, dict):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return parsed


def _extract_single_json_object(candidate: str) -> str:
    if candidate.startswith("{") and candidate.endswith("}"):
        return candidate
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    prefix = candidate[:start].strip()
    suffix = candidate[end + 1 :].strip()
    if "{" in candidate[start + 1 : end] or "}" in candidate[start + 1 : end]:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    if len(prefix) > 120 or len(suffix) > 120:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return candidate[start : end + 1]


def build_media_suggestion(
    parsed: dict[str, Any],
    *,
    required_extension: str,
    provider_id: str,
    model_id: str,
) -> MediaSuggestion:
    """Validate provider JSON and attach trusted FrameNest metadata."""
    try:
        return MediaSuggestion(
            title=parsed["title"],
            description=parsed["description"],
            collection=parsed["collection"],
            tags=tuple(parsed["tags"]),
            suggested_filename=validate_suggested_filename(
                parsed["suggested_filename"],
                required_extension=required_extension,
            ),
            confidence=parsed["confidence"],
            evidence=tuple(parsed["evidence"]),
            uncertainties=tuple(parsed["uncertainties"]),
            provider_id=provider_id,
            model_id=model_id,
            prompt_version=PROMPT_VERSION,
        )
    except (FrameNestMediaSuggestionError, TypeError, ValueError, KeyError):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        ) from None


def _response_status_code(response: object) -> int:
    status_code = getattr(response, "status_code", 200)
    if not isinstance(status_code, int):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return status_code


def _response_content_type(response: object) -> str:
    value = getattr(response, "content_type", None)
    if not isinstance(value, str) or not value:
        return "unknown"
    if len(value) > 100 or not all(
        character.isalnum() or character in "!#$&^_.+-/" for character in value
    ):
        return "other"
    return value.lower()


def _http_status_class(status_code: int) -> str:
    if 100 <= status_code <= 599:
        return f"{status_code // 100}xx"
    return "other"


def _response_body(response: object) -> bytes | bytearray:
    body = getattr(response, "body", response)
    if not isinstance(body, (bytes, bytearray)):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return body


def _decode_json_body(response: object) -> dict[str, Any]:
    body = _response_body(response)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return payload


def _extract_request_id(response: object) -> str:
    try:
        payload = _decode_json_body(response)
    except json.JSONDecodeError:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        ) from None
    if set(payload.keys()) != {"requestId"}:
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    request_id = payload["requestId"]
    if not isinstance(request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return request_id


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
    if message == TRANSPORT_UNAVAILABLE_MESSAGE:
        return MediaSuggestionProviderUnavailableError(SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE)
    if message == TRANSPORT_INVALID_RESPONSE_MESSAGE:
        return MediaSuggestionProviderInvalidResponseError(
            SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
        )
    return MediaSuggestionProviderFailedError(SUGGESTION_PROVIDER_FAILED_MESSAGE)


def _movie_request_diagnostics() -> dict[str, Any]:
    from kronika.domain.media_classification import (
        MOVIE_IDENTIFICATION_MAX_TOKENS,
        MOVIE_IDENTIFICATION_REASONING_BUDGET,
        MOVIE_IDENTIFICATION_TEMPERATURE,
        MOVIE_IDENTIFICATION_TOP_P,
    )

    return {
        "compatibility_mode": MOVIE_STRUCTURED_OUTPUT_COMPATIBILITY_MODE,
        "reasoning_enabled": True,
        "reasoning_budget": MOVIE_IDENTIFICATION_REASONING_BUDGET,
        "max_tokens": MOVIE_IDENTIFICATION_MAX_TOKENS,
        "temperature": MOVIE_IDENTIFICATION_TEMPERATURE,
        "top_p": MOVIE_IDENTIFICATION_TOP_P,
        "provider_submission_occurred": True,
        "initial_http_status_class": None,
        "http_status_class": None,
        "response_content_type": None,
        "response_lifecycle": None,
        "terminal_domain_error": None,
    }


def _emit_movie_response_diagnostics(
    facts: dict[str, Any],
    *,
    level: LogLevel,
    error_code: str | None,
) -> None:
    LOGGER.emit(
        level=level,
        event="movie_identification_response_classified",
        operation="identify_movie",
        error_code=error_code,
        retryable=False,
        context=facts,
    )


class NvidiaNimMediaSuggestionProvider:
    """NVIDIA NIM adapter for one media suggestion request."""

    def __init__(
        self,
        credential: NvidiaApiCredential,
        transport: JsonTransport | None = None,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        provider_id: str = DEFAULT_PROVIDER_ID,
        image_encoder: VlmImageDerivativeEncoder | None = None,
        pending_poll_interval_seconds: float = _PENDING_POLL_INTERVAL_SECONDS,
        pending_timeout_seconds: float = _PENDING_TIMEOUT_SECONDS,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._credential = credential
        self._transport = transport or HttpsJsonTransport(
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            max_response_bytes=MAX_RESPONSE_BODY_BYTES,
        )
        self._model_id = model_id
        self._provider_id = provider_id
        self._image_encoder = image_encoder or PillowVlmImageDerivativeEncoder()
        self._pending_poll_interval_seconds = pending_poll_interval_seconds
        self._pending_timeout_seconds = pending_timeout_seconds
        self._monotonic_clock = monotonic_clock
        self._sleep = sleep

    def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
        required_extension = _extension_from_basename(request.basename)
        try:
            body_dict = build_nvidia_request_body(
                request,
                model_id=self._model_id,
                image_encoder=self._image_encoder,
            )
            body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except FrameNestImageDerivativeError:
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None
        headers = {
            "Authorization": self._credential.authorization_header(),
            "Content-Type": "application/json",
        }
        try:
            response = self._transport.post_json(
                NVIDIA_CHAT_COMPLETIONS_URL,
                headers=headers,
                body=body,
                max_request_bytes=MAX_REQUEST_BODY_BYTES,
            )
            response = self._resolve_pending_response(
                response,
                headers={"Authorization": headers["Authorization"]},
            )
            payload = _decode_json_body(response)
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
        content_text = extract_message_content(payload)
        parsed = parse_suggestion_content_text(content_text)
        return build_media_suggestion(
            parsed,
            required_extension=required_extension,
            provider_id=self._provider_id,
            model_id=self._model_id,
        )

    def identify_movie(
        self,
        request: object,
    ) -> object:
        """Submit exactly one movie-identification request with reasoning enabled."""
        from kronika.application.movie_identification import (
            FrameNestMovieIdentificationError,
            parse_movie_identification_payload,
        )

        facts = _movie_request_diagnostics()
        try:
            body_dict = build_nvidia_movie_identification_body(
                request,
                model_id=self._model_id,
            )
            # Reject accidental path leakage into the provider JSON text fields.
            serialized = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
            if "/" in request.basename and request.basename in serialized:
                raise MediaSuggestionProviderInvalidResponseError(
                    SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
                )
            body = serialized.encode("utf-8")
        except FrameNestMovieIdentificationError:
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None
        headers = {
            "Authorization": self._credential.authorization_header(),
            "Content-Type": "application/json",
        }
        try:
            response = self._transport.post_json(
                NVIDIA_CHAT_COMPLETIONS_URL,
                headers=headers,
                body=body,
                max_request_bytes=MAX_REQUEST_BODY_BYTES,
            )
            initial_status = _response_status_code(response)
            facts["initial_http_status_class"] = _http_status_class(initial_status)
            facts["response_lifecycle"] = (
                "synchronous_200" if initial_status == 200 else "http_202_poll"
            )
            response = self._resolve_pending_response(
                response,
                headers={"Authorization": headers["Authorization"]},
            )
            final_status = _response_status_code(response)
            facts["http_status_class"] = _http_status_class(final_status)
            facts["response_content_type"] = _response_content_type(response)
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

        try:
            payload = _decode_json_body(response)
        except (json.JSONDecodeError, UnicodeError, MediaSuggestionProviderInvalidResponseError):
            facts.update(
                {
                    "parser_stage": "provider_envelope_json",
                    "json_decode_stage": "provider_envelope_failed",
                    "terminal_domain_error": "PROVIDER_INVALID_RESPONSE",
                }
            )
            _emit_movie_response_diagnostics(
                facts,
                level="WARNING",
                error_code="PROVIDER_INVALID_RESPONSE",
            )
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None

        facts.update(classify_chat_completion_choice(payload))
        try:
            content_text = extract_movie_message_content(payload, facts=facts)
            parsed = parse_movie_json_object_content_text(content_text, facts=facts)
            schema_error = _movie_schema_error_category(parsed)
            if schema_error is not None:
                facts["schema_validation_stage"] = "failed"
                facts["schema_error_category"] = schema_error
                raise MediaSuggestionProviderInvalidResponseError(
                    SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
                )
            suggestion = parse_movie_identification_payload(
                parsed,
                provider_id=self._provider_id,
                model_id=self._model_id,
                derivative_count=1,
            )
        except MediaSuggestionProviderTruncatedResponseError:
            facts["terminal_domain_error"] = "PROVIDER_RESPONSE_TRUNCATED"
            _emit_movie_response_diagnostics(
                facts,
                level="WARNING",
                error_code="PROVIDER_RESPONSE_TRUNCATED",
            )
            raise
        except MediaSuggestionProviderEmptyResponseError:
            facts["terminal_domain_error"] = "PROVIDER_RESPONSE_EMPTY"
            _emit_movie_response_diagnostics(
                facts,
                level="WARNING",
                error_code="PROVIDER_RESPONSE_EMPTY",
            )
            raise
        except MediaSuggestionProviderRefusalError:
            facts["terminal_domain_error"] = "PROVIDER_REFUSAL"
            _emit_movie_response_diagnostics(
                facts,
                level="WARNING",
                error_code="PROVIDER_REFUSAL",
            )
            raise
        except MediaSuggestionProviderInvalidResponseError:
            if facts.get("schema_validation_stage") == "not_started":
                facts["schema_validation_stage"] = "not_reached"
            facts["terminal_domain_error"] = "PROVIDER_INVALID_RESPONSE"
            _emit_movie_response_diagnostics(
                facts,
                level="WARNING",
                error_code="PROVIDER_INVALID_RESPONSE",
            )
            raise
        except FrameNestMovieIdentificationError:
            facts["schema_validation_stage"] = "failed"
            facts["schema_error_category"] = "domain_constraint"
            facts["terminal_domain_error"] = "PROVIDER_INVALID_RESPONSE"
            _emit_movie_response_diagnostics(
                facts,
                level="WARNING",
                error_code="PROVIDER_INVALID_RESPONSE",
            )
            raise MediaSuggestionProviderInvalidResponseError(
                SUGGESTION_PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None
        facts["schema_validation_stage"] = "succeeded"
        facts["schema_error_category"] = None
        facts["parser_stage"] = "completed"
        facts["terminal_domain_error"] = None
        _emit_movie_response_diagnostics(
            facts,
            level="INFO",
            error_code=None,
        )
        return suggestion

    def test_connection(self) -> None:
        body_dict = build_nvidia_connection_test_body(model_id=self._model_id)
        body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": self._credential.authorization_header(),
            "Content-Type": "application/json",
        }
        try:
            response = self._transport.post_json(
                NVIDIA_CHAT_COMPLETIONS_URL,
                headers=headers,
                body=body,
                max_request_bytes=MAX_REQUEST_BODY_BYTES,
            )
            response = self._resolve_pending_response(
                response,
                headers={"Authorization": headers["Authorization"]},
            )
            payload = _decode_json_body(response)
            extract_message_content(payload)
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

    def probe_vision(self, *, prompt: str, image_png: bytes) -> str:
        """Submit exactly one bounded single-image vision probe request."""
        from kronika.infrastructure.ai.openai_chat_completions import (
            build_chat_completions_vision_probe_body,
        )

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
        headers = {
            "Authorization": self._credential.authorization_header(),
            "Content-Type": "application/json",
        }
        try:
            response = self._transport.post_json(
                NVIDIA_CHAT_COMPLETIONS_URL,
                headers=headers,
                body=body,
                max_request_bytes=MAX_REQUEST_BODY_BYTES,
            )
            response = self._resolve_pending_response(
                response,
                headers={"Authorization": headers["Authorization"]},
            )
            payload = _decode_json_body(response)
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
        return extract_message_content(payload)

    def _resolve_pending_response(
        self,
        response: object,
        *,
        headers: dict[str, str],
    ) -> object:
        status_code = _response_status_code(response)
        if status_code == 200:
            return response
        if status_code != 202:
            raise _status_error(status_code)

        request_id = _extract_request_id(response)
        status_url = _NVIDIA_STATUS_URL_PREFIX + request_id
        deadline = self._monotonic_clock() + self._pending_timeout_seconds
        while True:
            if self._monotonic_clock() >= deadline:
                raise MediaSuggestionProviderPendingTimeoutError(
                    SUGGESTION_PROVIDER_PENDING_TIMEOUT_MESSAGE
                )
            self._sleep(self._pending_poll_interval_seconds)
            status_response = self._transport.get_json(status_url, headers=headers)
            status_code = _response_status_code(status_response)
            if status_code == 200:
                return status_response
            if status_code == 202:
                continue
            raise _status_error(status_code)

    def __repr__(self) -> str:
        return "NvidiaNimMediaSuggestionProvider(<redacted>)"
