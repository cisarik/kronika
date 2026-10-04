"""Atomic research completion evidence: document, record and request binding."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.application.ports.research import ResearchStoreError
from kronika.domain.research import (
    ApprovedResourceLimits,
    CompletionEvidence,
    FIXED_OPENAI_RESPONSES_MODEL_ID,
    OPENAI_RESPONSES_PROVIDER_ID,
    ProviderHandle,
    ResearchAnswer,
    ResearchCitation,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ResearchRequestRecord,
    ResearchUsage,
    ResultCompletion,
    ServerSelectedProfile,
    WEB_SEARCH_TOOL,
)
from kronika.domain.research import ResearchAccountingState
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.record_repository import (
    SqliteResearchResultCompletion,
)
from kronika.infrastructure.persistence.research_budget_repository import (
    SqliteResearchBudgetLedger,
)
from kronika.infrastructure.persistence.research_request_repository import (
    SqliteResearchRequestRepository,
)

REQUEST_ID = "op-completion-0001"
FINGERPRINT = "e" * 64


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
def database(tmp_path: Path):
    path = tmp_path / "completion.sqlite3"
    _migrate(path)
    engine = create_sqlite_engine(path)
    try:
        yield path, engine
    finally:
        dispose_engine(engine)


def _row(
    operation_id: str = REQUEST_ID,
    *,
    client_request_id: str = "client-completion-1",
    kind: ResearchOperationKind = ResearchOperationKind.SEARCH,
    prompt: str = "What is the synthetic question?",
):
    from kronika.application.ports.research import ResearchRequestRow

    profile = ServerSelectedProfile(
        provider_id=OPENAI_RESPONSES_PROVIDER_ID,
        model_id=FIXED_OPENAI_RESPONSES_MODEL_ID,
        configuration_version="3",
        reasoning_effort="low",
        tool_allowlist=(WEB_SEARCH_TOOL,),
        background=True,
        max_tool_calls=3,
        max_output_tokens=4096,
        deadline_seconds=180,
        budget_reservation_usd_micros=500_000,
    )
    limits = ApprovedResourceLimits(
        max_tool_calls=3,
        max_output_tokens=4096,
        budget_reservation_usd_micros=500_000,
        prompt_max_utf8_bytes=16_384,
        answer_max_utf8_bytes=2_097_152,
        citation_count_max=200,
    )
    record = ResearchRequestRecord(
        operation_id=operation_id,
        kind=kind,
        prompt=prompt,
        profile=profile,
        deadline_seconds=180,
        resource_limits=limits,
        state=ResearchLifecycleState.RUNNING,
        cleanup_state=ResearchRemoteCleanupState.PENDING,
        accounting_state=ResearchAccountingState.RESERVED,
        remote_handle=ProviderHandle("remote-handle-completion"),
        error_code=None,
    )
    return ResearchRequestRow(
        record=record,
        owner_login_key="alice@example.com",
        client_request_id=client_request_id,
        request_fingerprint=FINGERPRINT,
        checkpoint_json=None,
        checkpoint_sha256=None,
        record_id=None,
        created_at_ms=1_000,
        admitted_at_ms=1_001,
        submitted_at_ms=1_002,
        finished_at_ms=None,
        updated_at_ms=1_003,
        cancel_requested_at_ms=None,
        cancellation_confirmed_at_ms=None,
    )


def _admit(engine, row=None):
    from kronika.domain.research import BudgetReservation

    repository = SqliteResearchRequestRepository(engine)
    row = row or _row()
    reservation = BudgetReservation(
        operation_id=row.record.operation_id,
        kind=row.record.kind,
        reserved_usd_micros=500_000,
        daily_limit_usd_micros=10_000_000,
        monthly_limit_usd_micros=30_000_000,
    )
    return repository.admit(row, reservation)


def _completion(operation_id: str = REQUEST_ID) -> ResultCompletion:
    return ResultCompletion(
        operation_id=operation_id,
        answer=ResearchAnswer(
            text="Synthetic completed answer.",
            citations=(
                ResearchCitation(url="https://example.invalid/a", title="A"),
            ),
            evidence=CompletionEvidence(
                provider_terminal=True,
                answer_complete=True,
                web_search_executed=True,
                refusal_marker=False,
                incomplete_marker=False,
            ),
            usage=ResearchUsage(
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=50,
                reasoning_tokens=10,
                web_tool_calls=1,
            ),
        ),
        remote_handle=ProviderHandle("remote-handle-completion"),
    )


def _counts(database_path: Path) -> tuple[int, int]:
    connection = sqlite3.connect(database_path)
    try:
        documents = connection.execute(
            "SELECT COUNT(*) FROM kronika_documents"
        ).fetchone()[0]
        records = connection.execute("SELECT COUNT(*) FROM kronika_records").fetchone()[0]
        return int(documents), int(records)
    finally:
        connection.close()


def test_complete_creates_document_record_and_binding(database) -> None:
    path, engine = database
    stored = _admit(engine)
    receipt = SqliteResearchResultCompletion(engine).complete(_completion())
    assert receipt.state is ResearchLifecycleState.SAVED
    connection = sqlite3.connect(path)
    try:
        document = connection.execute(
            """
            SELECT operation_id, kind, question_text, answer_text, citations_json,
                   completion_evidence_json
            FROM kronika_documents
            """
        ).fetchone()
        record = connection.execute(
            """
            SELECT id, kind, owner_login_key, visibility, document_id, final_operation_id
            FROM kronika_records
            """
        ).fetchone()
        binding = connection.execute(
            "SELECT record_id FROM research_requests WHERE operation_id = ?",
            (REQUEST_ID,),
        ).fetchone()
    finally:
        connection.close()
    assert document is not None and record is not None and binding is not None
    assert document[0] == REQUEST_ID
    assert document[1] == "search"
    assert document[2] == "What is the synthetic question?"
    assert document[3] == "Synthetic completed answer."
    assert "https://example.invalid/a" in document[4]
    assert '"web_search_executed":true' in document[5]
    assert record[0] is not None
    assert record[1] == "search"
    assert record[2] == "alice@example.com"
    assert record[3] == "private"
    assert record[4] is not None
    assert record[5] == REQUEST_ID
    assert binding[0] == record[0]
    assert stored.record.operation_id == REQUEST_ID


def test_exact_replay_returns_existing_binding(database) -> None:
    path, engine = database
    _admit(engine)
    completion = SqliteResearchResultCompletion(engine)
    first = completion.complete(_completion())
    before = _counts(path)
    second = completion.complete(_completion())
    after = _counts(path)
    assert first.state is second.state is ResearchLifecycleState.SAVED
    assert before == after == (1, 1)


def test_unknown_request_is_refused(database) -> None:
    _, engine = database
    with pytest.raises(ResearchStoreError) as caught:
        SqliteResearchResultCompletion(engine).complete(
            _completion(operation_id="op-completion-missing")
        )
    assert caught.value.code is ResearchErrorCode.STORAGE


def test_coordinator_with_real_completion_binds_the_record(database) -> None:
    path, engine = database
    from kronika.application.research import ResearchCoordinator
    from kronika.infrastructure.ai.research_configuration import (
        default_research_configuration,
    )
    from kronika.infrastructure.ai.research_registry import (
        select_research_provider,
    )

    class FakeProvider:
        def describe(self):
            raise AssertionError("not used")

        def submit(self, request):
            from kronika.domain.research import ProviderObservation
            from kronika.domain.research import ProviderObservationKind

            return ProviderObservation(
                kind=ProviderObservationKind.RUNNING,
                remote_handle=ProviderHandle("remote-handle-completion"),
            )

        def poll(self, handle):
            from kronika.domain.research import ProviderObservation
            from kronika.domain.research import ProviderObservationKind

            return ProviderObservation(
                kind=ProviderObservationKind.COMPLETE,
                remote_handle=handle,
                answer=_completion().answer,
            )

        def cancel(self, handle):
            raise AssertionError("not used")

        def release_remote(self, handle):
            raise AssertionError("not used")

    now = [2_000_000]
    coordinator = ResearchCoordinator(
        provider=FakeProvider(),
        requests=SqliteResearchRequestRepository(engine, clock_ms=lambda: now[0]),
        ledger=SqliteResearchBudgetLedger(engine, clock_ms=lambda: now[0]),
        completion=SqliteResearchResultCompletion(engine, clock_ms=lambda: now[0]),
        select=lambda kind: select_research_provider(
            default_research_configuration(enabled=True), kind=kind
        ),
        clock_ms=lambda: now[0],
        new_operation_id=lambda: "op-coord-completion",
    )
    admitted = coordinator.admit(
        owner_login_key="alice@example.com",
        client_request_id="client-coord-completion",
        kind=ResearchOperationKind.SEARCH,
        prompt="What is the synthetic question?",
    ).row
    coordinator.submit_pending()
    saved = coordinator.poll_once()
    assert saved is not None
    assert saved.record.state is ResearchLifecycleState.SAVED
    assert saved.record_id is not None
    connection = sqlite3.connect(path)
    try:
        document = connection.execute(
            "SELECT operation_id FROM kronika_documents"
        ).fetchone()
        record = connection.execute(
            "SELECT id, owner_login_key FROM kronika_records"
        ).fetchone()
    finally:
        connection.close()
    assert document == ("op-coord-completion",)
    assert record is not None and record[0] == saved.record_id
    assert record[1] == "alice@example.com"
    assert admitted.record.operation_id == "op-coord-completion"
