"""Application port for Kronika records. Values are domain objects only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.domain.identity_access import IdentityContext
from kronika.domain.records import (
    ApprovalProjection,
    CompletedDocument,
    RecordId,
    RecordKind,
    RecordVisibility,
)


@dataclass(frozen=True, slots=True)
class RecordPageQuery:
    limit: int
    offset: int
    kind: RecordKind | None = None
    content_category: str | None = None
    visibility: RecordVisibility | None = None


@dataclass(frozen=True, slots=True)
class RecordSummary:
    record_id: str
    kind: RecordKind
    owner_login_key: str
    visibility: RecordVisibility
    created_at_ms: int
    completed_at_ms: int | None
    timeline_entered_at_ms: int | None
    version: int
    media_id: str | None
    read_decision: str
    display_title: str | None = None
    content_category: str | None = None


@dataclass(frozen=True, slots=True)
class RecordPage:
    items: tuple[RecordSummary, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class RecordDetail:
    summary: RecordSummary
    projection: ApprovalProjection | None
    document: CompletedDocument | None
    version: int


@dataclass(frozen=True, slots=True)
class ApprovalCandidate:
    record_id: str
    version: int
    analysis_run_id: str | None
    digest: str


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    record_id: str
    version: int
    changed: bool


class RecordRepository(Protocol):
    """Persistence contract for completed records and approval."""

    def create_completed_document(
        self,
        document: CompletedDocument,
        *,
        owner_login_key: str,
        record_id: RecordId,
    ) -> RecordDetail:
        """Insert one immutable document and its private record, or replay it."""

    def bind_media_record(
        self,
        connection: object,
        *,
        record_id: RecordId,
        media_id: str,
        owner_login_key: str,
        created_at_ms: int,
    ) -> None:
        """Insert a media record on the caller's open transaction."""

    def read_detail(self, identity: IdentityContext, record_id: str) -> RecordDetail:
        """Return an authorized detail or raise a sanitized not-found error."""

    def list_own_history(
        self, identity: IdentityContext, query: RecordPageQuery
    ) -> RecordPage:
        """Return the caller's records, including an administrator's own rows."""

    def list_admin_inventory(
        self, identity: IdentityContext, query: RecordPageQuery
    ) -> RecordPage:
        """Return every record for an application administrator."""

    def list_timeline(
        self, identity: IdentityContext, query: RecordPageQuery
    ) -> RecordPage:
        """Return approved family records only."""

    def prepare_approval(
        self, identity: IdentityContext, record_id: str
    ) -> ApprovalCandidate:
        """Build a candidate without writing."""

    def approve(
        self,
        identity: IdentityContext,
        candidate: ApprovalCandidate,
        *,
        approved_at_ms: int,
    ) -> ApprovalResult:
        """Approve inside one immediate transaction."""

    def withdraw(
        self,
        identity: IdentityContext,
        *,
        record_id: str,
        expected_version: int,
    ) -> ApprovalResult:
        """Withdraw household visibility inside one immediate transaction."""

    def approved_projection_for_media(self, media_id: str) -> ApprovalProjection | None:
        """Return the stored approval snapshot for one media id, or None."""
