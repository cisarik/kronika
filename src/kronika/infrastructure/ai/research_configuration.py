"""Non-secret research configuration for AI schema version 3.

An absent section means research is disabled. This module stores a credential
identifier name only. It does not store an endpoint, a secret, or a client
tool choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kronika.domain.research import (
    ALLOWED_REASONING_EFFORTS,
    MAX_ANSWER_UTF8_BYTES,
    MAX_CITATION_COUNT,
    MAX_DAILY_BUDGET_MICRO_USD,
    MAX_MONTHLY_BUDGET_MICRO_USD,
    MAX_PROMPT_UTF8_BYTES,
    MAX_PROVIDER_RESPONSE_BYTES,
    MAX_RESEARCH_DEADLINE_SECONDS,
    MAX_RESEARCH_OUTPUT_TOKENS,
    MAX_RESEARCH_RESERVATION_MICRO_USD,
    MAX_RESEARCH_TOOL_CALLS,
    MAX_SEARCH_DEADLINE_SECONDS,
    MAX_SEARCH_OUTPUT_TOKENS,
    MAX_SEARCH_RESERVATION_MICRO_USD,
    MAX_SEARCH_TOOL_CALLS,
    OPENAI_RESPONSES_PROVIDER_ID,
    WEB_SEARCH_TOOL,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderRecordError,
    validate_credential_environment_name,
    validate_model_identifier,
    validate_provider_identifier,
)
from kronika.infrastructure.ai.research_models import (
    DEFAULT_RESEARCH_MODEL_ID,
    is_known_selectable_model,
)

RESEARCH_CREDENTIAL_IDENTIFIER = "KRONIKA_RESEARCH_OPENAI_API_KEY"
MAX_CONNECT_TIMEOUT_SECONDS = 30
MAX_HTTP_OPERATION_TIMEOUT_SECONDS = 120
MAX_POLL_INTERVAL_SECONDS = 60

_OPERATION_KEYS = frozenset(
    {
        "reasoning_effort",
        "tool_allowlist",
        "max_tool_calls",
        "max_output_tokens",
        "deadline_seconds",
        "budget_reservation_usd_micros",
    }
)
_SECTION_KEYS = frozenset(
    {
        "enabled",
        "provider_id",
        "model_id",
        "background",
        "credential_identifier",
        "search",
        "research",
        "daily_budget_usd_micros",
        "monthly_budget_usd_micros",
        "prompt_max_utf8_bytes",
        "provider_response_max_bytes",
        "answer_max_utf8_bytes",
        "citation_count_max",
        "connect_timeout_seconds",
        "http_operation_timeout_seconds",
        "poll_interval_seconds",
    }
)


class ResearchConfigurationError(ValueError):
    """Sanitized research-configuration failure. It never echoes a value."""

    def __init__(self) -> None:
        super().__init__("AI research configuration is malformed.")


@dataclass(frozen=True, slots=True)
class ResearchOperationSettings:
    """Server limits for one search or research operation."""

    reasoning_effort: str
    tool_allowlist: tuple[str, ...]
    max_tool_calls: int
    max_output_tokens: int
    deadline_seconds: int
    budget_reservation_usd_micros: int

    def __post_init__(self) -> None:
        if self.reasoning_effort not in ALLOWED_REASONING_EFFORTS:
            raise ResearchConfigurationError
        if self.tool_allowlist != (WEB_SEARCH_TOOL,):
            raise ResearchConfigurationError
        _require_int(self.max_tool_calls, minimum=1, maximum=MAX_RESEARCH_TOOL_CALLS)
        _require_int(self.max_output_tokens, minimum=1, maximum=MAX_RESEARCH_OUTPUT_TOKENS)
        _require_int(self.deadline_seconds, minimum=1, maximum=MAX_RESEARCH_DEADLINE_SECONDS)
        _require_int(
            self.budget_reservation_usd_micros,
            minimum=1,
            maximum=MAX_RESEARCH_RESERVATION_MICRO_USD,
        )


@dataclass(frozen=True, slots=True)
class ResearchConfiguration:
    """Optional non-secret research section. Enabled defaults to false."""

    enabled: bool
    provider_id: str
    model_id: str
    background: bool
    credential_identifier: str
    search: ResearchOperationSettings
    research: ResearchOperationSettings
    daily_budget_usd_micros: int
    monthly_budget_usd_micros: int
    prompt_max_utf8_bytes: int
    provider_response_max_bytes: int
    answer_max_utf8_bytes: int
    citation_count_max: int
    connect_timeout_seconds: int
    http_operation_timeout_seconds: int
    poll_interval_seconds: int

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.background) is not bool:
            raise ResearchConfigurationError
        if self.provider_id != OPENAI_RESPONSES_PROVIDER_ID:
            raise ResearchConfigurationError
        if not is_known_selectable_model(self.model_id):
            raise ResearchConfigurationError
        if not isinstance(self.search, ResearchOperationSettings):
            raise ResearchConfigurationError
        if not isinstance(self.research, ResearchOperationSettings):
            raise ResearchConfigurationError
        _require_operation_ceiling(
            self.search,
            max_tool_calls=MAX_SEARCH_TOOL_CALLS,
            max_output_tokens=MAX_SEARCH_OUTPUT_TOKENS,
            max_deadline_seconds=MAX_SEARCH_DEADLINE_SECONDS,
            max_reservation_usd_micros=MAX_SEARCH_RESERVATION_MICRO_USD,
        )
        _require_int(
            self.daily_budget_usd_micros,
            minimum=1,
            maximum=MAX_DAILY_BUDGET_MICRO_USD,
        )
        _require_int(
            self.monthly_budget_usd_micros,
            minimum=1,
            maximum=MAX_MONTHLY_BUDGET_MICRO_USD,
        )
        if self.daily_budget_usd_micros > self.monthly_budget_usd_micros:
            raise ResearchConfigurationError
        _require_int(self.prompt_max_utf8_bytes, minimum=1, maximum=MAX_PROMPT_UTF8_BYTES)
        _require_int(
            self.provider_response_max_bytes,
            minimum=1,
            maximum=MAX_PROVIDER_RESPONSE_BYTES,
        )
        _require_int(self.answer_max_utf8_bytes, minimum=1, maximum=MAX_ANSWER_UTF8_BYTES)
        _require_int(self.citation_count_max, minimum=1, maximum=MAX_CITATION_COUNT)
        _require_int(self.connect_timeout_seconds, minimum=1, maximum=MAX_CONNECT_TIMEOUT_SECONDS)
        _require_int(
            self.http_operation_timeout_seconds,
            minimum=1,
            maximum=MAX_HTTP_OPERATION_TIMEOUT_SECONDS,
        )
        _require_int(self.poll_interval_seconds, minimum=1, maximum=MAX_POLL_INTERVAL_SECONDS)
        try:
            if validate_provider_identifier(self.provider_id) != self.provider_id:
                raise ResearchConfigurationError
            if validate_model_identifier(self.model_id) != self.model_id:
                raise ResearchConfigurationError
            if validate_credential_environment_name(self.credential_identifier) != self.credential_identifier:
                raise ResearchConfigurationError
        except AiProviderRecordError:
            raise ResearchConfigurationError from None


def default_research_configuration(*, enabled: bool = False) -> ResearchConfiguration:
    """Return the accepted non-secret defaults. Research stays disabled."""
    return ResearchConfiguration(
        enabled=enabled,
        provider_id=OPENAI_RESPONSES_PROVIDER_ID,
        model_id=DEFAULT_RESEARCH_MODEL_ID,
        background=True,
        credential_identifier=RESEARCH_CREDENTIAL_IDENTIFIER,
        search=ResearchOperationSettings(
            reasoning_effort="low",
            tool_allowlist=(WEB_SEARCH_TOOL,),
            max_tool_calls=MAX_SEARCH_TOOL_CALLS,
            max_output_tokens=MAX_SEARCH_OUTPUT_TOKENS,
            deadline_seconds=MAX_SEARCH_DEADLINE_SECONDS,
            budget_reservation_usd_micros=MAX_SEARCH_RESERVATION_MICRO_USD,
        ),
        research=ResearchOperationSettings(
            reasoning_effort="high",
            tool_allowlist=(WEB_SEARCH_TOOL,),
            max_tool_calls=MAX_RESEARCH_TOOL_CALLS,
            max_output_tokens=MAX_RESEARCH_OUTPUT_TOKENS,
            deadline_seconds=MAX_RESEARCH_DEADLINE_SECONDS,
            budget_reservation_usd_micros=MAX_RESEARCH_RESERVATION_MICRO_USD,
        ),
        daily_budget_usd_micros=MAX_DAILY_BUDGET_MICRO_USD,
        monthly_budget_usd_micros=MAX_MONTHLY_BUDGET_MICRO_USD,
        prompt_max_utf8_bytes=MAX_PROMPT_UTF8_BYTES,
        provider_response_max_bytes=MAX_PROVIDER_RESPONSE_BYTES,
        answer_max_utf8_bytes=MAX_ANSWER_UTF8_BYTES,
        citation_count_max=MAX_CITATION_COUNT,
        connect_timeout_seconds=5,
        http_operation_timeout_seconds=30,
        poll_interval_seconds=5,
    )


def parse_research_configuration(payload: object) -> ResearchConfiguration:
    """Parse one research section. Unknown and malformed fields are rejected."""
    if not isinstance(payload, dict) or set(payload) != _SECTION_KEYS:
        raise ResearchConfigurationError
    return ResearchConfiguration(
        enabled=_require_bool(payload.get("enabled")),
        provider_id=_require_provider_id(payload.get("provider_id")),
        model_id=_require_model_id(payload.get("model_id")),
        background=_require_bool(payload.get("background")),
        credential_identifier=_require_credential_identifier(payload.get("credential_identifier")),
        search=_parse_operation(
            payload.get("search"),
            max_tool_calls=MAX_SEARCH_TOOL_CALLS,
            max_output_tokens=MAX_SEARCH_OUTPUT_TOKENS,
            max_deadline_seconds=MAX_SEARCH_DEADLINE_SECONDS,
            max_reservation_usd_micros=MAX_SEARCH_RESERVATION_MICRO_USD,
        ),
        research=_parse_operation(
            payload.get("research"),
            max_tool_calls=MAX_RESEARCH_TOOL_CALLS,
            max_output_tokens=MAX_RESEARCH_OUTPUT_TOKENS,
            max_deadline_seconds=MAX_RESEARCH_DEADLINE_SECONDS,
            max_reservation_usd_micros=MAX_RESEARCH_RESERVATION_MICRO_USD,
        ),
        daily_budget_usd_micros=_require_int(
            payload.get("daily_budget_usd_micros"),
            minimum=1,
            maximum=MAX_DAILY_BUDGET_MICRO_USD,
        ),
        monthly_budget_usd_micros=_require_int(
            payload.get("monthly_budget_usd_micros"),
            minimum=1,
            maximum=MAX_MONTHLY_BUDGET_MICRO_USD,
        ),
        prompt_max_utf8_bytes=_require_int(
            payload.get("prompt_max_utf8_bytes"),
            minimum=1,
            maximum=MAX_PROMPT_UTF8_BYTES,
        ),
        provider_response_max_bytes=_require_int(
            payload.get("provider_response_max_bytes"),
            minimum=1,
            maximum=MAX_PROVIDER_RESPONSE_BYTES,
        ),
        answer_max_utf8_bytes=_require_int(
            payload.get("answer_max_utf8_bytes"),
            minimum=1,
            maximum=MAX_ANSWER_UTF8_BYTES,
        ),
        citation_count_max=_require_int(
            payload.get("citation_count_max"),
            minimum=1,
            maximum=MAX_CITATION_COUNT,
        ),
        connect_timeout_seconds=_require_int(
            payload.get("connect_timeout_seconds"),
            minimum=1,
            maximum=MAX_CONNECT_TIMEOUT_SECONDS,
        ),
        http_operation_timeout_seconds=_require_int(
            payload.get("http_operation_timeout_seconds"),
            minimum=1,
            maximum=MAX_HTTP_OPERATION_TIMEOUT_SECONDS,
        ),
        poll_interval_seconds=_require_int(
            payload.get("poll_interval_seconds"),
            minimum=1,
            maximum=MAX_POLL_INTERVAL_SECONDS,
        ),
    )


def serialize_research_configuration(config: ResearchConfiguration) -> dict[str, Any]:
    """Serialize one validated research section without secret material."""
    if not isinstance(config, ResearchConfiguration):
        raise ResearchConfigurationError
    parse_research_configuration(_section_payload(config))
    return _section_payload(config)


def _section_payload(config: ResearchConfiguration) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "provider_id": config.provider_id,
        "model_id": config.model_id,
        "background": config.background,
        "credential_identifier": config.credential_identifier,
        "search": _operation_payload(config.search),
        "research": _operation_payload(config.research),
        "daily_budget_usd_micros": config.daily_budget_usd_micros,
        "monthly_budget_usd_micros": config.monthly_budget_usd_micros,
        "prompt_max_utf8_bytes": config.prompt_max_utf8_bytes,
        "provider_response_max_bytes": config.provider_response_max_bytes,
        "answer_max_utf8_bytes": config.answer_max_utf8_bytes,
        "citation_count_max": config.citation_count_max,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "http_operation_timeout_seconds": config.http_operation_timeout_seconds,
        "poll_interval_seconds": config.poll_interval_seconds,
    }


def _operation_payload(settings: ResearchOperationSettings) -> dict[str, Any]:
    return {
        "reasoning_effort": settings.reasoning_effort,
        "tool_allowlist": list(settings.tool_allowlist),
        "max_tool_calls": settings.max_tool_calls,
        "max_output_tokens": settings.max_output_tokens,
        "deadline_seconds": settings.deadline_seconds,
        "budget_reservation_usd_micros": settings.budget_reservation_usd_micros,
    }


def _parse_operation(
    payload: object,
    *,
    max_tool_calls: int,
    max_output_tokens: int,
    max_deadline_seconds: int,
    max_reservation_usd_micros: int,
) -> ResearchOperationSettings:
    if not isinstance(payload, dict) or set(payload) != _OPERATION_KEYS:
        raise ResearchConfigurationError
    settings = ResearchOperationSettings(
        reasoning_effort=_require_reasoning(payload.get("reasoning_effort")),
        tool_allowlist=_require_tools(payload.get("tool_allowlist")),
        max_tool_calls=_require_int(payload.get("max_tool_calls"), minimum=1, maximum=max_tool_calls),
        max_output_tokens=_require_int(
            payload.get("max_output_tokens"),
            minimum=1,
            maximum=max_output_tokens,
        ),
        deadline_seconds=_require_int(
            payload.get("deadline_seconds"),
            minimum=1,
            maximum=max_deadline_seconds,
        ),
        budget_reservation_usd_micros=_require_int(
            payload.get("budget_reservation_usd_micros"),
            minimum=1,
            maximum=max_reservation_usd_micros,
        ),
    )
    _require_operation_ceiling(
        settings,
        max_tool_calls=max_tool_calls,
        max_output_tokens=max_output_tokens,
        max_deadline_seconds=max_deadline_seconds,
        max_reservation_usd_micros=max_reservation_usd_micros,
    )
    return settings


def _require_operation_ceiling(
    settings: ResearchOperationSettings,
    *,
    max_tool_calls: int,
    max_output_tokens: int,
    max_deadline_seconds: int,
    max_reservation_usd_micros: int,
) -> None:
    if (
        settings.max_tool_calls > max_tool_calls
        or settings.max_output_tokens > max_output_tokens
        or settings.deadline_seconds > max_deadline_seconds
        or settings.budget_reservation_usd_micros > max_reservation_usd_micros
    ):
        raise ResearchConfigurationError


def _require_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ResearchConfigurationError
    return value


def _require_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ResearchConfigurationError
    return value


def _require_reasoning(value: object) -> str:
    if value not in ALLOWED_REASONING_EFFORTS:
        raise ResearchConfigurationError
    return str(value)


def _require_tools(value: object) -> tuple[str, ...]:
    if value != [WEB_SEARCH_TOOL]:
        raise ResearchConfigurationError
    return (WEB_SEARCH_TOOL,)


def _require_provider_id(value: object) -> str:
    try:
        normalized = validate_provider_identifier(value)
    except AiProviderRecordError:
        raise ResearchConfigurationError from None
    if normalized != OPENAI_RESPONSES_PROVIDER_ID:
        raise ResearchConfigurationError
    return normalized


def _require_model_id(value: object) -> str:
    try:
        normalized = validate_model_identifier(value)
    except AiProviderRecordError:
        raise ResearchConfigurationError from None
    if not is_known_selectable_model(normalized):
        raise ResearchConfigurationError
    return normalized


def _require_credential_identifier(value: object) -> str:
    try:
        return validate_credential_environment_name(value)
    except AiProviderRecordError:
        raise ResearchConfigurationError from None
