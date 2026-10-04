"""OpenAI Responses adapter evidence with a fake bounded transport."""

from __future__ import annotations

import json

import pytest

from kronika.application.ports.research import ResearchProvider
from kronika.domain.research import (
    ApprovedResourceLimits,
    FIXED_OPENAI_RESPONSES_MODEL_ID,
    OPENAI_RESPONSES_PROVIDER_ID,
    ProviderObservationKind,
    ProviderRequest,
    ResearchErrorCode,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ServerSelectedProfile,
    WEB_SEARCH_TOOL,
)
from kronika.infrastructure.ai.openai_responses import (
    DEFAULT_RESPONSES_ENDPOINT,
    OPENAI_RESPONSES_PRICE_SCHEDULE_2026_09_26,
    OpenAIResponsesAdapter,
)
from kronika.infrastructure.ai.transport import (
    TRANSPORT_AUTH_REJECTED_MESSAGE,
    TRANSPORT_MODEL_UNAVAILABLE_MESSAGE,
    TRANSPORT_UNAVAILABLE_MESSAGE,
    HttpsJsonResponse,
    HttpsTransportError,
)
from kronika.domain.research import ProviderHandle


class FakeTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict, bytes, int]] = []
        self.gets: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, dict]] = []
        self.post_response: HttpsJsonResponse | Exception | None = None
        self.get_response: HttpsJsonResponse | Exception | None = None
        self.delete_response: HttpsJsonResponse | Exception | None = None

    def post_json(self, url, *, headers, body, max_request_bytes):
        self.posts.append((url, dict(headers), body, max_request_bytes))
        assert self.post_response is not None
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response

    def get_json(self, url, *, headers):
        self.gets.append((url, dict(headers)))
        assert self.get_response is not None
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response

    def delete_json(self, url, *, headers):
        self.deletes.append((url, dict(headers)))
        assert self.delete_response is not None
        if isinstance(self.delete_response, Exception):
            raise self.delete_response
        return self.delete_response


def _json_response(status_code: int, payload: dict) -> HttpsJsonResponse:
    return HttpsJsonResponse(
        status_code=status_code,
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


def _request() -> ProviderRequest:
    return ProviderRequest(
        operation_id="op-adapter-0001",
        kind=ResearchOperationKind.RESEARCH,
        prompt="What is the synthetic question?",
        profile=ServerSelectedProfile(
            provider_id=OPENAI_RESPONSES_PROVIDER_ID,
            model_id=FIXED_OPENAI_RESPONSES_MODEL_ID,
            configuration_version="3",
            reasoning_effort="high",
            tool_allowlist=(WEB_SEARCH_TOOL,),
            background=True,
            max_tool_calls=20,
            max_output_tokens=32768,
            deadline_seconds=1800,
            budget_reservation_usd_micros=5_000_000,
        ),
        deadline_seconds=1800,
        resource_limits=ApprovedResourceLimits(
            max_tool_calls=20,
            max_output_tokens=32768,
            budget_reservation_usd_micros=5_000_000,
            prompt_max_utf8_bytes=16_384,
            answer_max_utf8_bytes=2_097_152,
            citation_count_max=200,
        ),
    )


def _adapter(transport: FakeTransport, *, api_key: str | None = "test-key"):
    return OpenAIResponsesAdapter(
        transport=transport,
        api_key_supplier=lambda: api_key,
    )


def _completed_payload() -> dict:
    return {
        "id": "resp-123",
        "status": "completed",
        "output": [
            {"type": "web_search_call", "id": "ws-1"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Synthetic research answer.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.invalid/source",
                                "title": "Source",
                            }
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 1_200,
            "output_tokens": 640,
            "input_tokens_details": {"cached_tokens": 200},
            "output_tokens_details": {"reasoning_tokens": 140},
        },
    }


def test_documented_price_schedule_pins_the_2026_09_26_rates() -> None:
    schedule = OPENAI_RESPONSES_PRICE_SCHEDULE_2026_09_26
    assert schedule.input_micro_usd_per_million == 5_000_000
    assert schedule.cached_input_micro_usd_per_million == 500_000
    assert schedule.output_micro_usd_per_million == 30_000_000
    assert schedule.web_search_micro_usd_per_thousand == 10_000_000


def test_describe_is_network_free_and_port_conformant() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)
    descriptor = adapter.describe()
    assert descriptor.provider_id == OPENAI_RESPONSES_PROVIDER_ID
    assert transport.posts == [] and transport.gets == [] and transport.deletes == []
    assert isinstance(adapter, ResearchProvider)


def test_submit_builds_a_bounded_server_selected_body() -> None:
    transport = FakeTransport()
    transport.post_response = _json_response(200, {"id": "resp-123", "status": "queued"})
    adapter = _adapter(transport)
    observation = adapter.submit(_request())
    assert observation.kind is ProviderObservationKind.RUNNING
    assert observation.remote_handle is not None
    assert observation.remote_handle.value == "resp-123"
    url, headers, body, max_bytes = transport.posts[0]
    assert url == DEFAULT_RESPONSES_ENDPOINT
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"
    assert max_bytes == 16_384 + 4096
    payload = json.loads(body)
    assert payload["model"] == FIXED_OPENAI_RESPONSES_MODEL_ID
    assert payload["input"] == "What is the synthetic question?"
    assert payload["tools"] == [{"type": "web_search"}]
    assert payload["tool_choice"] == "required"
    assert payload["parallel_tool_calls"] is False
    assert payload["max_tool_calls"] == 20
    assert payload["max_output_tokens"] == 32768
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["background"] is True
    assert payload["store"] is True
    # No client endpoint, model, or tool fields can enter the body.
    assert "endpoint" not in payload and "tool" not in payload


def test_missing_credential_is_not_configured_without_network() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport, api_key=None)
    observation = adapter.submit(_request())
    assert observation.kind is ProviderObservationKind.FAILED
    assert observation.error_code is ResearchErrorCode.NOT_CONFIGURED
    assert transport.posts == []


def test_poll_completed_parses_answer_evidence_and_usage() -> None:
    transport = FakeTransport()
    transport.get_response = _json_response(200, _completed_payload())
    adapter = _adapter(transport)
    observation = adapter.poll(ProviderHandle("resp-123"))
    assert observation.kind is ProviderObservationKind.COMPLETE
    assert observation.answer is not None
    assert observation.answer.text == "Synthetic research answer."
    assert observation.answer.citations[0].url == "https://example.invalid/source"
    assert observation.answer.usage.input_tokens == 1_200
    assert observation.answer.usage.cached_input_tokens == 200
    assert observation.answer.usage.output_tokens == 640
    assert observation.answer.usage.reasoning_tokens == 140
    assert observation.answer.usage.web_tool_calls == 1
    assert observation.answer.evidence.web_search_executed is True
    assert transport.gets[0][0] == f"{DEFAULT_RESPONSES_ENDPOINT}/resp-123"


def test_poll_completed_without_web_search_is_no_web_evidence() -> None:
    payload = _completed_payload()
    payload["output"] = [
        item for item in payload["output"] if item.get("type") != "web_search_call"
    ]
    transport = FakeTransport()
    transport.get_response = _json_response(200, payload)
    observation = _adapter(transport).poll(ProviderHandle("resp-123"))
    assert observation.kind is ProviderObservationKind.FAILED
    assert observation.error_code is ResearchErrorCode.NO_WEB_EVIDENCE


def test_poll_missing_response_and_transport_failure_map_to_codes() -> None:
    transport = FakeTransport()
    transport.get_response = HttpsTransportError(TRANSPORT_MODEL_UNAVAILABLE_MESSAGE)
    missing = _adapter(transport).poll(ProviderHandle("resp-404"))
    assert missing.error_code is ResearchErrorCode.RESULT_EXPIRED
    transport.get_response = HttpsTransportError(TRANSPORT_UNAVAILABLE_MESSAGE)
    unavailable = _adapter(transport).poll(ProviderHandle("resp-500"))
    assert unavailable.error_code is ResearchErrorCode.PROVIDER_UNAVAILABLE
    transport.get_response = HttpsTransportError(TRANSPORT_AUTH_REJECTED_MESSAGE)
    unauthorized = _adapter(transport).poll(ProviderHandle("resp-401"))
    assert unauthorized.error_code is ResearchErrorCode.AUTH


def test_cancel_and_remote_release_outcomes() -> None:
    transport = FakeTransport()
    transport.post_response = _json_response(200, {"id": "resp-123", "status": "cancelled"})
    cancelled = _adapter(transport).cancel(ProviderHandle("resp-123"))
    assert cancelled.kind is ProviderObservationKind.CANCELLED
    assert cancelled.error_code is ResearchErrorCode.CANCELLED
    assert transport.posts[0][0] == f"{DEFAULT_RESPONSES_ENDPOINT}/resp-123/cancel"
    transport.delete_response = HttpsJsonResponse(status_code=204, body=b"")
    assert (
        _adapter(transport).release_remote(ProviderHandle("resp-123")).state
        is ResearchRemoteCleanupState.DELETED
    )
    transport.delete_response = HttpsTransportError(TRANSPORT_MODEL_UNAVAILABLE_MESSAGE)
    assert (
        _adapter(transport).release_remote(ProviderHandle("resp-404")).state
        is ResearchRemoteCleanupState.DELETED
    )
    transport.delete_response = _json_response(500, {"error": "x"})
    failed = _adapter(transport).release_remote(ProviderHandle("resp-500"))
    assert failed.state is ResearchRemoteCleanupState.FAILED
    assert failed.error_code is ResearchErrorCode.PROVIDER_UNAVAILABLE


def _poll(usage_payload: object) -> ProviderObservation:
    payload = _completed_payload()
    if usage_payload is _MISSING:
        payload.pop("usage")
    else:
        payload["usage"] = usage_payload
    transport = FakeTransport()
    transport.get_response = _json_response(200, payload)
    return _adapter(transport).poll(ProviderHandle("resp-123"))


class _Missing:
    pass


_MISSING = _Missing()


def test_reported_cache_write_tokens_parse_into_cache_write_input_tokens() -> None:
    observation = _poll(
        {
            "input_tokens": 1_000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
            "output_tokens_details": {"reasoning_tokens": 10},
        }
    )
    assert observation.kind is ProviderObservationKind.COMPLETE
    usage = observation.answer.usage
    assert usage is not None
    assert usage.cache_write_input_tokens == 300
    assert usage.input_tokens == 1_000
    assert usage.cached_input_tokens == 200
    # The partition stays internally consistent: cached + written <= input.
    assert usage.cached_input_tokens + usage.cache_write_input_tokens <= usage.input_tokens


def test_absent_cache_write_detail_stays_absent_rather_than_zero() -> None:
    observation = _poll(
        {
            "input_tokens": 1_000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 200},
        }
    )
    assert observation.kind is ProviderObservationKind.COMPLETE
    assert observation.answer.usage.cache_write_input_tokens is None


def test_missing_usage_is_unknown_not_a_zeroed_observation() -> None:
    observation = _poll(_MISSING)
    assert observation.kind is ProviderObservationKind.COMPLETE
    # A complete answer may carry no trustworthy accounting.
    assert observation.answer.usage is None


@pytest.mark.parametrize(
    "usage_payload",
    [
        {},
        {"input_tokens": "1000", "output_tokens": 100},
        {"input_tokens": 1_000},
        {"input_tokens": -1, "output_tokens": 100},
        {"input_tokens": True, "output_tokens": 100},
        {"input_tokens": 1_000, "output_tokens": None},
        {"input_tokens": 1_000, "output_tokens": 100, "input_tokens_details": "no"},
        {"input_tokens": 1_000, "output_tokens": 100, "output_tokens_details": []},
        {
            "input_tokens": 1_000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": -5},
        },
        {
            "input_tokens": 1_000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 2_000},
        },
        {
            "input_tokens": 1_000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 400},
        },
    ],
)
def test_missing_or_invalid_usage_never_becomes_zero(usage_payload: dict) -> None:
    observation = _poll(usage_payload)
    assert observation.kind is ProviderObservationKind.COMPLETE
    assert observation.answer.usage is None


def test_submit_404_is_provider_unavailable_while_poll_404_is_expired() -> None:
    submit_transport = FakeTransport()
    submit_transport.post_response = _json_response(404, {"error": "not found"})
    submitted = _adapter(submit_transport).submit(_request())
    assert submitted.kind is ProviderObservationKind.FAILED
    assert submitted.error_code is ResearchErrorCode.PROVIDER_UNAVAILABLE
    assert submitted.error_code is not ResearchErrorCode.RESULT_EXPIRED

    poll_transport = FakeTransport()
    poll_transport.get_response = _json_response(404, {"error": "not found"})
    polled = _adapter(poll_transport).poll(ProviderHandle("resp-404"))
    assert polled.kind is ProviderObservationKind.FAILED
    assert polled.error_code is ResearchErrorCode.RESULT_EXPIRED
