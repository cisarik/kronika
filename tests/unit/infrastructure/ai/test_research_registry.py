"""Registry tests for provider-neutral research selection."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kronika.domain.research import (
    CHATGPT_PAGE_PROVIDER_ID,
    FIXED_OPENAI_RESPONSES_MODEL_ID,
    OPENAI_RESPONSES_PROVIDER_ID,
    SELF_HOSTED_PROVIDER_EXTENSION,
    ExecutionLocation,
    ProviderAvailability,
    ResearchErrorCode,
    ResearchOperationKind,
    SubmissionIdempotency,
)
from kronika.infrastructure.ai.research_configuration import default_research_configuration
from kronika.infrastructure.ai.research_registry import (
    RESEARCH_PROVIDER_DESCRIPTORS,
    ResearchSelectionError,
    require_selectable,
    research_provider_descriptor,
    select_research_provider,
)


def test_descriptors_map_capabilities_without_a_shipped_adapter() -> None:
    openai = research_provider_descriptor(OPENAI_RESPONSES_PROVIDER_ID)
    parked = research_provider_descriptor(CHATGPT_PAGE_PROVIDER_ID)

    assert openai.availability is ProviderAvailability.UNCONFIGURED
    assert openai.execution_location is ExecutionLocation.PROVIDER_NATIVE
    assert openai.submission_idempotency is SubmissionIdempotency.NOT_GUARANTEED
    assert openai.capabilities.search is True
    assert openai.capabilities.research is True
    assert openai.capabilities.native_research is True
    assert openai.capabilities.cancellation is True
    assert openai.capabilities.remote_retrieval is True
    assert openai.capabilities.remote_deletion is True
    assert openai.accounting.missing_usage_is_visible_failure is True

    assert parked.availability is ProviderAvailability.PARKED
    assert parked.capabilities.search is False
    assert parked.capabilities.research is False
    assert parked.submission_idempotency is SubmissionIdempotency.NOT_GUARANTEED
    assert SELF_HOSTED_PROVIDER_EXTENSION not in RESEARCH_PROVIDER_DESCRIPTORS


def test_parked_provider_is_unavailable_for_new_work() -> None:
    with pytest.raises(ResearchSelectionError) as caught:
        require_selectable(CHATGPT_PAGE_PROVIDER_ID, ResearchOperationKind.SEARCH)

    assert caught.value.code is ResearchErrorCode.CAPABILITY_UNAVAILABLE


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ResearchSelectionError) as caught:
        research_provider_descriptor("not-a-provider")

    assert caught.value.code is ResearchErrorCode.NOT_CONFIGURED
    with pytest.raises(ResearchSelectionError) as extension:
        research_provider_descriptor(SELF_HOSTED_PROVIDER_EXTENSION)
    assert extension.value.code is ResearchErrorCode.NOT_CONFIGURED


def test_selection_snapshot_stays_stable_when_configuration_changes() -> None:
    config = default_research_configuration(enabled=True)
    snapshot = select_research_provider(config, kind=ResearchOperationKind.SEARCH)

    assert snapshot.provider_id == OPENAI_RESPONSES_PROVIDER_ID
    assert snapshot.model_id == FIXED_OPENAI_RESPONSES_MODEL_ID
    assert snapshot.live_ready is False
    assert snapshot.descriptor.availability is ProviderAvailability.UNCONFIGURED
    assert snapshot.profile.deadline_seconds == 180
    assert snapshot.profile.tool_allowlist == ("web_search",)

    changed = replace(config, search=replace(config.search, deadline_seconds=30))
    later = select_research_provider(changed, kind=ResearchOperationKind.SEARCH)

    assert snapshot.provider_id == OPENAI_RESPONSES_PROVIDER_ID
    assert snapshot.model_id == FIXED_OPENAI_RESPONSES_MODEL_ID
    assert snapshot.profile.deadline_seconds == 180
    assert later.profile.deadline_seconds == 30
    assert later.provider_id == snapshot.provider_id
    assert later.model_id == snapshot.model_id


def test_selection_does_not_fall_back_when_research_is_disabled() -> None:
    config = default_research_configuration(enabled=True)
    snapshot = select_research_provider(config, kind=ResearchOperationKind.RESEARCH)
    disabled = replace(config, enabled=False)

    with pytest.raises(ResearchSelectionError) as caught:
        select_research_provider(disabled, kind=ResearchOperationKind.RESEARCH)
    with pytest.raises(ResearchSelectionError) as missing:
        select_research_provider(None, kind=ResearchOperationKind.SEARCH)

    assert caught.value.code is ResearchErrorCode.DISABLED
    assert missing.value.code is ResearchErrorCode.DISABLED
    assert snapshot.provider_id == OPENAI_RESPONSES_PROVIDER_ID
    assert snapshot.model_id == FIXED_OPENAI_RESPONSES_MODEL_ID
    assert CHATGPT_PAGE_PROVIDER_ID != snapshot.provider_id
