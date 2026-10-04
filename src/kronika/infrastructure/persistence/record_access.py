"""SQL predicates for Kronika record visibility.

Domain decisions stay in ``domain.record_access``. This module only builds
SQLAlchemy expressions.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, not_, or_, select

from kronika.domain.record_access import AccessScopeKind, RecordAccessScope
from kronika.infrastructure.persistence.catalog_schema import (
    kronika_records,
    logical_media,
    media_content_publications,
)


def record_bound_to_media():
    """True when a common record is bound to the logical media row."""
    records = kronika_records.alias("kronika_bound_records")
    return exists(
        select(1)
        .select_from(records)
        .where(records.c.media_id == logical_media.c.id)
    )


def legacy_public_media_predicate():
    """Published media that has no common record."""
    publications = media_content_publications.alias("legacy_publications")
    published = exists(
        select(1)
        .select_from(publications)
        .where(publications.c.media_id == logical_media.c.id)
    )
    return and_(published, not_(record_bound_to_media()))


def member_current_media_predicate(owner_login_key: str):
    """Own records, plus unbound media. Contribution filters are added by callers."""
    records = kronika_records.alias("kronika_own_records")
    own = exists(
        select(1)
        .select_from(records)
        .where(
            records.c.media_id == logical_media.c.id,
            records.c.owner_login_key == owner_login_key,
        )
    )
    return or_(own, not_(record_bound_to_media()))


def member_approved_media_predicate(owner_login_key: str):
    """Other owners' approved family media."""
    records = kronika_records.alias("kronika_family_records")
    return exists(
        select(1)
        .select_from(records)
        .where(
            records.c.media_id == logical_media.c.id,
            records.c.owner_login_key != owner_login_key,
            records.c.visibility == "family",
            records.c.approved_projection_json.is_not(None),
            records.c.completed_at_ms.is_not(None),
        )
    )


def unbound_published_or_owned(owner_login_key: str):
    """Gallery membership: own records, approved family, or published unbound media."""
    publications = media_content_publications.alias("member_publications")
    published = exists(
        select(1)
        .select_from(publications)
        .where(publications.c.media_id == logical_media.c.id)
    )
    records = kronika_records.alias("kronika_member_records")
    own = exists(
        select(1)
        .select_from(records)
        .where(
            records.c.media_id == logical_media.c.id,
            records.c.owner_login_key == owner_login_key,
        )
    )
    return or_(own, member_approved_media_predicate(owner_login_key), published)


def catalog_scope_predicate(scope: RecordAccessScope | None):
    """Membership predicate. A missing or deny scope matches nothing."""
    if scope is None or scope.kind is AccessScopeKind.DENY:
        return logical_media.c.id.is_(None)
    if scope.kind is AccessScopeKind.ADMINISTRATOR:
        return logical_media.c.id.is_not(None)
    if scope.kind is AccessScopeKind.LEGACY_PUBLIC:
        return legacy_public_media_predicate()
    if scope.owner_login_key is None:
        return logical_media.c.id.is_(None)
    return or_(
        member_current_media_predicate(scope.owner_login_key),
        member_approved_media_predicate(scope.owner_login_key),
    )
