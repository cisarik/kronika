"""Minimal durable security audit event model for privileged actions."""

from __future__ import annotations

from dataclasses import dataclass
import time
import uuid

AUDIT_OUTCOME_ALLOWED = "allowed"
AUDIT_OUTCOME_DENIED = "denied"
AUDIT_OUTCOMES = frozenset({AUDIT_OUTCOME_ALLOWED, AUDIT_OUTCOME_DENIED})

MAX_AUDIT_REQUEST_ID_LENGTH = 64
MAX_AUDIT_ACTOR_LENGTH = 254
MAX_AUDIT_PROVENANCE_LENGTH = 32
MAX_AUDIT_ROLE_LENGTH = 16
MAX_AUDIT_CAPABILITY_LENGTH = 64
MAX_AUDIT_ACTION_LENGTH = 64
MAX_AUDIT_TARGET_TYPE_LENGTH = 64
MAX_AUDIT_TARGET_ID_LENGTH = 128


class FrameNestSecurityAuditError(Exception):
    """Sanitized audit-model failure safe for fail-closed handling."""


@dataclass(frozen=True, slots=True)
class SecurityAuditEvent:
    """One immutable privileged-action audit record."""

    id: str
    occurred_at_ms: int
    request_id: str
    actor_login: str
    actor_key: str
    identity_provenance: str
    role: str
    capability: str
    action: str
    target_type: str
    target_id: str | None
    outcome: str
    http_status: int | None

    @classmethod
    def new(
        cls,
        *,
        request_id: str,
        actor_login: str,
        actor_key: str,
        identity_provenance: str,
        role: str,
        capability: str,
        action: str,
        target_type: str,
        target_id: str | None,
        outcome: str,
        http_status: int | None,
        now_ms: int | None = None,
    ) -> "SecurityAuditEvent":
        """Create a validated audit event with a fresh id and timestamp."""
        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000
        if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0:
            raise FrameNestSecurityAuditError("Audit event is invalid.")
        event_id = str(uuid.uuid4())
        return cls(
            id=event_id,
            occurred_at_ms=now_ms,
            request_id=_bounded_machine_text(
                request_id, MAX_AUDIT_REQUEST_ID_LENGTH
            ),
            actor_login=_bounded_text(actor_login, MAX_AUDIT_ACTOR_LENGTH),
            actor_key=_bounded_machine_text(actor_key, MAX_AUDIT_ACTOR_LENGTH),
            identity_provenance=_bounded_machine_text(
                identity_provenance, MAX_AUDIT_PROVENANCE_LENGTH
            ),
            role=_bounded_machine_text(role, MAX_AUDIT_ROLE_LENGTH),
            capability=_bounded_machine_text(capability, MAX_AUDIT_CAPABILITY_LENGTH),
            action=_bounded_machine_text(action, MAX_AUDIT_ACTION_LENGTH),
            target_type=_bounded_machine_text(target_type, MAX_AUDIT_TARGET_TYPE_LENGTH),
            target_id=(
                None
                if target_id is None
                else _bounded_text(target_id, MAX_AUDIT_TARGET_ID_LENGTH)
            ),
            outcome=_validated_outcome(outcome),
            http_status=_validated_http_status(http_status),
        )


def _validated_outcome(outcome: str) -> str:
    if outcome not in AUDIT_OUTCOMES:
        raise FrameNestSecurityAuditError("Audit event is invalid.")
    return outcome


def _validated_http_status(http_status: int | None) -> int | None:
    if http_status is None:
        return None
    if (
        not isinstance(http_status, int)
        or isinstance(http_status, bool)
        or http_status < 100
        or http_status > 599
    ):
        raise FrameNestSecurityAuditError("Audit event is invalid.")
    return http_status


def _bounded_text(value: object, max_length: int) -> str:
    if not isinstance(value, str):
        raise FrameNestSecurityAuditError("Audit event is invalid.")
    stripped = value.strip()
    if not stripped or len(stripped) > max_length:
        raise FrameNestSecurityAuditError("Audit event is invalid.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in stripped):
        raise FrameNestSecurityAuditError("Audit event is invalid.")
    return stripped


def _bounded_machine_text(value: object, max_length: int) -> str:
    text = _bounded_text(value, max_length)
    if any(character.isspace() for character in text):
        raise FrameNestSecurityAuditError("Audit event is invalid.")
    return text
