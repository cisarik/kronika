"""SQLite research runtime repository: admission, slot ownership, saves."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping

from sqlalchemy import Engine, Connection, insert, select, update

from kronika.application.ports.research import (
    ResearchRequestRow,
    ResearchStoreError,
)
from kronika.domain.research import (
    ApprovedResourceLimits,
    BudgetReservation,
    ProviderHandle,
    TERMINAL_RESEARCH_LIFECYCLE_STATES,
    ResearchAccountingState,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ResearchRequestRecord,
    ServerSelectedProfile,
    is_terminal_lifecycle,
)
from kronika.infrastructure.persistence.catalog_schema import (
    research_active_slot,
    research_requests,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)
from kronika.infrastructure.persistence.research_budget_repository import (
    reserve_hold,
)

_MUTABLE_COLUMNS = (
    "lifecycle_state",
    "cleanup_state",
    "accounting_state",
    "remote_handle_json",
    "checkpoint_json",
    "checkpoint_sha256",
    "record_id",
    "error_code",
    "submitted_at_ms",
    "finished_at_ms",
    "updated_at_ms",
    "cancel_requested_at_ms",
    "cancellation_confirmed_at_ms",
)


def _row_to_values(row: ResearchRequestRow) -> dict[str, object]:
    record = row.record
    profile = record.profile
    limits = record.resource_limits
    return {
        "operation_id": record.operation_id,
        "owner_login_key": row.owner_login_key,
        "client_request_id": row.client_request_id,
        "request_fingerprint": row.request_fingerprint,
        "kind": record.kind.value,
        "prompt_text": record.prompt,
        "prompt_utf8_bytes": len(record.prompt.encode("utf-8")),
        "lifecycle_state": record.state.value,
        "provider_id": profile.provider_id,
        "model_id": profile.model_id,
        "configuration_version": profile.configuration_version,
        "reasoning_effort": profile.reasoning_effort,
        "max_tool_calls": limits.max_tool_calls,
        "max_output_tokens": limits.max_output_tokens,
        "deadline_seconds": record.deadline_seconds,
        "reservation_micro_usd": profile.budget_reservation_usd_micros,
        "tool_allowlist_json": json.dumps(list(profile.tool_allowlist)),
        "background": 1 if profile.background else 0,
        "prompt_max_utf8_bytes": limits.prompt_max_utf8_bytes,
        "answer_max_utf8_bytes": limits.answer_max_utf8_bytes,
        "citation_count_max": limits.citation_count_max,
        "remote_handle_json": (
            None
            if record.remote_handle is None
            else json.dumps({"value": record.remote_handle.value})
        ),
        "checkpoint_json": row.checkpoint_json,
        "checkpoint_sha256": row.checkpoint_sha256,
        "record_id": row.record_id,
        "error_code": None if record.error_code is None else record.error_code.value,
        "cancel_requested_at_ms": row.cancel_requested_at_ms,
        "cancellation_confirmed_at_ms": row.cancellation_confirmed_at_ms,
        "cleanup_state": record.cleanup_state.value,
        "accounting_state": record.accounting_state.value,
        "created_at_ms": row.created_at_ms,
        "admitted_at_ms": row.admitted_at_ms,
        "submitted_at_ms": row.submitted_at_ms,
        "finished_at_ms": row.finished_at_ms,
        "updated_at_ms": row.updated_at_ms,
    }


def _row_from_mapping(mapping: Mapping[str, object]) -> ResearchRequestRow:
    profile = ServerSelectedProfile(
        provider_id=str(mapping["provider_id"]),
        model_id=str(mapping["model_id"]),
        configuration_version=str(mapping["configuration_version"]),
        reasoning_effort=str(mapping["reasoning_effort"]),
        tool_allowlist=tuple(json.loads(str(mapping["tool_allowlist_json"]))),
        background=bool(mapping["background"]),
        max_tool_calls=int(mapping["max_tool_calls"]),
        max_output_tokens=int(mapping["max_output_tokens"]),
        deadline_seconds=int(mapping["deadline_seconds"]),
        budget_reservation_usd_micros=int(mapping["reservation_micro_usd"]),
    )
    limits = ApprovedResourceLimits(
        max_tool_calls=int(mapping["max_tool_calls"]),
        max_output_tokens=int(mapping["max_output_tokens"]),
        budget_reservation_usd_micros=int(mapping["reservation_micro_usd"]),
        prompt_max_utf8_bytes=int(mapping["prompt_max_utf8_bytes"]),
        answer_max_utf8_bytes=int(mapping["answer_max_utf8_bytes"]),
        citation_count_max=int(mapping["citation_count_max"]),
    )
    remote_handle_json = mapping["remote_handle_json"]
    error_code = mapping["error_code"]
    record = ResearchRequestRecord(
        operation_id=str(mapping["operation_id"]),
        kind=ResearchOperationKind(str(mapping["kind"])),
        prompt=str(mapping["prompt_text"]),
        profile=profile,
        deadline_seconds=int(mapping["deadline_seconds"]),
        resource_limits=limits,
        state=ResearchLifecycleState(str(mapping["lifecycle_state"])),
        cleanup_state=ResearchRemoteCleanupState(str(mapping["cleanup_state"])),
        accounting_state=ResearchAccountingState(str(mapping["accounting_state"])),
        remote_handle=(
            None
            if remote_handle_json is None
            else ProviderHandle(str(json.loads(str(remote_handle_json))["value"]))
        ),
        error_code=None if error_code is None else ResearchErrorCode(str(error_code)),
    )
    return ResearchRequestRow(
        record=record,
        owner_login_key=str(mapping["owner_login_key"]),
        client_request_id=str(mapping["client_request_id"]),
        request_fingerprint=str(mapping["request_fingerprint"]),
        checkpoint_json=(
            None if mapping["checkpoint_json"] is None else str(mapping["checkpoint_json"])
        ),
        checkpoint_sha256=(
            None
            if mapping["checkpoint_sha256"] is None
            else str(mapping["checkpoint_sha256"])
        ),
        record_id=None if mapping["record_id"] is None else str(mapping["record_id"]),
        created_at_ms=int(mapping["created_at_ms"]),
        admitted_at_ms=int(mapping["admitted_at_ms"]),
        submitted_at_ms=(
            None if mapping["submitted_at_ms"] is None else int(mapping["submitted_at_ms"])
        ),
        finished_at_ms=(
            None if mapping["finished_at_ms"] is None else int(mapping["finished_at_ms"])
        ),
        updated_at_ms=int(mapping["updated_at_ms"]),
        cancel_requested_at_ms=(
            None
            if mapping["cancel_requested_at_ms"] is None
            else int(mapping["cancel_requested_at_ms"])
        ),
        cancellation_confirmed_at_ms=(
            None
            if mapping["cancellation_confirmed_at_ms"] is None
            else int(mapping["cancellation_confirmed_at_ms"])
        ),
    )


def _select_request(
    connection: Connection,
    *,
    operation_id: str | None = None,
    client: tuple[str, str] | None = None,
) -> Mapping[str, object] | None:
    statement = select(research_requests)
    if operation_id is not None:
        statement = statement.where(research_requests.c.operation_id == operation_id)
    if client is not None:
        statement = statement.where(
            research_requests.c.owner_login_key == client[0],
            research_requests.c.client_request_id == client[1],
        )
    row = connection.execute(statement).mappings().first()
    return None if row is None else dict(row)


class SqliteResearchRequestRepository:
    """Synchronous request and active-slot store on the shared catalogue engine."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._engine = engine
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def get_request(self, operation_id: str) -> ResearchRequestRow | None:
        def operation(connection: Connection) -> ResearchRequestRow | None:
            row = _select_request(connection, operation_id=operation_id)
            return None if row is None else _row_from_mapping(row)

        return run_in_transaction(self._engine, operation)

    def find_by_client(
        self,
        owner_login_key: str,
        client_request_id: str,
    ) -> ResearchRequestRow | None:
        def operation(connection: Connection) -> ResearchRequestRow | None:
            row = _select_request(
                connection, client=(owner_login_key, client_request_id)
            )
            return None if row is None else _row_from_mapping(row)

        return run_in_transaction(self._engine, operation)

    def admit(
        self,
        row: ResearchRequestRow,
        reservation: BudgetReservation,
    ) -> ResearchRequestRow:
        if (
            reservation.operation_id != row.record.operation_id
            or reservation.reserved_usd_micros
            != row.record.profile.budget_reservation_usd_micros
        ):
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)

        def operation(connection: Connection) -> ResearchRequestRow:
            existing = _select_request(
                connection,
                client=(row.owner_login_key, row.client_request_id),
            )
            if existing is not None:
                if str(existing["request_fingerprint"]) == row.request_fingerprint:
                    return _row_from_mapping(existing)
                raise ResearchStoreError(ResearchErrorCode.IDEMPOTENCY_CONFLICT)
            slot = connection.execute(
                select(research_active_slot).where(research_active_slot.c.id == 1)
            ).mappings().first()
            if slot is None:
                connection.execute(
                    insert(research_active_slot).values(
                        id=1, operation_id=None, held_since_ms=None
                    )
                )
                held_operation: str | None = None
            else:
                held_operation = slot["operation_id"]
            if held_operation is not None:
                raise ResearchStoreError(ResearchErrorCode.BUSY)
            # The request row precedes the hold because the hold foreign key
            # references it; a budget refusal still rolls the whole immediate
            # transaction back.
            connection.execute(insert(research_requests).values(**_row_to_values(row)))
            reserve_hold(connection, reservation, now_ms=row.admitted_at_ms)
            connection.execute(
                update(research_active_slot)
                .where(research_active_slot.c.id == 1)
                .values(
                    operation_id=row.record.operation_id,
                    held_since_ms=row.admitted_at_ms,
                )
            )
            return row

        return run_in_immediate_transaction(self._engine, operation)

    def save(self, row: ResearchRequestRow) -> ResearchRequestRow:
        values = _row_to_values(row)
        update_values = {column: values[column] for column in _MUTABLE_COLUMNS}

        def operation(connection: Connection) -> ResearchRequestRow:
            result = connection.execute(
                update(research_requests)
                .where(
                    research_requests.c.operation_id == row.record.operation_id
                )
                .values(**update_values)
            )
            if result.rowcount != 1:
                raise ResearchStoreError(ResearchErrorCode.STORAGE)
            if is_terminal_lifecycle(row.record.state):
                connection.execute(
                    update(research_active_slot)
                    .where(
                        research_active_slot.c.id == 1,
                        research_active_slot.c.operation_id
                        == row.record.operation_id,
                    )
                    .values(operation_id=None, held_since_ms=None)
                )
            return row

        return run_in_immediate_transaction(self._engine, operation)

    def claim_submission(self, operation_id: str) -> ResearchRequestRow | None:
        """Atomically claim ``ADMITTED`` to ``SUBMITTING``.

        Only the single winner of the claim receives the row; every other
        concurrent caller receives ``None`` and must not issue provider
        creation.
        """
        now_ms = self._clock_ms()

        def operation(connection: Connection) -> ResearchRequestRow | None:
            result = connection.execute(
                update(research_requests)
                .where(
                    research_requests.c.operation_id == operation_id,
                    research_requests.c.lifecycle_state
                    == ResearchLifecycleState.ADMITTED.value,
                )
                .values(
                    lifecycle_state=ResearchLifecycleState.SUBMITTING.value,
                    updated_at_ms=now_ms,
                )
            )
            if result.rowcount != 1:
                return None
            row = _select_request(connection, operation_id=operation_id)
            return None if row is None else _row_from_mapping(row)

        return run_in_immediate_transaction(self._engine, operation)

    def active_slot_operation_id(self) -> str | None:
        def operation(connection: Connection) -> str | None:
            slot = connection.execute(
                select(research_active_slot.c.operation_id).where(
                    research_active_slot.c.id == 1
                )
            ).first()
            if slot is None:
                return None
            return None if slot[0] is None else str(slot[0])

        return run_in_transaction(self._engine, operation)

    def list_cleanup_pending(self, limit: int) -> tuple[ResearchRequestRow, ...]:
        terminal_states = sorted(
            state.value for state in TERMINAL_RESEARCH_LIFECYCLE_STATES
        )

        def operation(connection: Connection) -> tuple[ResearchRequestRow, ...]:
            rows = (
                connection.execute(
                    select(research_requests)
                    .where(
                        research_requests.c.cleanup_state
                        == ResearchRemoteCleanupState.PENDING.value,
                        research_requests.c.remote_handle_json.is_not(None),
                        research_requests.c.lifecycle_state.in_(terminal_states),
                    )
                    .order_by(
                        research_requests.c.updated_at_ms.asc(),
                        research_requests.c.operation_id.asc(),
                    )
                    .limit(max(0, int(limit)))
                )
                .mappings()
                .all()
            )
            return tuple(_row_from_mapping(dict(row)) for row in rows)

        return run_in_transaction(self._engine, operation)

    def list_for_owner(
        self,
        owner_login_key: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> tuple[ResearchRequestRow, ...]:
        def operation(connection: Connection) -> tuple[ResearchRequestRow, ...]:
            rows = (
                connection.execute(
                    select(research_requests)
                    .where(research_requests.c.owner_login_key == owner_login_key)
                    .order_by(
                        research_requests.c.created_at_ms.desc(),
                        research_requests.c.operation_id.asc(),
                    )
                    .limit(max(0, int(limit)))
                    .offset(max(0, int(offset)))
                )
                .mappings()
                .all()
            )
            return tuple(_row_from_mapping(dict(row)) for row in rows)

        return run_in_transaction(self._engine, operation)

    def count_for_owner(self, owner_login_key: str) -> int:
        def operation(connection: Connection) -> int:
            from sqlalchemy import func

            return int(
                connection.execute(
                    select(func.count())
                    .select_from(research_requests)
                    .where(research_requests.c.owner_login_key == owner_login_key)
                ).scalar()
                or 0
            )

        return run_in_transaction(self._engine, operation)

    def list_all(
        self,
        *,
        limit: int,
        offset: int = 0,
    ) -> tuple[ResearchRequestRow, ...]:
        def operation(connection: Connection) -> tuple[ResearchRequestRow, ...]:
            rows = (
                connection.execute(
                    select(research_requests)
                    .order_by(
                        research_requests.c.created_at_ms.desc(),
                        research_requests.c.operation_id.asc(),
                    )
                    .limit(max(0, int(limit)))
                    .offset(max(0, int(offset)))
                )
                .mappings()
                .all()
            )
            return tuple(_row_from_mapping(dict(row)) for row in rows)

        return run_in_transaction(self._engine, operation)

    def count_all(self) -> int:
        def operation(connection: Connection) -> int:
            from sqlalchemy import func

            return int(
                connection.execute(
                    select(func.count()).select_from(research_requests)
                ).scalar()
                or 0
            )

        return run_in_transaction(self._engine, operation)
