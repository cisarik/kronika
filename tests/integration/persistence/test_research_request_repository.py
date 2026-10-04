"""Repository and ledger evidence for the durable research runtime (0035)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kronika.application.ports.research import (
    ResearchRequestRow,
    ResearchStoreError,
)
from kronika.domain.research import (
    ApprovedResourceLimits,
    BudgetReconciliation,
    BudgetReservation,
    FIXED_OPENAI_RESPONSES_MODEL_ID,
    OPENAI_RESPONSES_PROVIDER_ID,
    ResearchAccountingState,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ResearchRequestRecord,
    ResearchUsage,
    ProviderHandle,
    ServerSelectedProfile,
    WEB_SEARCH_TOOL,
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

FINGERPRINT = "b" * 64
RESERVATION_MICROS = 500_000
DAILY_LIMIT = 10_000_000
MONTHLY_LIMIT = 30_000_000


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


@pytest.fixture()
def engine(tmp_path: Path):
    database = tmp_path / "research.sqlite3"
    _migrate(database)
    created = create_sqlite_engine(database)
    try:
        yield created
    finally:
        dispose_engine(created)


def _profile(
    *,
    reservation_micro_usd: int = RESERVATION_MICROS,
) -> ServerSelectedProfile:
    return ServerSelectedProfile(
        provider_id=OPENAI_RESPONSES_PROVIDER_ID,
        model_id=FIXED_OPENAI_RESPONSES_MODEL_ID,
        configuration_version="3",
        reasoning_effort="low",
        tool_allowlist=(WEB_SEARCH_TOOL,),
        background=True,
        max_tool_calls=3,
        max_output_tokens=4096,
        deadline_seconds=180,
        budget_reservation_usd_micros=reservation_micro_usd,
    )


def _limits(
    *,
    reservation_micro_usd: int = RESERVATION_MICROS,
) -> ApprovedResourceLimits:
    return ApprovedResourceLimits(
        max_tool_calls=3,
        max_output_tokens=4096,
        budget_reservation_usd_micros=reservation_micro_usd,
        prompt_max_utf8_bytes=16_384,
        answer_max_utf8_bytes=2_097_152,
        citation_count_max=200,
    )


def _record(
    operation_id: str,
    *,
    state: ResearchLifecycleState = ResearchLifecycleState.ADMITTED,
    reservation_micro_usd: int = RESERVATION_MICROS,
) -> ResearchRequestRecord:
    return ResearchRequestRecord(
        operation_id=operation_id,
        kind=ResearchOperationKind.SEARCH,
        prompt="What is the synthetic question?",
        profile=_profile(reservation_micro_usd=reservation_micro_usd),
        deadline_seconds=180,
        resource_limits=_limits(reservation_micro_usd=reservation_micro_usd),
        state=state,
        cleanup_state=ResearchRemoteCleanupState.NOT_REQUIRED,
        accounting_state=ResearchAccountingState.RESERVED,
        remote_handle=None,
        error_code=None,
    )


def _row(
    operation_id: str,
    *,
    client_request_id: str,
    fingerprint: str = FINGERPRINT,
    state: ResearchLifecycleState = ResearchLifecycleState.ADMITTED,
    now_ms: int = 1_000,
    reservation_micro_usd: int = RESERVATION_MICROS,
) -> ResearchRequestRow:
    return ResearchRequestRow(
        record=_record(
            operation_id,
            state=state,
            reservation_micro_usd=reservation_micro_usd,
        ),
        owner_login_key="alice@example.com",
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        checkpoint_json=None,
        checkpoint_sha256=None,
        record_id=None,
        created_at_ms=now_ms - 1,
        admitted_at_ms=now_ms,
        submitted_at_ms=None,
        finished_at_ms=None,
        updated_at_ms=now_ms,
        cancel_requested_at_ms=None,
        cancellation_confirmed_at_ms=None,
    )


def _reservation(
    operation_id: str,
    *,
    reserved: int = RESERVATION_MICROS,
    daily: int = DAILY_LIMIT,
    monthly: int = MONTHLY_LIMIT,
) -> BudgetReservation:
    return BudgetReservation(
        operation_id=operation_id,
        kind=ResearchOperationKind.SEARCH,
        reserved_usd_micros=reserved,
        daily_limit_usd_micros=daily,
        monthly_limit_usd_micros=monthly,
    )


def test_admit_get_and_find_roundtrip(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    row = _row("op-admit-0001", client_request_id="client-0001")
    stored = repository.admit(row, _reservation("op-admit-0001"))
    assert stored.record.operation_id == "op-admit-0001"
    fetched = repository.get_request("op-admit-0001")
    assert fetched is not None
    assert fetched.record.profile == row.record.profile
    assert fetched.record.resource_limits == row.record.resource_limits
    assert fetched.record.prompt == row.record.prompt
    assert fetched.owner_login_key == "alice@example.com"
    assert fetched.request_fingerprint == FINGERPRINT
    found = repository.find_by_client("alice@example.com", "client-0001")
    assert found is not None and found.record.operation_id == "op-admit-0001"
    assert repository.active_slot_operation_id() == "op-admit-0001"
    assert repository.get_request("op-missing") is None


def test_duplicate_client_key_is_idempotent_and_conflicts_on_fingerprint(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    first = _row("op-idem-0001", client_request_id="client-0002")
    repository.admit(first, _reservation("op-idem-0001"))
    replay = repository.admit(
        _row("op-idem-0001", client_request_id="client-0002", now_ms=2_000),
        _reservation("op-idem-0001"),
    )
    assert replay.record.operation_id == "op-idem-0001"
    assert repository.active_slot_operation_id() == "op-idem-0001"
    with pytest.raises(ResearchStoreError) as caught:
        repository.admit(
            _row(
                "op-idem-0002",
                client_request_id="client-0002",
                fingerprint="c" * 64,
            ),
            _reservation("op-idem-0002"),
        )
    assert caught.value.code is ResearchErrorCode.IDEMPOTENCY_CONFLICT
    assert repository.get_request("op-idem-0002") is None


def test_second_admission_is_busy_and_terminal_save_releases_slot(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    first = _row("op-slot-0001", client_request_id="client-0003")
    repository.admit(first, _reservation("op-slot-0001"))
    with pytest.raises(ResearchStoreError) as caught:
        repository.admit(
            _row("op-slot-0002", client_request_id="client-0004"),
            _reservation("op-slot-0002"),
        )
    assert caught.value.code is ResearchErrorCode.BUSY
    assert repository.get_request("op-slot-0002") is None
    ledger = SqliteResearchBudgetLedger(engine)
    assert ledger.consumed_micros(day_key="1970-01-01", month_key="1970-01") == (
        RESERVATION_MICROS,
        RESERVATION_MICROS,
    )
    finished = first.with_record(
        _record("op-slot-0001", state=ResearchLifecycleState.FAILED)
    )
    assert repository.save(finished).record.state is ResearchLifecycleState.FAILED
    assert repository.active_slot_operation_id() is None
    repository.admit(
        _row("op-slot-0002", client_request_id="client-0004"),
        _reservation("op-slot-0002"),
    )
    assert repository.active_slot_operation_id() == "op-slot-0002"


def test_budget_exceeded_rolls_back_admission(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    first = repository.admit(
        _row("op-budget-0001", client_request_id="client-0005"),
        _reservation("op-budget-0001", daily=RESERVATION_MICROS),
    )
    repository.save(
        first.with_record(
            _record("op-budget-0001", state=ResearchLifecycleState.FAILED)
        )
    )
    with pytest.raises(ResearchStoreError) as caught:
        repository.admit(
            _row("op-budget-0002", client_request_id="client-0006"),
            _reservation("op-budget-0002", daily=RESERVATION_MICROS),
        )
    assert caught.value.code is ResearchErrorCode.BUDGET_EXCEEDED
    assert repository.get_request("op-budget-0002") is None
    assert repository.active_slot_operation_id() is None
    ledger = SqliteResearchBudgetLedger(engine)
    assert ledger.consumed_micros(day_key="1970-01-01", month_key="1970-01") == (
        RESERVATION_MICROS,
        RESERVATION_MICROS,
    )


def test_reconcile_releases_unused_reservation_for_the_next_admission(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    ledger = SqliteResearchBudgetLedger(engine)
    first = repository.admit(
        _row("op-rec-0001", client_request_id="client-0007"),
        _reservation("op-rec-0001", daily=800_000),
    )
    repository.save(
        first.with_record(_record("op-rec-0001", state=ResearchLifecycleState.SAVED))
    )
    with pytest.raises(ResearchStoreError) as caught:
        repository.admit(
            _row("op-rec-0002", client_request_id="client-0008"),
            _reservation("op-rec-0002", daily=800_000),
        )
    assert caught.value.code is ResearchErrorCode.BUDGET_EXCEEDED
    hold = ledger.reconcile(
        BudgetReconciliation(
            operation_id="op-rec-0001",
            usage=ResearchUsage(
                input_tokens=1_000,
                cached_input_tokens=0,
                output_tokens=500,
                reasoning_tokens=100,
                web_tool_calls=1,
            ),
            calculated_cost_usd_micros=300_000,
            state=ResearchAccountingState.RECONCILED,
        )
    )
    assert hold.state is ResearchAccountingState.RECONCILED
    assert ledger.consumed_micros(day_key="1970-01-01", month_key="1970-01") == (
        300_000,
        300_000,
    )
    repository.admit(
        _row("op-rec-0002", client_request_id="client-0008"),
        _reservation("op-rec-0002", daily=800_000),
    )
    assert repository.active_slot_operation_id() == "op-rec-0002"


def test_reconcile_unknown_consumes_the_reservation(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    ledger = SqliteResearchBudgetLedger(engine)
    repository.admit(
        _row("op-unknown-0001", client_request_id="client-0009"),
        _reservation("op-unknown-0001"),
    )
    hold = ledger.reconcile(
        BudgetReconciliation(
            operation_id="op-unknown-0001",
            usage=None,
            calculated_cost_usd_micros=None,
            state=ResearchAccountingState.UNKNOWN,
        )
    )
    assert hold.state is ResearchAccountingState.UNKNOWN
    assert ledger.consumed_micros(day_key="1970-01-01", month_key="1970-01") == (
        RESERVATION_MICROS,
        RESERVATION_MICROS,
    )


def test_claim_submission_has_one_winner(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    repository.admit(
        _row("op-claim-0001", client_request_id="client-claim"),
        _reservation("op-claim-0001"),
    )
    first = repository.claim_submission("op-claim-0001")
    assert first is not None
    assert first.record.state is ResearchLifecycleState.SUBMITTING
    assert repository.claim_submission("op-claim-0001") is None
    assert repository.claim_submission("op-missing") is None


def test_save_persists_checkpoint_handle_and_record_binding(engine) -> None:
    repository = SqliteResearchRequestRepository(engine)
    first = repository.admit(
        _row("op-save-0001", client_request_id="client-0010"),
        _reservation("op-save-0001"),
    )
    running = _record("op-save-0001", state=ResearchLifecycleState.RUNNING)
    running = ResearchRequestRecord(
        operation_id=running.operation_id,
        kind=running.kind,
        prompt=running.prompt,
        profile=running.profile,
        deadline_seconds=running.deadline_seconds,
        resource_limits=running.resource_limits,
        state=running.state,
        cleanup_state=ResearchRemoteCleanupState.PENDING,
        accounting_state=ResearchAccountingState.RESERVED,
        remote_handle=ProviderHandle("remote-handle-1"),
        error_code=None,
    )
    checkpoint = '{"answer":"partial"}'
    updated = ResearchRequestRow(
        record=running,
        owner_login_key=first.owner_login_key,
        client_request_id=first.client_request_id,
        request_fingerprint=first.request_fingerprint,
        checkpoint_json=checkpoint,
        checkpoint_sha256="d" * 64,
        record_id=None,
        created_at_ms=first.created_at_ms,
        admitted_at_ms=first.admitted_at_ms,
        submitted_at_ms=1_100,
        finished_at_ms=None,
        updated_at_ms=1_200,
        cancel_requested_at_ms=None,
        cancellation_confirmed_at_ms=None,
    )
    repository.save(updated)
    fetched = repository.get_request("op-save-0001")
    assert fetched is not None
    assert fetched.record.state is ResearchLifecycleState.RUNNING
    assert fetched.record.cleanup_state is ResearchRemoteCleanupState.PENDING
    assert fetched.record.remote_handle is not None
    assert fetched.record.remote_handle.value == "remote-handle-1"
    assert fetched.checkpoint_json == checkpoint
    assert fetched.checkpoint_sha256 == "d" * 64
    assert fetched.submitted_at_ms == 1_100
    assert repository.active_slot_operation_id() == "op-save-0001"
