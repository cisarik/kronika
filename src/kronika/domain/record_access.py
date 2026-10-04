"""Pure caller and access-scope decisions for Kronika records.

Decisions are deny, current, approved, or legacy. A missing scope denies.
legacy-public is available only when a caller explicitly selects it; this
module does not infer it from a missing identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_WORKFLOW_READ,
    IdentityContext,
)
from kronika.domain.records import RecordKind, RecordVisibility

READ_DENY = "deny"
READ_CURRENT = "current"
READ_APPROVED = "approved"
READ_LEGACY = "legacy"


class AccessScopeKind(str, Enum):
    DENY = "deny"
    MEMBER = "member"
    ADMINISTRATOR = "administrator"
    LEGACY_PUBLIC = "legacy_public"


@dataclass(frozen=True, slots=True)
class RecordAccessScope:
    """Server-derived scope carried into list and catalog queries."""

    kind: AccessScopeKind
    owner_login_key: str | None = None

    def __post_init__(self) -> None:
        if self.kind in {AccessScopeKind.MEMBER, AccessScopeKind.ADMINISTRATOR}:
            if not isinstance(self.owner_login_key, str) or not self.owner_login_key:
                raise ValueError("Record access scope is invalid.")
        elif self.owner_login_key is not None:
            raise ValueError("Record access scope is invalid.")

    @classmethod
    def deny(cls) -> "RecordAccessScope":
        return cls(kind=AccessScopeKind.DENY)

    @classmethod
    def legacy_public(cls) -> "RecordAccessScope":
        return cls(kind=AccessScopeKind.LEGACY_PUBLIC)

    @property
    def allows_queries(self) -> bool:
        return self.kind is not AccessScopeKind.DENY


def scope_for_identity(identity: object) -> RecordAccessScope:
    """Derive a scope from a verified identity. Missing identity denies."""
    if not isinstance(identity, IdentityContext) or not identity.login_key:
        return RecordAccessScope.deny()
    if identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ):
        return RecordAccessScope(
            kind=AccessScopeKind.ADMINISTRATOR,
            owner_login_key=identity.login_key,
        )
    return RecordAccessScope(
        kind=AccessScopeKind.MEMBER,
        owner_login_key=identity.login_key,
    )


@dataclass(frozen=True, slots=True)
class BoundRecordView:
    """Minimum binding facts needed for a pure read decision."""

    owner_login_key: str
    kind: RecordKind
    visibility: RecordVisibility
    completed: bool
    approved: bool


def decide_bound_read(identity: object, record: BoundRecordView) -> str:
    """Apply the accepted caller matrix to one bound record."""
    if not isinstance(identity, IdentityContext) or not identity.login_key:
        return READ_DENY
    if identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ):
        return READ_CURRENT
    if identity.login_key == record.owner_login_key:
        return READ_CURRENT
    if record.visibility is RecordVisibility.FAMILY and record.approved and record.completed:
        return READ_APPROVED
    return READ_DENY


def decide_unbound_legacy(
    identity: object,
    *,
    exists: bool,
    published: bool,
    requester_access: bool,
) -> str:
    """Preserve verified legacy behavior for media that has no common record."""
    if not isinstance(identity, IdentityContext) or not identity.login_key or not exists:
        return READ_DENY
    if identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ):
        return READ_CURRENT
    if published or requester_access:
        return READ_LEGACY
    return READ_DENY


def may_approve(identity: object) -> bool:
    """Approval is an explicit administrator action, never ownership."""
    return (
        isinstance(identity, IdentityContext)
        and identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ)
        and bool(identity.login_key)
    )


def timeline_includes(record: BoundRecordView) -> bool:
    """The shared Timeline is approved-family only, including for administrators."""
    return (
        record.visibility is RecordVisibility.FAMILY
        and record.approved
        and record.completed
    )
