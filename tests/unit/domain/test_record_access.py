"""Caller-matrix decisions for bound Kronika records."""

from __future__ import annotations

from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    ROLE_ADMIN,
    ROLE_USER,
    IdentityContext,
)
from kronika.domain.record_access import (
    READ_APPROVED,
    READ_CURRENT,
    READ_DENY,
    BoundRecordView,
    decide_bound_read,
    may_approve,
    scope_for_identity,
)
from kronika.domain.records import RecordKind, RecordVisibility


def _identity(login: str, role: str) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login,
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


def _record(*, owner: str, visibility: RecordVisibility, approved: bool) -> BoundRecordView:
    return BoundRecordView(
        owner_login_key=owner,
        kind=RecordKind.MEDIA,
        visibility=visibility,
        completed=True,
        approved=approved,
    )


def test_owner_reads_private_current_and_stranger_is_denied() -> None:
    private = _record(owner="alice", visibility=RecordVisibility.PRIVATE, approved=False)
    alice = _identity("alice", ROLE_USER)
    bob = _identity("bob", ROLE_USER)
    assert decide_bound_read(alice, private) == READ_CURRENT
    assert decide_bound_read(bob, private) == READ_DENY


def test_household_member_reads_approved_snapshot_and_admin_reads_current() -> None:
    family = _record(owner="alice", visibility=RecordVisibility.FAMILY, approved=True)
    bob = _identity("bob", ROLE_USER)
    admin = _identity("ada", ROLE_ADMIN)
    assert decide_bound_read(bob, family) == READ_APPROVED
    assert decide_bound_read(admin, family) == READ_CURRENT
    assert may_approve(admin) is True
    assert may_approve(bob) is False


def test_missing_identity_denies_scope_and_reads() -> None:
    private = _record(owner="alice", visibility=RecordVisibility.PRIVATE, approved=False)
    assert decide_bound_read(None, private) == READ_DENY
    assert scope_for_identity(None).allows_queries is False
