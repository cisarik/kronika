"""SQLite budget ledger for research reservations and reconciliation."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, Connection, insert, select, update

from kronika.application.ports.research import ResearchStoreError
from kronika.domain.research import (
    TERMINAL_RESEARCH_LIFECYCLE_STATES,
    BudgetHold,
    BudgetReconciliation,
    BudgetReservation,
    ResearchAccountingState,
    ResearchErrorCode,
)
from kronika.infrastructure.persistence.catalog_schema import (
    research_budget_holds,
    research_requests,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)


def _utc_keys(at_ms: int) -> tuple[str, str]:
    moment = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")


def _consumed_micros(connection: Connection, column: object, key: str) -> int:
    rows = connection.execute(
        select(
            research_budget_holds.c.state,
            research_budget_holds.c.reserved_usd_micros,
            research_budget_holds.c.accounted_usd_micros,
        ).where(column == key)
    ).all()
    total = 0
    for state, reserved, accounted in rows:
        if state == ResearchAccountingState.RESERVED.value:
            total += int(reserved)
        else:
            total += int(accounted or 0)
    return total


def reserve_hold(
    connection: Connection,
    reservation: BudgetReservation,
    *,
    now_ms: int,
) -> BudgetHold:
    """Reserve one allowance inside a caller-owned immediate transaction."""
    day_key, month_key = _utc_keys(now_ms)
    if (
        _consumed_micros(connection, research_budget_holds.c.day_key, day_key)
        + reservation.reserved_usd_micros
        > reservation.daily_limit_usd_micros
    ):
        raise ResearchStoreError(ResearchErrorCode.BUDGET_EXCEEDED)
    if (
        _consumed_micros(connection, research_budget_holds.c.month_key, month_key)
        + reservation.reserved_usd_micros
        > reservation.monthly_limit_usd_micros
    ):
        raise ResearchStoreError(ResearchErrorCode.BUDGET_EXCEEDED)
    connection.execute(
        insert(research_budget_holds).values(
            operation_id=reservation.operation_id,
            day_key=day_key,
            month_key=month_key,
            reserved_usd_micros=reservation.reserved_usd_micros,
            accounted_usd_micros=None,
            state=ResearchAccountingState.RESERVED.value,
            created_at_ms=now_ms,
            reconciled_at_ms=None,
        )
    )
    return BudgetHold(
        operation_id=reservation.operation_id,
        reserved_usd_micros=reservation.reserved_usd_micros,
        state=ResearchAccountingState.RESERVED,
    )


def reconcile_hold(
    connection: Connection,
    reconciliation: BudgetReconciliation,
    *,
    now_ms: int,
) -> BudgetHold:
    """Reconcile one recorded hold inside a caller-owned immediate transaction."""
    row = connection.execute(
        select(research_budget_holds).where(
            research_budget_holds.c.operation_id == reconciliation.operation_id
        )
    ).first()
    if row is None:
        raise ResearchStoreError(ResearchErrorCode.STORAGE)
    reserved = int(row.reserved_usd_micros)
    if row.state != ResearchAccountingState.RESERVED.value:
        return BudgetHold(
            operation_id=reconciliation.operation_id,
            reserved_usd_micros=reserved,
            state=ResearchAccountingState(row.state),
        )
    if reconciliation.state is ResearchAccountingState.RECONCILED:
        accounted = int(reconciliation.calculated_cost_usd_micros or 0)
    elif reconciliation.state is ResearchAccountingState.UNKNOWN:
        # Unknown usage is never stored as zero: consume the reservation.
        accounted = reserved
    else:
        raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
    connection.execute(
        update(research_budget_holds)
        .where(research_budget_holds.c.operation_id == reconciliation.operation_id)
        .values(
            accounted_usd_micros=accounted,
            state=reconciliation.state.value,
            reconciled_at_ms=now_ms,
        )
    )
    return BudgetHold(
        operation_id=reconciliation.operation_id,
        reserved_usd_micros=reserved,
        state=reconciliation.state,
    )


class SqliteResearchBudgetLedger:
    """Synchronous ledger on the shared catalogue engine."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._engine = engine
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def reserve(self, reservation: BudgetReservation) -> BudgetHold:
        now_ms = self._clock_ms()
        return run_in_immediate_transaction(
            self._engine,
            lambda connection: reserve_hold(connection, reservation, now_ms=now_ms),
        )

    def reconcile(self, reconciliation: BudgetReconciliation) -> BudgetHold:
        now_ms = self._clock_ms()
        return run_in_immediate_transaction(
            self._engine,
            lambda connection: reconcile_hold(
                connection, reconciliation, now_ms=now_ms
            ),
        )

    def blocking_accounting_state(self) -> ResearchErrorCode | None:
        """Return the fail-closed accounting blocker, or ``None``.

        Any unresolved unknown accounting, any recorded cost above its
        reservation, and any terminal request whose hold is still reserved
        (a crash between terminal persistence and reconciliation) block further
        generation until an operator reconciles them.
        """

        def operation(connection: Connection) -> ResearchErrorCode | None:
            rows = connection.execute(
                select(
                    research_budget_holds.c.state,
                    research_budget_holds.c.reserved_usd_micros,
                    research_budget_holds.c.accounted_usd_micros,
                )
            ).all()
            for state, reserved, accounted in rows:
                if state == ResearchAccountingState.UNKNOWN.value:
                    return ResearchErrorCode.ACCOUNTING_UNKNOWN
                if accounted is not None and int(accounted) > int(reserved):
                    return ResearchErrorCode.BUDGET_EXCEEDED
            terminal_states = tuple(
                research_state.value
                for research_state in TERMINAL_RESEARCH_LIFECYCLE_STATES
            )
            gap = connection.execute(
                select(research_budget_holds.c.operation_id)
                .join(
                    research_requests,
                    research_requests.c.operation_id
                    == research_budget_holds.c.operation_id,
                )
                .where(
                    research_budget_holds.c.state
                    == ResearchAccountingState.RESERVED.value,
                    research_requests.c.lifecycle_state.in_(terminal_states),
                )
                .limit(1)
            ).first()
            if gap is not None:
                return ResearchErrorCode.ACCOUNTING_UNKNOWN
            return None

        return run_in_transaction(self._engine, operation)

    def consumed_micros(self, *, day_key: str, month_key: str) -> tuple[int, int]:
        """Read-only consumed totals for one UTC day and month."""

        def operation(connection: Connection) -> tuple[int, int]:
            return (
                _consumed_micros(connection, research_budget_holds.c.day_key, day_key),
                _consumed_micros(connection, research_budget_holds.c.month_key, month_key),
            )

        return run_in_transaction(self._engine, operation)
