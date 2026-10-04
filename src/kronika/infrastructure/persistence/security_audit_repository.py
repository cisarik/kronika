"""SQLAlchemy Core adapter for durable security audit events."""

from __future__ import annotations

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from kronika.domain.security_audit import SecurityAuditEvent
from kronika.infrastructure.persistence.catalog_schema import security_audit_events
from kronika.infrastructure.persistence.engine import run_in_transaction
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError

_AUDIT_FAILURE_MESSAGE = "Security audit operation failed."


class FrameNestSecurityAuditRepositoryError(FrameNestPersistenceError):
    """Sanitized security audit persistence failure."""


class SqliteSecurityAuditRepository:
    """Synchronous SQLite adapter for append-only privileged-action audit events."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, event: SecurityAuditEvent) -> None:
        """Append one validated audit event in its own short transaction."""

        def operation(connection: Connection) -> None:
            connection.execute(
                insert(security_audit_events).values(_values_from_event(event))
            )

        try:
            run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestSecurityAuditRepositoryError(
                _AUDIT_FAILURE_MESSAGE,
                error_code="SECURITY_AUDIT_RECORD_FAILED",
                retryable=False,
                cause=exc,
            ) from exc

    def record_http_status(self, event_id: str, http_status: int) -> None:
        """Stamp the final HTTP status onto one already-recorded event."""

        def operation(connection: Connection) -> None:
            connection.execute(
                update(security_audit_events)
                .where(security_audit_events.c.id == event_id)
                .values(http_status=http_status)
            )

        try:
            run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestSecurityAuditRepositoryError(
                _AUDIT_FAILURE_MESSAGE,
                error_code="SECURITY_AUDIT_RECORD_FAILED",
                retryable=False,
                cause=exc,
            ) from exc

    def count_events(self) -> int:
        """Return the number of persisted audit events for review and tests."""

        def operation(connection: Connection) -> int:
            return int(
                connection.execute(
                    select(func.count()).select_from(security_audit_events)
                ).scalar_one()
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestSecurityAuditRepositoryError(
                _AUDIT_FAILURE_MESSAGE,
                error_code="SECURITY_AUDIT_RECORD_FAILED",
                retryable=False,
                cause=exc,
            ) from exc


def _values_from_event(event: SecurityAuditEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "occurred_at_ms": event.occurred_at_ms,
        "request_id": event.request_id,
        "actor_login": event.actor_login,
        "actor_key": event.actor_key,
        "identity_provenance": event.identity_provenance,
        "role": event.role,
        "capability": event.capability,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "outcome": event.outcome,
        "http_status": event.http_status,
    }
