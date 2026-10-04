"""Coordinator lifecycle evidence with a fake provider and the real store."""

from __future__ import annotations

from pathlib import Path

import pytest

from kronika.application.ports.research import ResearchStoreError
from kronika.application.research import ResearchCoordinator
from kronika.domain.research import (
    CompletionEvidence,
    FIXED_OPENAI_RESPONSES_MODEL_ID,
    OPENAI_RESPONSES_PROVIDER_ID,
    ProviderHandle,
    ProviderObservation,
    ProviderObservationKind,
    ResearchAccountingState,
    ResearchAnswer,
    ResearchCitation,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchUsage,
    ResultCompletionReceipt,
    UsagePriceSchedule,
)
from kronika.infrastructure.ai.research_configuration import (
    default_research_configuration,
)
from kronika.infrastructure.ai.research_registry import (
    ResearchSelectionError,
    select_research_provider,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.research_budget_repository import (
    SqliteResearchBudgetLedger,
)
from kronika.infrastructure.persistence.research_request_repository import (
    SqliteResearchRequestRepository,
)

HANDLE = ProviderHandle("remote-handle-1")


def _migrate(database_path: Path) -> None:
    from alembic import command

    from kronika.infrastructure.persistence.migrations import _alembic_config

    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
    finally:
        dispose_engine(engine)


class FakeProvider:
    def __init__(self) -> None:
        self.submit_observation: ProviderObservation | None = None
        self.poll_observations: list[ProviderObservation] = []
        self.cancel_observation: ProviderObservation | None = None
        self.release_outcome_error: Exception | None = None
        self.calls: list[str] = []

    def describe(self):  # pragma: no cover - not used by the coordinator
        raise AssertionError("describe must not be called by the coordinator")

    def submit(self, request):
        self.calls.append("submit")
        if isinstance(self.submit_observation, Exception):
            raise self.submit_observation
        assert self.submit_observation is not None
        return self.submit_observation

    def poll(self, handle):
        self.calls.append("poll")
        if not self.poll_observations:
            raise AssertionError("unexpected poll")
        observation = self.poll_observations.pop(0)
        if isinstance(observation, Exception):
            raise observation
        return observation

    def cancel(self, handle):
        self.calls.append("cancel")
        assert self.cancel_observation is not None
        return self.cancel_observation

    def release_remote(self, handle):
        from kronika.domain.research import CleanupOutcome, ResearchRemoteCleanupState

        self.calls.append("release")
        if self.release_outcome_error is not None:
            raise self.release_outcome_error
        return CleanupOutcome(state=ResearchRemoteCleanupState.DELETED)


class FakeCompletion:
    def __init__(self) -> None:
        self.fail_next = False
        self.payloads = []

    def complete(self, completion):
        self.payloads.append(completion)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic completion failure")
        return ResultCompletionReceipt(
            operation_id=completion.operation_id,
            state=ResearchLifecycleState.SAVED,
        )


def _answer() -> ResearchAnswer:
    return ResearchAnswer(
        text="Synthetic answer with evidence.",
        citations=(ResearchCitation(url="https://example.invalid/a", title="A"),),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        usage=ResearchUsage(
            input_tokens=1_000,
            cached_input_tokens=0,
            output_tokens=500,
            reasoning_tokens=100,
            web_tool_calls=1,
        ),
    )


def _observation(kind: ProviderObservationKind, **fields) -> ProviderObservation:
    return ProviderObservation(kind=kind, **fields)


@pytest.fixture()
def context(tmp_path: Path):
    database = tmp_path / "coordinator.sqlite3"
    _migrate(database)
    engine = create_sqlite_engine(database)
    now = [1_000_000]
    counter = [0]

    def next_id() -> str:
        counter[0] += 1
        return f"op-coord-{counter[0]:04d}"

    provider = FakeProvider()
    requests = SqliteResearchRequestRepository(engine, clock_ms=lambda: now[0])
    ledger = SqliteResearchBudgetLedger(engine, clock_ms=lambda: now[0])
    completion = FakeCompletion()
    zero_schedule = UsagePriceSchedule(
        input_micro_usd_per_million=0,
        cached_input_micro_usd_per_million=0,
        output_micro_usd_per_million=0,
        web_search_micro_usd_per_thousand=0,
    )
    coordinator = ResearchCoordinator(
        provider=provider,
        requests=requests,
        ledger=ledger,
        completion=completion,
        select=lambda kind: select_research_provider(
            default_research_configuration(enabled=True), kind=kind
        ),
        resolve_price_schedule=lambda provider_id, model_id, version: zero_schedule,
        clock_ms=lambda: now[0],
        new_operation_id=next_id,
    )
    try:
        yield {
            "engine": engine,
            "now": now,
            "provider": provider,
            "requests": requests,
            "ledger": ledger,
            "completion": completion,
            "coordinator": coordinator,
        }
    finally:
        dispose_engine(engine)


def _admit(context, *, client: str = "client-0001"):
    return context["coordinator"].admit(
        owner_login_key="alice@example.com",
        client_request_id=client,
        kind=ResearchOperationKind.SEARCH,
        prompt="What is the synthetic question?",
    ).row


def test_admit_persists_and_duplicate_returns_existing(context) -> None:
    row = _admit(context)
    assert row.record.state is ResearchLifecycleState.ADMITTED
    assert row.record.profile.provider_id == OPENAI_RESPONSES_PROVIDER_ID
    assert row.record.profile.model_id == FIXED_OPENAI_RESPONSES_MODEL_ID
    replay = _admit(context)
    assert replay.record.operation_id == row.record.operation_id
    assert context["requests"].active_slot_operation_id() == row.record.operation_id
    with pytest.raises(ResearchStoreError) as caught:
        context["coordinator"].admit(
            owner_login_key="alice@example.com",
            client_request_id="client-0001",
            kind=ResearchOperationKind.SEARCH,
            prompt="A different question?",
        )
    assert caught.value.code is ResearchErrorCode.IDEMPOTENCY_CONFLICT


def test_admit_disabled_configuration_is_refused(context) -> None:
    coordinator = ResearchCoordinator(
        provider=context["provider"],
        requests=context["requests"],
        ledger=context["ledger"],
        completion=context["completion"],
        select=lambda kind: _raise_selection(),
        clock_ms=lambda: context["now"][0],
        new_operation_id=lambda: "op-coord-disabled",
    )
    with pytest.raises(ResearchStoreError) as caught:
        coordinator.admit(
            owner_login_key="alice@example.com",
            client_request_id="client-0003",
            kind=ResearchOperationKind.SEARCH,
            prompt="Question?",
        )
    assert caught.value.code is ResearchErrorCode.DISABLED


def _raise_selection():
    raise ResearchSelectionError(ResearchErrorCode.DISABLED)


def _coordinator(context, **overrides) -> ResearchCoordinator:
    kwargs = {
        "provider": context["provider"],
        "requests": context["requests"],
        "ledger": context["ledger"],
        "completion": context["completion"],
        "select": lambda kind: select_research_provider(
            default_research_configuration(enabled=True), kind=kind
        ),
        "clock_ms": lambda: context["now"][0],
        "new_operation_id": lambda: "op-coord-extra",
    }
    kwargs.update(overrides)
    return ResearchCoordinator(**kwargs)


def test_accounting_blocker_refuses_admission(context) -> None:
    coordinator = _coordinator(
        context,
        accounting_blocker=lambda: ResearchErrorCode.ACCOUNTING_UNKNOWN,
    )
    with pytest.raises(ResearchStoreError) as caught:
        coordinator.admit(
            owner_login_key="alice@example.com",
            client_request_id="client-blocked",
            kind=ResearchOperationKind.SEARCH,
            prompt="Question?",
        )
    assert caught.value.code is ResearchErrorCode.ACCOUNTING_UNKNOWN


def test_admission_guard_refuses_expired_cutoff(context) -> None:
    coordinator = _coordinator(
        context,
        admission_guard=lambda snapshot, now_ms: ResearchErrorCode.CAPABILITY_UNAVAILABLE,
    )
    with pytest.raises(ResearchStoreError) as caught:
        coordinator.admit(
            owner_login_key="alice@example.com",
            client_request_id="client-expired",
            kind=ResearchOperationKind.SEARCH,
            prompt="Question?",
        )
    assert caught.value.code is ResearchErrorCode.CAPABILITY_UNAVAILABLE


def test_disabled_submission_does_not_claim(context) -> None:
    row = _admit(context, client="client-disabled")
    coordinator = _coordinator(context, submission_enabled=lambda: False)
    assert coordinator.submit_pending() is None
    stored = context["requests"].get_request(row.record.operation_id)
    assert stored is not None
    assert stored.record.state is ResearchLifecycleState.ADMITTED


def test_replay_matches_after_content_changes_and_is_new_once(context) -> None:
    first = context["coordinator"].admit(
        owner_login_key="alice@example.com",
        client_request_id="client-replay",
        kind=ResearchOperationKind.SEARCH,
        prompt="Question?",
        consent_version="2026-09",
    )
    assert first.newly_admitted is True
    replay = context["coordinator"].admit(
        owner_login_key="alice@example.com",
        client_request_id="client-replay",
        kind=ResearchOperationKind.SEARCH,
        prompt="Question?",
        consent_version="2026-09",
    )
    assert replay.newly_admitted is False
    assert replay.row.record.operation_id == first.row.record.operation_id
    with pytest.raises(ResearchStoreError) as caught:
        context["coordinator"].admit(
            owner_login_key="alice@example.com",
            client_request_id="client-replay",
            kind=ResearchOperationKind.SEARCH,
            prompt="A different question?",
            consent_version="2026-09",
        )
    assert caught.value.code is ResearchErrorCode.IDEMPOTENCY_CONFLICT


def test_submit_transitions_to_running_and_refusal_finishes(context) -> None:
    row = _admit(context)
    provider = context["provider"]
    provider.submit_observation = _observation(
        ProviderObservationKind.RUNNING, remote_handle=HANDLE
    )
    running = context["coordinator"].submit_pending()
    assert running is not None
    assert running.record.state is ResearchLifecycleState.RUNNING
    assert running.record.remote_handle == HANDLE
    assert running.submitted_at_ms == context["now"][0]

    provider.poll_observations.append(
        _observation(
            ProviderObservationKind.REFUSED,
            error_code=ResearchErrorCode.REFUSED,
        )
    )
    finished = context["coordinator"].poll_once()
    assert finished is not None
    assert finished.record.state is ResearchLifecycleState.REFUSED
    assert finished.record.error_code is ResearchErrorCode.REFUSED
    assert context["requests"].active_slot_operation_id() is None


def test_submit_uncertain_becomes_submission_unknown(context) -> None:
    _admit(context)
    context["provider"].submit_observation = _observation(
        ProviderObservationKind.UNCERTAIN,
        error_code=ResearchErrorCode.SUBMISSION_UNKNOWN,
    )
    finished = context["coordinator"].submit_pending()
    assert finished is not None
    assert finished.record.state is ResearchLifecycleState.SUBMISSION_UNKNOWN
    assert context["requests"].active_slot_operation_id() is None


def test_success_flow_saves_and_releases_remote(context) -> None:
    _admit(context)
    provider = context["provider"]
    provider.submit_observation = _observation(
        ProviderObservationKind.RUNNING, remote_handle=HANDLE
    )
    context["coordinator"].submit_pending()
    provider.poll_observations.append(
        _observation(
            ProviderObservationKind.COMPLETE,
            remote_handle=HANDLE,
            answer=_answer(),
        )
    )
    saved = context["coordinator"].poll_once()
    assert saved is not None
    assert saved.record.state is ResearchLifecycleState.SAVED
    assert saved.record.accounting_state is ResearchAccountingState.RECONCILED
    assert saved.record.cleanup_state.value == "pending"
    assert context["requests"].active_slot_operation_id() is None
    assert len(context["completion"].payloads) == 1
    released = context["coordinator"].release_remote_pending()
    assert len(released) == 1
    assert released[0].record.cleanup_state.value == "deleted"


def test_completion_failure_keeps_validating_and_retries(context) -> None:
    _admit(context)
    provider = context["provider"]
    provider.submit_observation = _observation(
        ProviderObservationKind.RUNNING, remote_handle=HANDLE
    )
    context["coordinator"].submit_pending()
    context["completion"].fail_next = True
    provider.poll_observations.append(
        _observation(
            ProviderObservationKind.COMPLETE,
            remote_handle=HANDLE,
            answer=_answer(),
        )
    )
    validating = context["coordinator"].poll_once()
    assert validating is not None
    assert validating.record.state is ResearchLifecycleState.VALIDATING
    assert validating.checkpoint_json is not None
    assert validating.checkpoint_sha256 is not None
    retried = context["coordinator"].poll_once()
    assert retried is not None
    assert retried.record.state is ResearchLifecycleState.SAVED


def test_cancel_before_save_prevents_finalization(context) -> None:
    row = _admit(context)
    provider = context["provider"]
    provider.submit_observation = _observation(
        ProviderObservationKind.RUNNING, remote_handle=HANDLE
    )
    context["coordinator"].submit_pending()
    provider.cancel_observation = _observation(
        ProviderObservationKind.CANCELLED,
        error_code=ResearchErrorCode.CANCELLED,
    )
    cancelled = context["coordinator"].cancel(row.record.operation_id)
    assert cancelled.record.state is ResearchLifecycleState.CANCELLED
    assert cancelled.cancellation_confirmed_at_ms == context["now"][0]
    assert context["requests"].active_slot_operation_id() is None


def test_deadline_timeout_finishes_timeout(context) -> None:
    _admit(context)
    provider = context["provider"]
    provider.submit_observation = _observation(
        ProviderObservationKind.RUNNING, remote_handle=HANDLE
    )
    running = context["coordinator"].submit_pending()
    assert running is not None
    context["now"][0] = running.admitted_at_ms + 180_000 + 1
    provider.cancel_observation = _observation(
        ProviderObservationKind.CANCELLED,
        error_code=ResearchErrorCode.CANCELLED,
    )
    finished = context["coordinator"].poll_once()
    assert finished is not None
    assert finished.record.state is ResearchLifecycleState.TIMEOUT
    assert finished.record.error_code is ResearchErrorCode.TIMEOUT
    assert "cancel" in provider.calls
    assert context["requests"].active_slot_operation_id() is None


def test_recover_submitting_becomes_submission_unknown(context) -> None:
    row = _admit(context)
    requests = context["requests"]
    marker = row.with_record(
        _replace_state(row.record, ResearchLifecycleState.SUBMITTING)
    )
    requests.save(marker)
    recovered = context["coordinator"].recover()
    assert recovered is not None
    assert recovered.record.state is ResearchLifecycleState.SUBMISSION_UNKNOWN
    assert context["requests"].active_slot_operation_id() is None


def _replace_state(record, state):
    from dataclasses import replace

    return replace(record, state=state)
