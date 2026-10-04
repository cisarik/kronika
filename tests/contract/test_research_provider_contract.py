"""Contract tests for provider-neutral research ports and typed outcomes."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from kronika.infrastructure.ai.transport import HttpsJsonResponse

from kronika.application.ports.research import (
    ResearchBudgetLedger,
    ResearchProvider,
    ResearchRequestRepository,
    ResearchResultCompletion,
)
from kronika.domain.research import (
    MICRO_USD_SCALE,
    NONTERMINAL_RESEARCH_LIFECYCLE_STATES,
    RESEARCH_ERROR_CODES,
    TERMINAL_RESEARCH_LIFECYCLE_STATES,
    ApprovedResourceLimits,
    BudgetHold,
    BudgetReconciliation,
    BudgetReservation,
    CleanupOutcome,
    CompletionEvidence,
    ProviderAvailability,
    ProviderHandle,
    ProviderObservation,
    ProviderObservationKind,
    ProviderRequest,
    ResearchAccountingState,
    ResearchAnswer,
    ResearchCitation,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ResearchRequestRecord,
    ResearchUsage,
    ResearchValueError,
    ResultCompletion,
    ResultCompletionReceipt,
    ServerSelectedProfile,
    SubmissionIdempotency,
    UsagePriceSchedule,
    usage_cost_micro_usd,
)
from kronika.infrastructure.ai.research_configuration import default_research_configuration
from kronika.infrastructure.ai.research_registry import select_research_provider

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "alembic",
        "fastapi",
        "httpx",
        "kronika_capture",
        "openai",
        "pydantic",
        "requests",
        "sqlalchemy",
        "starlette",
        "urllib",
        "uvicorn",
    }
)


def _profile() -> ServerSelectedProfile:
    snapshot = select_research_provider(
        default_research_configuration(enabled=True),
        kind=ResearchOperationKind.SEARCH,
    )
    return snapshot.profile


def _limits() -> ApprovedResourceLimits:
    return ApprovedResourceLimits(
        max_tool_calls=3,
        max_output_tokens=4096,
        budget_reservation_usd_micros=500_000,
        prompt_max_utf8_bytes=16384,
        answer_max_utf8_bytes=2097152,
        citation_count_max=200,
    )


def _usage() -> ResearchUsage:
    return ResearchUsage(
        input_tokens=20,
        cached_input_tokens=4,
        output_tokens=10,
        reasoning_tokens=3,
        web_tool_calls=1,
    )


def _answer() -> ResearchAnswer:
    return ResearchAnswer(
        text="A complete markdown answer.",
        citations=(ResearchCitation(url="https://example.test/source", title="Source"),),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        usage=_usage(),
    )


def _observation(kind: ProviderObservationKind) -> ProviderObservation:
    handle = ProviderHandle("remote-1")
    if kind is ProviderObservationKind.COMPLETE:
        return ProviderObservation(kind=kind, remote_handle=handle, answer=_answer())
    if kind in {ProviderObservationKind.PENDING, ProviderObservationKind.RUNNING}:
        return ProviderObservation(kind=kind, remote_handle=handle)
    code = {
        ProviderObservationKind.REFUSED: ResearchErrorCode.REFUSED,
        ProviderObservationKind.FAILED: ResearchErrorCode.PROVIDER_UNAVAILABLE,
        ProviderObservationKind.CANCELLED: ResearchErrorCode.CANCELLED,
        ProviderObservationKind.UNCERTAIN: ResearchErrorCode.SUBMISSION_UNKNOWN,
    }[kind]
    return ProviderObservation(kind=kind, remote_handle=handle, error_code=code)


class FakeResearchProvider:
    """Deterministic in-test provider. It is not a production adapter."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def describe(self):
        snapshot = select_research_provider(
            default_research_configuration(enabled=True),
            kind=ResearchOperationKind.RESEARCH,
        )
        return snapshot.descriptor

    def submit(self, request: ProviderRequest) -> ProviderObservation:
        self.calls.append("submit")
        assert request.operation_id == "op-1"
        return _observation(ProviderObservationKind.PENDING)

    def poll(self, handle: ProviderHandle) -> ProviderObservation:
        self.calls.append("poll")
        assert handle.value == "remote-1"
        return _observation(ProviderObservationKind.COMPLETE)

    def cancel(self, handle: ProviderHandle) -> ProviderObservation:
        self.calls.append("cancel")
        assert handle.value == "remote-1"
        return _observation(ProviderObservationKind.CANCELLED)

    def release_remote(self, handle: ProviderHandle) -> CleanupOutcome:
        self.calls.append("release_remote")
        assert handle.value == "remote-1"
        return CleanupOutcome(state=ResearchRemoteCleanupState.DELETED)


class FakeRequestRepository:
    def __init__(self) -> None:
        self.records: dict[str, ResearchRequestRecord] = {}

    def get(self, operation_id: str) -> ResearchRequestRecord | None:
        return self.records.get(operation_id)

    def admit(self, record: ResearchRequestRecord) -> ResearchRequestRecord:
        self.records[record.operation_id] = record
        return record

    def save(self, record: ResearchRequestRecord) -> ResearchRequestRecord:
        self.records[record.operation_id] = record
        return record


class FakeBudgetLedger:
    def reserve(self, reservation: BudgetReservation) -> BudgetHold:
        return BudgetHold(
            operation_id=reservation.operation_id,
            reserved_usd_micros=reservation.reserved_usd_micros,
            state=ResearchAccountingState.RESERVED,
        )

    def reconcile(self, reconciliation: BudgetReconciliation) -> BudgetHold:
        assert reconciliation.state is ResearchAccountingState.RECONCILED
        assert reconciliation.usage is not None
        assert reconciliation.calculated_cost_usd_micros is not None
        return BudgetHold(
            operation_id=reconciliation.operation_id,
            reserved_usd_micros=500_000,
            state=reconciliation.state,
        )


class FakeResultCompletion:
    def complete(self, completion: ResultCompletion) -> ResultCompletionReceipt:
        return ResultCompletionReceipt(
            operation_id=completion.operation_id,
            state=ResearchLifecycleState.SAVED,
        )


def test_fake_provider_exercises_every_port_method_and_outcome() -> None:
    provider = FakeResearchProvider()
    requests = FakeRequestRepository()
    ledger = FakeBudgetLedger()
    completion_port = FakeResultCompletion()
    profile = _profile()
    limits = _limits()
    request = ProviderRequest(
        operation_id="op-1",
        kind=ResearchOperationKind.SEARCH,
        prompt="What happened?",
        profile=profile,
        deadline_seconds=profile.deadline_seconds,
        resource_limits=limits,
    )
    record = ResearchRequestRecord(
        operation_id="op-1",
        kind=request.kind,
        prompt=request.prompt,
        profile=profile,
        deadline_seconds=request.deadline_seconds,
        resource_limits=limits,
        state=ResearchLifecycleState.ADMITTED,
        cleanup_state=ResearchRemoteCleanupState.NOT_REQUIRED,
        accounting_state=ResearchAccountingState.RESERVED,
        remote_handle=None,
        error_code=None,
    )

    assert isinstance(provider, ResearchProvider)
    assert isinstance(requests, ResearchRequestRepository)
    assert isinstance(ledger, ResearchBudgetLedger)
    assert isinstance(completion_port, ResearchResultCompletion)

    descriptor = provider.describe()
    pending = provider.submit(request)
    complete = provider.poll(ProviderHandle("remote-1"))
    cancelled = provider.cancel(ProviderHandle("remote-1"))
    cleanup = provider.release_remote(ProviderHandle("remote-1"))
    admitted = requests.admit(record)
    assert requests.get("op-1") == admitted
    saved_record = requests.save(
        ResearchRequestRecord(
            operation_id=record.operation_id,
            kind=record.kind,
            prompt=record.prompt,
            profile=record.profile,
            deadline_seconds=record.deadline_seconds,
            resource_limits=record.resource_limits,
            state=ResearchLifecycleState.SAVED,
            cleanup_state=ResearchRemoteCleanupState.DELETED,
            accounting_state=ResearchAccountingState.RECONCILED,
            remote_handle=ProviderHandle("remote-1"),
            error_code=None,
        )
    )
    hold = ledger.reserve(
        BudgetReservation(
            operation_id="op-1",
            kind=ResearchOperationKind.SEARCH,
            reserved_usd_micros=500_000,
            daily_limit_usd_micros=10_000_000,
            monthly_limit_usd_micros=30_000_000,
        )
    )
    reconciled = ledger.reconcile(
        BudgetReconciliation(
            operation_id="op-1",
            usage=_usage(),
            calculated_cost_usd_micros=1,
            state=ResearchAccountingState.RECONCILED,
        )
    )
    receipt = completion_port.complete(
        ResultCompletion(operation_id="op-1", answer=_answer(), remote_handle=ProviderHandle("remote-1"))
    )

    assert provider.calls == ["submit", "poll", "cancel", "release_remote"]
    assert descriptor.availability is ProviderAvailability.UNCONFIGURED
    assert descriptor.submission_idempotency is SubmissionIdempotency.NOT_GUARANTEED
    assert pending.kind is ProviderObservationKind.PENDING
    assert complete.kind is ProviderObservationKind.COMPLETE
    assert complete.answer is not None
    assert complete.answer.text == "A complete markdown answer."
    assert complete.remote_handle is not None
    assert cancelled.error_code is ResearchErrorCode.CANCELLED
    assert cleanup.state is ResearchRemoteCleanupState.DELETED
    assert saved_record.state is ResearchLifecycleState.SAVED
    assert hold.state is ResearchAccountingState.RESERVED
    assert reconciled.state is ResearchAccountingState.RECONCILED
    assert receipt.state is ResearchLifecycleState.SAVED
    for kind in ProviderObservationKind:
        assert _observation(kind).kind is kind


def test_typed_error_codes_and_lifecycle_partition_are_stable() -> None:
    assert RESEARCH_ERROR_CODES == {
        "E_DISABLED",
        "E_NOT_CONFIGURED",
        "E_CAPABILITY_UNAVAILABLE",
        "E_INVALID_REQUEST",
        "E_BUSY",
        "E_IDEMPOTENCY_CONFLICT",
        "E_AUTH",
        "E_RATE_LIMIT",
        "E_PROVIDER_QUOTA",
        "E_PROVIDER_UNAVAILABLE",
        "E_REFUSED",
        "E_INVALID_RESULT",
        "E_INCOMPLETE_RESULT",
        "E_RESULT_TOO_LARGE",
        "E_NO_WEB_EVIDENCE",
        "E_TIMEOUT",
        "E_CANCELLED",
        "E_SUBMISSION_UNKNOWN",
        "E_RESULT_EXPIRED",
        "E_ACCOUNTING_UNKNOWN",
        "E_BUDGET_EXCEEDED",
        "E_STORAGE",
    }
    assert set(ResearchLifecycleState) == (
        NONTERMINAL_RESEARCH_LIFECYCLE_STATES | TERMINAL_RESEARCH_LIFECYCLE_STATES
    )
    assert NONTERMINAL_RESEARCH_LIFECYCLE_STATES.isdisjoint(TERMINAL_RESEARCH_LIFECYCLE_STATES)
    assert ResearchLifecycleState.SUBMISSION_UNKNOWN in TERMINAL_RESEARCH_LIFECYCLE_STATES
    assert ResearchRemoteCleanupState.DELETED.value == "deleted"
    assert ResearchAccountingState.UNKNOWN.value == "unknown"
    with pytest.raises(ResearchValueError):
        BudgetReconciliation(
            operation_id="op-1",
            usage=None,
            calculated_cost_usd_micros=0,
            state=ResearchAccountingState.UNKNOWN,
        )


def test_micro_usd_arithmetic_does_not_double_count_cached_or_reasoning_tokens() -> None:
    schedule = UsagePriceSchedule(
        input_micro_usd_per_million=5 * MICRO_USD_SCALE,
        cached_input_micro_usd_per_million=MICRO_USD_SCALE // 2,
        output_micro_usd_per_million=30 * MICRO_USD_SCALE,
        web_search_micro_usd_per_thousand=10 * MICRO_USD_SCALE,
    )
    usage = ResearchUsage(
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        output_tokens=1_000_000,
        reasoning_tokens=1_000_000,
        web_tool_calls=1,
    )

    cost = usage_cost_micro_usd(usage, schedule)

    assert cost == (MICRO_USD_SCALE // 2) + (30 * MICRO_USD_SCALE) + 10_000


def test_ports_do_not_accept_client_endpoint_model_or_tool_fields() -> None:
    request_fields = set(ProviderRequest.__dataclass_fields__)
    assert request_fields.isdisjoint(
        {"endpoint", "model", "model_id", "tools", "tool", "owner", "owner_id", "api_key", "html"}
    )
    assert "model_id" in ServerSelectedProfile.__dataclass_fields__
    assert "html" not in ResearchAnswer.__dataclass_fields__
    assert "html" not in ProviderObservation.__dataclass_fields__
    submit = inspect.signature(ResearchProvider.submit)
    assert list(submit.parameters) == ["self", "request"]
    select = inspect.signature(select_research_provider)
    assert set(select.parameters) == {"config", "kind"}
    registry_source = (
        REPOSITORY_ROOT / "src" / "kronika" / "infrastructure" / "ai" / "research_registry.py"
    ).read_text(encoding="utf-8")
    assert "importlib" not in registry_source
    assert "entry_points" not in registry_source


def test_domain_and_port_modules_keep_import_boundaries() -> None:
    paths = (
        REPOSITORY_ROOT / "src" / "kronika" / "domain" / "research.py",
        REPOSITORY_ROOT / "src" / "kronika" / "application" / "ports" / "research.py",
    )
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if root in FORBIDDEN_IMPORT_ROOTS or module.startswith("kronika.infrastructure"):
                    violations.append(f"{path.name}: {module}")
                if path.name == "research.py" and path.parent.name == "ports":
                    if module.startswith("framenest.") and module != "kronika.domain.research":
                        violations.append(f"{path.name}: {module}")
    assert violations == []


def test_research_runtime_wiring_is_inert_when_disabled() -> None:
    from kronika.adapters.api.application import build_research_runtime
    from kronika.infrastructure.ai.research_configuration import (
        default_research_configuration,
    )

    assert build_research_runtime(engine=None, configuration=None) is None
    assert (
        build_research_runtime(
            engine=None,
            configuration=default_research_configuration(enabled=False),
        )
        is None
    )


def test_research_runtime_wiring_builds_offline_objects_when_enabled() -> None:
    from kronika.adapters.api.application import build_research_runtime
    from kronika.infrastructure.ai.research_configuration import (
        default_research_configuration,
    )

    class FakeTransport:
        def post_json(self, url, *, headers, body, max_request_bytes):
            raise AssertionError("no network during construction")

        def get_json(self, url, *, headers):
            raise AssertionError("no network during construction")

        def delete_json(self, url, *, headers):
            raise AssertionError("no network during construction")

    runtime = build_research_runtime(
        engine=object(),
        configuration=default_research_configuration(enabled=True),
        transport=FakeTransport(),
        recover=False,
    )
    assert runtime is not None
    descriptor = runtime._provider.describe()
    assert descriptor.provider_id == "openai-responses"


def _migrate_synthetic_catalog(database_path: Path) -> None:
    from alembic import command

    from kronika.infrastructure.persistence.migrations import _alembic_config
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )

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


def test_production_runtime_reconciles_completed_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completed usage through build_research_runtime becomes a reconciled hold.

    Cached input and reasoning tokens are present so a double-counted cache or
    an added reasoning charge cannot match usage_cost_micro_usd.
    """
    import json
    from datetime import UTC, datetime

    from kronika.infrastructure.ai.transport import HttpsJsonResponse
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.research_budget_repository import (
        SqliteResearchBudgetLedger,
    )

    usage = ResearchUsage(
        input_tokens=1_000_000,
        cached_input_tokens=200_000,
        output_tokens=10_000,
        reasoning_tokens=4_000,
        web_tool_calls=1,
    )

    class RecordingTransport:
        def __init__(self) -> None:
            self.posts: list[str] = []
            self.gets: list[str] = []
            self.deletes: list[str] = []

        def post_json(self, url, *, headers, body, max_request_bytes):
            self.posts.append(url)
            del headers, body, max_request_bytes
            return HttpsJsonResponse(
                status_code=200,
                body=json.dumps(
                    {"id": "resp-price-1", "status": "queued"}
                ).encode("utf-8"),
                content_type="application/json",
            )

        def get_json(self, url, *, headers):
            self.gets.append(url)
            del headers
            payload = {
                "id": "resp-price-1",
                "status": "completed",
                "output": [
                    {"type": "web_search_call", "id": "ws-1"},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Synthetic reconciled answer.",
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
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "input_tokens_details": {
                        "cached_tokens": usage.cached_input_tokens
                    },
                    "output_tokens_details": {
                        "reasoning_tokens": usage.reasoning_tokens
                    },
                },
            }
            return HttpsJsonResponse(
                status_code=200,
                body=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
            )

        def delete_json(self, url, *, headers):
            self.deletes.append(url)
            del headers
            raise AssertionError("completion does not release the remote response")

    # Synthetic supplier only. The production adapter refuses to call the
    # transport when no key is present; this does not read a credential.
    monkeypatch.setattr(
        "kronika.adapters.api.application._research_credential_key",
        lambda identifier: "synthetic-test-key",
    )
    database = tmp_path / "research-price.sqlite3"
    _migrate_synthetic_catalog(database)
    engine = create_sqlite_engine(database)
    transport = RecordingTransport()
    try:
        from kronika.adapters.api.application import build_research_runtime
        from kronika.infrastructure.ai.research_configuration import (
            default_research_configuration,
        )

        runtime = build_research_runtime(
            engine=engine,
            configuration=default_research_configuration(enabled=True),
            transport=transport,
            recover=False,
        )
        assert runtime is not None
        assert transport.posts == [] and transport.gets == [] and transport.deletes == []
        runtime.admit(
            owner_login_key="alice@example.com",
            client_request_id="client-price-0001",
            kind=ResearchOperationKind.SEARCH,
            prompt="What is the synthetic question?",
        )
        submitted = runtime.submit_pending()
        assert submitted is not None
        assert submitted.record.state is ResearchLifecycleState.RUNNING
        saved = runtime.poll_once()
        assert saved is not None
        assert saved.record.state is ResearchLifecycleState.SAVED
        assert saved.record.accounting_state is ResearchAccountingState.RECONCILED
        assert transport.posts == ["https://api.openai.com/v1/responses"]
        assert transport.gets == ["https://api.openai.com/v1/responses/resp-price-1"]
        assert transport.deletes == []

        from kronika.infrastructure.ai.research_models import (
            RESEARCH_ADMISSION_PROFILE_VERSION,
            resolve_usage_price_schedule,
        )

        schedule = resolve_usage_price_schedule(
            "openai-responses",
            "gpt-5.5-2026-04-23",
            RESEARCH_ADMISSION_PROFILE_VERSION,
        )
        assert schedule is not None
        expected = usage_cost_micro_usd(usage, schedule)
        assert expected != 500_000
        moment = datetime.now(UTC)
        consumed = SqliteResearchBudgetLedger(engine).consumed_micros(
            day_key=moment.strftime("%Y-%m-%d"),
            month_key=moment.strftime("%Y-%m"),
        )
        assert consumed == (expected, expected)
    finally:
        dispose_engine(engine)


class _SequenceTransport:
    """Fake transport that hands out one completed response per poll."""

    def __init__(self, usage: dict[str, object]) -> None:
        self._usage = usage
        self.posts: list[str] = []
        self.gets: list[str] = []
        self.deletes: list[str] = []

    def post_json(self, url, *, headers, body, max_request_bytes):
        self.posts.append(url)
        return HttpsJsonResponse(
            status_code=200,
            body=json.dumps(
                {"id": f"resp-{len(self.posts)}", "status": "queued"}
            ).encode("utf-8"),
            content_type="application/json",
        )

    def get_json(self, url, *, headers):
        self.gets.append(url)
        payload = {
            "id": url.rsplit("/", 1)[-1],
            "status": "completed",
            "output": [
                {"type": "web_search_call", "id": "ws-1"},
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Synthetic refreshed answer.",
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
            "usage": self._usage,
        }
        return HttpsJsonResponse(
            status_code=200,
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )

    def delete_json(self, url, *, headers):
        self.deletes.append(url)
        return HttpsJsonResponse(status_code=204, body=b"")


def test_disabled_start_enables_without_restart_and_keeps_admitted_pricing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled-start process activates, and admitted pricing is immutable.

    This is a guard regression for behavior that already exists on the
    candidate: the runtime is built while research is disabled, admission is
    refused, the same process then admits after the saved configuration is
    enabled, a later model change reaches only new admissions, and the first
    request keeps its persisted model, schedule and reconciled cost across a
    simulated restart.
    """
    import json
    from dataclasses import replace
    from datetime import UTC, datetime

    from kronika.adapters.api.application import build_research_runtime
    from kronika.application.ports.research import ResearchStoreError
    from kronika.infrastructure.ai.research_models import (
        RESEARCH_ADMISSION_PROFILE_VERSION,
        resolve_usage_price_schedule,
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

    usage_payload = {
        "input_tokens": 1_000,
        "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
        "output_tokens_details": {"reasoning_tokens": 10},
    }
    # Synthetic supplier only; no credential is read.
    monkeypatch.setattr(
        "kronika.adapters.api.application._research_credential_key",
        lambda identifier: "synthetic-test-key",
    )

    database = tmp_path / "research-refresh.sqlite3"
    _migrate_synthetic_catalog(database)
    engine = create_sqlite_engine(database)
    transport = _SequenceTransport(usage_payload)
    configuration = {"current": default_research_configuration(enabled=False)}
    try:
        # A persistent coordinator exists even though research starts disabled.
        runtime = build_research_runtime(
            engine=engine,
            configuration_provider=lambda: configuration["current"],
            transport=transport,
            recover=False,
        )
        assert runtime is not None
        assert transport.posts == [] and transport.gets == []

        # A disabled start refuses new admission without a restart.
        with pytest.raises(ResearchStoreError) as disabled:
            runtime.admit(
                owner_login_key="alice@example.com",
                client_request_id="client-refresh-0",
                kind=ResearchOperationKind.SEARCH,
                prompt="What is the synthetic question?",
            )
        assert disabled.value.code is ResearchErrorCode.DISABLED

        # The administrator enables the first model; no restart occurs.
        configuration["current"] = replace(
            default_research_configuration(enabled=True),
            model_id="gpt-5.6-luna",
        )
        first = runtime.admit(
            owner_login_key="alice@example.com",
            client_request_id="client-refresh-1",
            kind=ResearchOperationKind.SEARCH,
            prompt="What is the synthetic question?",
            consent_version="2026-09",
        )
        assert first.newly_admitted is True
        assert first.row.record.profile.model_id == "gpt-5.6-luna"
        assert runtime.submit_pending() is not None
        saved = runtime.poll_once()
        assert saved is not None
        assert saved.record.state is ResearchLifecycleState.SAVED
        assert saved.record.accounting_state is ResearchAccountingState.RECONCILED

        admitted_usage = ResearchUsage(
            input_tokens=1_000,
            cached_input_tokens=200,
            output_tokens=100,
            reasoning_tokens=10,
            web_tool_calls=1,
            cache_write_input_tokens=100,
        )
        luna_schedule = resolve_usage_price_schedule(
            "openai-responses",
            "gpt-5.6-luna",
            RESEARCH_ADMISSION_PROFILE_VERSION,
        )
        assert luna_schedule is not None
        expected = usage_cost_micro_usd(admitted_usage, luna_schedule)

        # A saved model change reaches the next admission only.
        configuration["current"] = replace(
            configuration["current"],
            model_id="gpt-5.5-2026-04-23",
        )
        second = runtime.admit(
            owner_login_key="alice@example.com",
            client_request_id="client-refresh-2",
            kind=ResearchOperationKind.SEARCH,
            prompt="A second synthetic question?",
            consent_version="2026-09",
        )
        assert second.row.record.profile.model_id == "gpt-5.5-2026-04-23"

        repository = SqliteResearchRequestRepository(engine)
        stored_first = repository.get_request(first.row.record.operation_id)
        assert stored_first is not None
        assert stored_first.record.profile.model_id == "gpt-5.6-luna"
        assert stored_first.record.accounting_state is ResearchAccountingState.RECONCILED

        # A restart keeps pricing bound to the persisted request identity.
        restarted = build_research_runtime(
            engine=engine,
            configuration_provider=lambda: configuration["current"],
            transport=transport,
        )
        assert restarted is not None
        after_restart = repository.get_request(first.row.record.operation_id)
        assert after_restart is not None
        assert after_restart.record.profile.model_id == "gpt-5.6-luna"
        assert after_restart.record.profile.configuration_version == (
            RESEARCH_ADMISSION_PROFILE_VERSION
        )
        assert after_restart.record.accounting_state is ResearchAccountingState.RECONCILED

        resolved = resolve_usage_price_schedule(
            after_restart.record.profile.provider_id,
            after_restart.record.profile.model_id,
            after_restart.record.profile.configuration_version,
        )
        assert resolved == luna_schedule
        moment = datetime.now(UTC)
        consumed = SqliteResearchBudgetLedger(engine).consumed_micros(
            day_key=moment.strftime("%Y-%m-%d"),
            month_key=moment.strftime("%Y-%m"),
        )
        # The first request's reservation was reconciled to its Luna cost and
        # the second admission is still reserved, so the day total is the sum.
        assert consumed == (
            expected + second.row.record.profile.budget_reservation_usd_micros,
            expected + second.row.record.profile.budget_reservation_usd_micros,
        )
    finally:
        dispose_engine(engine)
