"""Application ports for provider-neutral research.

Protocols only. They import domain values and no HTTP, database, SDK, or
capture modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from kronika.domain.research import (
    ApprovedResourceLimits,
    BudgetHold,
    BudgetReconciliation,
    BudgetReservation,
    CleanupOutcome,
    ProviderDescriptor,
    ProviderHandle,
    ProviderObservation,
    ProviderRequest,
    ResearchAccountingState,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ResearchRequestRecord,
    ResultCompletion,
    ResultCompletionReceipt,
    ServerSelectedProfile,
)


@runtime_checkable
class ResearchProvider(Protocol):
    """One research adapter. Selection and limits arrive already snapshotted."""

    def describe(self) -> ProviderDescriptor:
        """Return the network-free provider descriptor."""

    def submit(self, request: ProviderRequest) -> ProviderObservation:
        """Submit one bounded generation. There is no client endpoint or model."""

    def poll(self, handle: ProviderHandle) -> ProviderObservation:
        """Read one already submitted remote operation."""

    def cancel(self, handle: ProviderHandle) -> ProviderObservation:
        """Request cancellation of one known remote operation."""

    def release_remote(self, handle: ProviderHandle) -> CleanupOutcome:
        """Delete one remote response after local reconciliation."""


@runtime_checkable
class ResearchRequestRepository(Protocol):
    """Durable request records. Owner identity is not part of this port."""

    def get(self, operation_id: str) -> ResearchRequestRecord | None:
        """Return one request, or none when it is absent."""

    def admit(self, record: ResearchRequestRecord) -> ResearchRequestRecord:
        """Persist one newly admitted request."""

    def save(self, record: ResearchRequestRecord) -> ResearchRequestRecord:
        """Persist a later lifecycle, cleanup, or accounting transition."""


@runtime_checkable
class ResearchBudgetLedger(Protocol):
    """Atomic budget reservation and later usage reconciliation."""

    def reserve(self, reservation: BudgetReservation) -> BudgetHold:
        """Reserve the full per-operation allowance or refuse admission."""

    def reconcile(self, reconciliation: BudgetReconciliation) -> BudgetHold:
        """Record reconciled usage, or an explicit unknown accounting state."""


@runtime_checkable
class ResearchResultCompletion(Protocol):
    """Atomic local save of one validated result."""

    def complete(self, completion: ResultCompletion) -> ResultCompletionReceipt:
        """Save the answer, citations, and request binding in one transaction."""


@dataclass(frozen=True, slots=True)
class ResearchSelectionSnapshot:
    """Admission-time copy of the selected provider, model, limits and budgets.

    Replacing the configuration later does not change an existing snapshot.
    A stored configuration is not live readiness.
    """

    provider_id: str
    model_id: str
    kind: ResearchOperationKind
    profile: ServerSelectedProfile
    resource_limits: ApprovedResourceLimits
    deadline_seconds: int
    descriptor: ProviderDescriptor
    live_ready: bool
    daily_budget_usd_micros: int
    monthly_budget_usd_micros: int


class ResearchSelectionError(Exception):
    """Sanitized selection failure. The message is the stable error code."""

    def __init__(self, code: ResearchErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResearchRequestRow:
    """Durable research request row.

    Domain lifecycle values are carried by ``record``. Storage-only fields
    (owner, client key, fingerprint, checkpoints, timestamps) stay apart so the
    domain module never depends on persistence.
    """

    record: ResearchRequestRecord
    owner_login_key: str
    client_request_id: str
    request_fingerprint: str
    checkpoint_json: str | None
    checkpoint_sha256: str | None
    record_id: str | None
    created_at_ms: int
    admitted_at_ms: int
    submitted_at_ms: int | None
    finished_at_ms: int | None
    updated_at_ms: int
    cancel_requested_at_ms: int | None
    cancellation_confirmed_at_ms: int | None

    def with_record(self, record: ResearchRequestRecord) -> ResearchRequestRow:
        """Return a copy carrying a different domain record."""
        return ResearchRequestRow(
            record=record,
            owner_login_key=self.owner_login_key,
            client_request_id=self.client_request_id,
            request_fingerprint=self.request_fingerprint,
            checkpoint_json=self.checkpoint_json,
            checkpoint_sha256=self.checkpoint_sha256,
            record_id=self.record_id,
            created_at_ms=self.created_at_ms,
            admitted_at_ms=self.admitted_at_ms,
            submitted_at_ms=self.submitted_at_ms,
            finished_at_ms=self.finished_at_ms,
            updated_at_ms=self.updated_at_ms,
            cancel_requested_at_ms=self.cancel_requested_at_ms,
            cancellation_confirmed_at_ms=self.cancellation_confirmed_at_ms,
        )


@dataclass(frozen=True, slots=True)
class ResearchAdmissionReceipt:
    """Admission result distinguishing a new admission from a replay.

    Only a newly admitted receipt may trigger a provider submission nudge.
    """

    row: ResearchRequestRow
    newly_admitted: bool


class ResearchStoreError(RuntimeError):
    """Sanitized durable-store refusal carrying one stable error code."""

    def __init__(self, code: ResearchErrorCode) -> None:
        super().__init__("research storage refused the operation.")
        self.code = code


@runtime_checkable
class ResearchRuntimeRepository(Protocol):
    """Durable runtime rows: admission, slot ownership, lifecycle saves."""

    def get_request(self, operation_id: str) -> ResearchRequestRow | None:
        """Return one stored request, or none when it is absent."""

    def find_by_client(
        self,
        owner_login_key: str,
        client_request_id: str,
    ) -> ResearchRequestRow | None:
        """Return the stored request for one client key, or none."""

    def admit(
        self,
        row: ResearchRequestRow,
        reservation: BudgetReservation,
    ) -> ResearchRequestRow:
        """Atomically reserve budget, acquire the slot, and persist the request."""

    def save(self, row: ResearchRequestRow) -> ResearchRequestRow:
        """Persist one lifecycle, cleanup, accounting, or checkpoint change."""

    def claim_submission(self, operation_id: str) -> ResearchRequestRow | None:
        """Atomically claim ``ADMITTED`` to ``SUBMITTING``.

        Returns the claimed row for the single winner, or ``None`` when another
        caller already claimed it. Only a winner may issue provider creation.
        """

    def active_slot_operation_id(self) -> str | None:
        """Return the operation holding the single active slot, or none."""

    def list_cleanup_pending(self, limit: int) -> tuple[ResearchRequestRow, ...]:
        """Return terminal requests whose remote response still needs release."""
