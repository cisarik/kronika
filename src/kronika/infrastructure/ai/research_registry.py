"""Server-controlled research provider registry.

Descriptors are static. This module does not import plugins, does not read
client endpoint, model, or tool fields, and does not fall back to another
provider. A future self-hosted provider needs an explicit later grant before
it can appear here.

The OpenAI Responses descriptor is present and unconfigured. No adapter is
shipped in this module. The parked chatgpt.com capture descriptor cannot
accept new Search or Research work.
"""

from __future__ import annotations

from kronika.application.ports.research import (
    ResearchSelectionError,
    ResearchSelectionSnapshot,
)
from kronika.domain.research import (
    CHATGPT_PAGE_PROVIDER_ID,
    OPENAI_RESPONSES_PROVIDER_ID,
    RESEARCH_CONFIGURATION_VERSION,
    SELF_HOSTED_PROVIDER_EXTENSION,
    UNSHIPPED_ADAPTER_VERSION,
    AccountingCapabilities,
    ApprovedResourceLimits,
    ExecutionLocation,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderDescriptor,
    ResearchErrorCode,
    ResearchOperationKind,
    ResearchValueError,
    RetentionPosture,
    ServerSelectedProfile,
    SubmissionIdempotency,
)
from kronika.infrastructure.ai.research_configuration import (
    ResearchConfiguration,
    ResearchOperationSettings,
)
from kronika.infrastructure.ai.research_models import (
    RESEARCH_ADMISSION_PROFILE_VERSION,
    is_known_selectable_model,
)

# Documented extension point only. It is not selectable and not imported.
SELF_HOSTED_RESEARCH_EXTENSION = SELF_HOSTED_PROVIDER_EXTENSION

# ResearchSelectionError and ResearchSelectionSnapshot are re-exported from the
# application ports so the coordinator never imports this infrastructure module.


def _accounting(*, reports: bool) -> AccountingCapabilities:
    return AccountingCapabilities(
        reports_token_usage=reports,
        reports_cached_input_separately=reports,
        reports_web_tool_calls=reports,
        missing_usage_is_visible_failure=True,
    )


def _openai_responses_descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=OPENAI_RESPONSES_PROVIDER_ID,
        adapter_version=UNSHIPPED_ADAPTER_VERSION,
        configuration_version=RESEARCH_CONFIGURATION_VERSION,
        capabilities=ProviderCapabilities(
            search=True,
            research=True,
            native_research=True,
            cancellation=True,
            remote_retrieval=True,
            remote_deletion=True,
        ),
        execution_location=ExecutionLocation.PROVIDER_NATIVE,
        retention_posture=RetentionPosture.STANDARD_REMOTE_DELETE,
        accounting=_accounting(reports=True),
        submission_idempotency=SubmissionIdempotency.NOT_GUARANTEED,
        availability=ProviderAvailability.UNCONFIGURED,
    )


def _chatgpt_page_descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=CHATGPT_PAGE_PROVIDER_ID,
        adapter_version=UNSHIPPED_ADAPTER_VERSION,
        configuration_version=RESEARCH_CONFIGURATION_VERSION,
        capabilities=ProviderCapabilities(
            search=False,
            research=False,
            native_research=False,
            cancellation=False,
            remote_retrieval=False,
            remote_deletion=False,
        ),
        execution_location=ExecutionLocation.PARKED,
        retention_posture=RetentionPosture.NOT_APPLICABLE,
        accounting=_accounting(reports=False),
        submission_idempotency=SubmissionIdempotency.NOT_GUARANTEED,
        availability=ProviderAvailability.PARKED,
    )


RESEARCH_PROVIDER_DESCRIPTORS: dict[str, ProviderDescriptor] = {
    OPENAI_RESPONSES_PROVIDER_ID: _openai_responses_descriptor(),
    CHATGPT_PAGE_PROVIDER_ID: _chatgpt_page_descriptor(),
}


def research_provider_descriptor(provider_id: str) -> ProviderDescriptor:
    """Return one catalog descriptor. Unknown ids are rejected."""
    try:
        return RESEARCH_PROVIDER_DESCRIPTORS[provider_id]
    except KeyError:
        raise ResearchSelectionError(ResearchErrorCode.NOT_CONFIGURED) from None


def require_selectable(provider_id: str, kind: ResearchOperationKind) -> ProviderDescriptor:
    """Reject parked, disabled, and incapable providers. There is no fallback."""
    if not isinstance(kind, ResearchOperationKind):
        raise ResearchSelectionError(ResearchErrorCode.INVALID_REQUEST)
    descriptor = research_provider_descriptor(provider_id)
    if descriptor.availability is ProviderAvailability.DISABLED:
        raise ResearchSelectionError(ResearchErrorCode.DISABLED)
    if descriptor.availability is ProviderAvailability.PARKED or not descriptor.capabilities.supports(kind):
        raise ResearchSelectionError(ResearchErrorCode.CAPABILITY_UNAVAILABLE)
    if descriptor.provider_id != provider_id:
        raise ResearchSelectionError(ResearchErrorCode.NOT_CONFIGURED)
    return descriptor


def select_research_provider(
    config: ResearchConfiguration | None,
    *,
    kind: ResearchOperationKind,
) -> ResearchSelectionSnapshot:
    """Snapshot the server-selected provider. Configuration changes do not rewrite it."""
    if config is None or not config.enabled:
        raise ResearchSelectionError(ResearchErrorCode.DISABLED)
    if not isinstance(config, ResearchConfiguration):
        raise ResearchSelectionError(ResearchErrorCode.NOT_CONFIGURED)
    descriptor = require_selectable(config.provider_id, kind)
    if not is_known_selectable_model(config.model_id):
        raise ResearchSelectionError(ResearchErrorCode.CAPABILITY_UNAVAILABLE)
    settings = _settings_for(config, kind)
    profile = _profile_from(config, settings)
    limits = _limits_from(config, settings)
    return ResearchSelectionSnapshot(
        provider_id=descriptor.provider_id,
        model_id=config.model_id,
        kind=kind,
        profile=profile,
        resource_limits=limits,
        deadline_seconds=settings.deadline_seconds,
        descriptor=descriptor,
        live_ready=False,
        daily_budget_usd_micros=config.daily_budget_usd_micros,
        monthly_budget_usd_micros=config.monthly_budget_usd_micros,
    )


def _settings_for(
    config: ResearchConfiguration,
    kind: ResearchOperationKind,
) -> ResearchOperationSettings:
    if kind is ResearchOperationKind.SEARCH:
        return config.search
    if kind is ResearchOperationKind.RESEARCH:
        return config.research
    raise ResearchSelectionError(ResearchErrorCode.INVALID_REQUEST)


def _profile_from(
    config: ResearchConfiguration,
    settings: ResearchOperationSettings,
) -> ServerSelectedProfile:
    return ServerSelectedProfile(
        provider_id=config.provider_id,
        model_id=config.model_id,
        configuration_version=RESEARCH_ADMISSION_PROFILE_VERSION,
        reasoning_effort=settings.reasoning_effort,
        tool_allowlist=settings.tool_allowlist,
        background=config.background,
        max_tool_calls=settings.max_tool_calls,
        max_output_tokens=settings.max_output_tokens,
        deadline_seconds=settings.deadline_seconds,
        budget_reservation_usd_micros=settings.budget_reservation_usd_micros,
    )


def _limits_from(
    config: ResearchConfiguration,
    settings: ResearchOperationSettings,
) -> ApprovedResourceLimits:
    return ApprovedResourceLimits(
        max_tool_calls=settings.max_tool_calls,
        max_output_tokens=settings.max_output_tokens,
        budget_reservation_usd_micros=settings.budget_reservation_usd_micros,
        prompt_max_utf8_bytes=config.prompt_max_utf8_bytes,
        answer_max_utf8_bytes=config.answer_max_utf8_bytes,
        citation_count_max=config.citation_count_max,
    )
