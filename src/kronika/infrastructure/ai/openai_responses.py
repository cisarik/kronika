"""OpenAI Responses adapter for the provider-neutral research port.

The adapter is transport-injectable and performs no network access at import
or construction time. It builds bounded request bodies from the server-selected
profile only, maps provider outcomes to the stable research error codes, and
never includes provider bodies, credentials, or reasoning text in errors.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from kronika.application.ports.research import ResearchProvider
from kronika.domain.research import (
    CleanupOutcome,
    CompletionEvidence,
    ProviderDescriptor,
    ProviderHandle,
    ProviderObservation,
    ProviderObservationKind,
    ProviderRequest,
    ResearchAnswer,
    ResearchCitation,
    ResearchErrorCode,
    ResearchRemoteCleanupState,
    ResearchUsage,
    ResearchValueError,
    UsagePriceSchedule,
    completion_error,
)
from kronika.infrastructure.ai.research_registry import (
    research_provider_descriptor,
)
from kronika.infrastructure.ai.transport import (
    TRANSPORT_AUTH_REJECTED_MESSAGE,
    TRANSPORT_INVALID_RESPONSE_MESSAGE,
    TRANSPORT_MODEL_UNAVAILABLE_MESSAGE,
    TRANSPORT_RATE_LIMITED_MESSAGE,
    TRANSPORT_REQUEST_TOO_LARGE_MESSAGE,
    TRANSPORT_RESPONSE_TOO_LARGE_MESSAGE,
    TRANSPORT_UNAVAILABLE_MESSAGE,
    HttpsJsonResponse,
    HttpsTransportError,
)

DEFAULT_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
_REQUEST_OVERHEAD_BYTES = 4096


def _openai_responses_price_schedule_2026_09_26() -> UsagePriceSchedule:
    """Documented OpenAI Responses usage prices for model gpt-5.5-2026-04-23.

    Source: accepted kronika-one-product planning report, section 3, rates
    retrieved 2026-09-26. Input is USD 5 per million tokens, cached input is
    USD 0.50 per million, output is USD 30 per million, and web search is
    USD 10 per 1,000 calls. The values below are integer micro-USD. Revalidate
    these rates before any live provider activation.
    """

    return UsagePriceSchedule(
        input_micro_usd_per_million=5_000_000,
        cached_input_micro_usd_per_million=500_000,
        output_micro_usd_per_million=30_000_000,
        web_search_micro_usd_per_thousand=10_000_000,
    )


OPENAI_RESPONSES_PRICE_SCHEDULE_2026_09_26 = (
    _openai_responses_price_schedule_2026_09_26()
)


@runtime_checkable
class ResearchJsonTransport(Protocol):
    """Bounded JSON transport seam. Tests inject a fake implementation."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> HttpsJsonResponse: ...

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> HttpsJsonResponse: ...

    def delete_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> HttpsJsonResponse: ...


def _transport_error_code(exc: HttpsTransportError) -> ResearchErrorCode:
    message = str(exc)
    if message == TRANSPORT_AUTH_REJECTED_MESSAGE:
        return ResearchErrorCode.AUTH
    if message == TRANSPORT_RATE_LIMITED_MESSAGE:
        return ResearchErrorCode.RATE_LIMIT
    if message == TRANSPORT_MODEL_UNAVAILABLE_MESSAGE:
        return ResearchErrorCode.RESULT_EXPIRED
    if message in (TRANSPORT_RESPONSE_TOO_LARGE_MESSAGE,):
        return ResearchErrorCode.RESULT_TOO_LARGE
    if message in (TRANSPORT_REQUEST_TOO_LARGE_MESSAGE, TRANSPORT_INVALID_RESPONSE_MESSAGE):
        return ResearchErrorCode.INVALID_REQUEST
    if message == TRANSPORT_UNAVAILABLE_MESSAGE:
        return ResearchErrorCode.PROVIDER_UNAVAILABLE
    return ResearchErrorCode.PROVIDER_UNAVAILABLE


def _status_error_code(status_code: int) -> ResearchErrorCode:
    if status_code in {401, 403}:
        return ResearchErrorCode.AUTH
    if status_code == 429:
        return ResearchErrorCode.RATE_LIMIT
    if status_code == 404:
        return ResearchErrorCode.RESULT_EXPIRED
    if status_code >= 500:
        return ResearchErrorCode.PROVIDER_UNAVAILABLE
    return ResearchErrorCode.INVALID_REQUEST


def _submit_status_error_code(status_code: int) -> ResearchErrorCode:
    """Map a creation failure without masquerading as a missing response.

    A 404 during creation is provider-side unavailability, not an expired
    remote result. Only polling may interpret 404 as a released response.
    """
    if status_code == 404:
        return ResearchErrorCode.PROVIDER_UNAVAILABLE
    return _status_error_code(status_code)


def _decode_payload(response: HttpsJsonResponse) -> Mapping[str, object] | None:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


class OpenAIResponsesAdapter:
    """First research provider adapter. Disabled by default at composition."""

    def __init__(
        self,
        *,
        transport: ResearchJsonTransport,
        api_key_supplier: Callable[[], str | None],
        endpoint: str = DEFAULT_RESPONSES_ENDPOINT,
    ) -> None:
        self._transport = transport
        self._api_key_supplier = api_key_supplier
        self._endpoint = endpoint

    def describe(self) -> ProviderDescriptor:
        return research_provider_descriptor("openai-responses")

    # -- submission --------------------------------------------------------

    def submit(self, request: ProviderRequest) -> ProviderObservation:
        body = self._submit_body(request)
        headers = self._auth_headers()
        if headers is None:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.NOT_CONFIGURED,
            )
        try:
            response = self._transport.post_json(
                self._endpoint,
                headers=headers,
                body=body,
                max_request_bytes=(
                    request.resource_limits.prompt_max_utf8_bytes
                    + _REQUEST_OVERHEAD_BYTES
                ),
            )
        except HttpsTransportError as exc:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=_transport_error_code(exc),
            )
        if response.status_code not in {200, 201, 202}:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=_submit_status_error_code(response.status_code),
            )
        payload = _decode_payload(response)
        if payload is None:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.INVALID_RESULT,
            )
        handle_value = payload.get("id")
        if not isinstance(handle_value, str) or not handle_value:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.INVALID_RESULT,
            )
        status = payload.get("status")
        if status in {"queued", "in_progress", "completed"}:
            return ProviderObservation(
                kind=ProviderObservationKind.RUNNING,
                remote_handle=ProviderHandle(handle_value),
            )
        return ProviderObservation(
            kind=ProviderObservationKind.FAILED,
            error_code=ResearchErrorCode.PROVIDER_UNAVAILABLE,
        )

    def _submit_body(self, request: ProviderRequest) -> bytes:
        profile = request.profile
        body = {
            "model": profile.model_id,
            "input": request.prompt,
            "tools": [{"type": tool} for tool in profile.tool_allowlist],
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "max_tool_calls": profile.max_tool_calls,
            "max_output_tokens": profile.max_output_tokens,
            "reasoning": {"effort": profile.reasoning_effort},
            "background": profile.background,
            "store": True,
        }
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

    # -- polling -----------------------------------------------------------

    def poll(self, handle: ProviderHandle) -> ProviderObservation:
        headers = self._auth_headers()
        if headers is None:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.NOT_CONFIGURED,
            )
        try:
            response = self._transport.get_json(
                f"{self._endpoint}/{handle.value}",
                headers=headers,
            )
        except HttpsTransportError as exc:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=_transport_error_code(exc),
            )
        if response.status_code != 200:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=_status_error_code(response.status_code),
            )
        payload = _decode_payload(response)
        if payload is None:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.INVALID_RESULT,
            )
        status = payload.get("status")
        if status in {"queued", "in_progress"}:
            return ProviderObservation(
                kind=ProviderObservationKind.RUNNING,
                remote_handle=handle,
            )
        if status == "cancelled":
            return ProviderObservation(
                kind=ProviderObservationKind.CANCELLED,
                error_code=ResearchErrorCode.CANCELLED,
            )
        if status == "failed":
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.PROVIDER_UNAVAILABLE,
            )
        if status != "completed":
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.INVALID_RESULT,
            )
        try:
            answer = _parse_answer(payload)
        except ResearchValueError:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.INVALID_RESULT,
            )
        error = completion_error(answer.evidence)
        if error is not None:
            kind = (
                ProviderObservationKind.REFUSED
                if error is ResearchErrorCode.REFUSED
                else ProviderObservationKind.FAILED
            )
            return ProviderObservation(kind=kind, error_code=error)
        return ProviderObservation(
            kind=ProviderObservationKind.COMPLETE,
            remote_handle=handle,
            answer=answer,
        )

    # -- cancellation and remote release ----------------------------------

    def cancel(self, handle: ProviderHandle) -> ProviderObservation:
        headers = self._auth_headers()
        if headers is None:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=ResearchErrorCode.NOT_CONFIGURED,
            )
        try:
            response = self._transport.post_json(
                f"{self._endpoint}/{handle.value}/cancel",
                headers=headers,
                body=b"{}",
                max_request_bytes=1024,
            )
        except HttpsTransportError as exc:
            return ProviderObservation(
                kind=ProviderObservationKind.FAILED,
                error_code=_transport_error_code(exc),
            )
        if response.status_code in {200, 201, 202}:
            return ProviderObservation(
                kind=ProviderObservationKind.CANCELLED,
                error_code=ResearchErrorCode.CANCELLED,
            )
        return ProviderObservation(
            kind=ProviderObservationKind.FAILED,
            error_code=_status_error_code(response.status_code),
        )

    def release_remote(self, handle: ProviderHandle) -> CleanupOutcome:
        headers = self._auth_headers()
        if headers is None:
            return CleanupOutcome(
                state=ResearchRemoteCleanupState.FAILED,
                error_code=ResearchErrorCode.NOT_CONFIGURED,
            )
        try:
            response = self._transport.delete_json(
                f"{self._endpoint}/{handle.value}",
                headers=headers,
            )
        except HttpsTransportError as exc:
            error_code = _transport_error_code(exc)
            if error_code is ResearchErrorCode.RESULT_EXPIRED:
                # A missing remote response is already released.
                return CleanupOutcome(state=ResearchRemoteCleanupState.DELETED)
            return CleanupOutcome(
                state=ResearchRemoteCleanupState.FAILED,
                error_code=error_code,
            )
        if response.status_code in {200, 202, 204}:
            return CleanupOutcome(state=ResearchRemoteCleanupState.DELETED)
        if response.status_code == 404:
            return CleanupOutcome(state=ResearchRemoteCleanupState.DELETED)
        return CleanupOutcome(
            state=ResearchRemoteCleanupState.FAILED,
            error_code=_status_error_code(response.status_code),
        )

    # -- credentials -------------------------------------------------------

    def _auth_headers(self) -> dict[str, str] | None:
        try:
            api_key = self._api_key_supplier()
        except Exception:
            return None
        if not isinstance(api_key, str) or not api_key:
            return None
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }


def _parse_answer(payload: Mapping[str, object]) -> ResearchAnswer:
    text_parts: list[str] = []
    citations: list[ResearchCitation] = []
    refusal = False
    web_search_calls = 0
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "web_search_call":
                web_search_calls += 1
            if item_type != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "refusal":
                    refusal = True
                if part_type != "output_text":
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
                annotations = part.get("annotations")
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") != "url_citation":
                        continue
                    url = annotation.get("url")
                    title = annotation.get("title")
                    if isinstance(url, str) and isinstance(title, str):
                        citations.append(ResearchCitation(url=url, title=title))
    text = "".join(text_parts)
    incomplete = bool(payload.get("incomplete_details"))
    usage = _parse_usage(payload, web_search_calls=web_search_calls)
    return ResearchAnswer(
        text=text,
        citations=tuple(citations),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=bool(text.strip()),
            web_search_executed=web_search_calls > 0,
            refusal_marker=refusal,
            incomplete_marker=incomplete,
        ),
        usage=usage,
    )


def _parse_usage(
    payload: Mapping[str, object],
    *,
    web_search_calls: int,
) -> ResearchUsage | None:
    """Parse provider usage without inventing zeros.

    ``input_tokens`` and ``output_tokens`` are required. A missing or invalid
    required value, or an invalid optional value, makes accounting unknown
    (``None``) instead of visible-as-zero. Cache-write tokens are retained only
    when the provider reports them; absence stays absence.
    """
    usage_payload = payload.get("usage")
    if not isinstance(usage_payload, dict):
        return None
    input_tokens = _non_negative_int(usage_payload.get("input_tokens"))
    output_tokens = _non_negative_int(usage_payload.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    input_details = usage_payload.get("input_tokens_details")
    if input_details is None:
        input_details = {}
    elif not isinstance(input_details, dict):
        return None
    output_details = usage_payload.get("output_tokens_details")
    if output_details is None:
        output_details = {}
    elif not isinstance(output_details, dict):
        return None
    cached = _optional_non_negative_int(input_details.get("cached_tokens"))
    reasoning = _optional_non_negative_int(output_details.get("reasoning_tokens"))
    cache_write = _optional_non_negative_int(input_details.get("cache_write_tokens"))
    if cached is False or reasoning is False or cache_write is False:
        return None
    cached_tokens = 0 if cached is None else cached
    reasoning_tokens = 0 if reasoning is None else reasoning
    cache_write_tokens = None if cache_write is None else cache_write
    try:
        return ResearchUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_tool_calls=web_search_calls,
            cache_write_input_tokens=cache_write_tokens,
        )
    except ResearchValueError:
        return None


def _non_negative_int(value: object) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _optional_non_negative_int(value: object) -> int | None | bool:
    """Return an int, ``None`` when absent, or ``False`` when invalid."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        return False
    return value


__all__ = [
    "DEFAULT_RESPONSES_ENDPOINT",
    "OPENAI_RESPONSES_PRICE_SCHEDULE_2026_09_26",
    "OpenAIResponsesAdapter",
    "ResearchJsonTransport",
]
