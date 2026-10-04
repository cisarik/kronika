"""Use cases for Kronika records. Owners come from the verified caller."""

from __future__ import annotations

from dataclasses import dataclass

from kronika.application.ports.records import (
    ApprovalCandidate,
    ApprovalResult,
    RecordDetail,
    RecordPage,
    RecordPageQuery,
    RecordRepository,
)
from kronika.domain.identity_access import IdentityContext
from kronika.domain.record_access import may_approve
from kronika.domain.records import (
    CompletedDocument,
    RecordConflictError,
    RecordId,
    RecordKind,
    RecordNotFoundError,
    RecordValueError,
    RecordVisibility,
    parse_owner_login_key,
)

DEFAULT_RECORD_PAGE_LIMIT = 24
MAX_RECORD_PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class RecordService:
    """Authorized record reads and administrator approval."""

    repository: RecordRepository

    def create_completed_document(
        self,
        identity: object,
        document: CompletedDocument,
        *,
        record_id: RecordId | None = None,
    ) -> RecordDetail:
        owner = _require_login(identity)
        return self.repository.create_completed_document(
            document,
            owner_login_key=owner,
            record_id=record_id or RecordId.new(),
        )

    def read_detail(self, identity: object, record_id: str) -> RecordDetail:
        caller = _require_identity(identity)
        return self.repository.read_detail(caller, record_id)

    def list_own_history(
        self,
        identity: object,
        *,
        limit: int = DEFAULT_RECORD_PAGE_LIMIT,
        offset: int = 0,
        kind: str | None = None,
        content_category: str | None = None,
    ) -> RecordPage:
        caller = _require_identity(identity)
        return self.repository.list_own_history(
            caller,
            _query(
                limit,
                offset,
                kind=kind,
                content_category=content_category,
                visibility=None,
            ),
        )

    def list_admin_inventory(
        self,
        identity: object,
        *,
        limit: int = DEFAULT_RECORD_PAGE_LIMIT,
        offset: int = 0,
        kind: str | None = None,
        content_category: str | None = None,
        visibility: str | None = None,
    ) -> RecordPage:
        caller = _require_identity(identity)
        if not may_approve(caller):
            raise RecordNotFoundError()
        return self.repository.list_admin_inventory(
            caller,
            _query(
                limit,
                offset,
                kind=kind,
                content_category=content_category,
                visibility=visibility,
            ),
        )

    def list_timeline(
        self,
        identity: object,
        *,
        limit: int = DEFAULT_RECORD_PAGE_LIMIT,
        offset: int = 0,
        kind: str | None = None,
        content_category: str | None = None,
    ) -> RecordPage:
        caller = _require_identity(identity)
        return self.repository.list_timeline(
            caller,
            _query(
                limit,
                offset,
                kind=kind,
                content_category=content_category,
                visibility=None,
            ),
        )

    def prepare_approval(self, identity: object, record_id: str) -> ApprovalCandidate:
        caller = _require_identity(identity)
        if not may_approve(caller):
            raise RecordNotFoundError()
        return self.repository.prepare_approval(caller, record_id)

    def approve(
        self,
        identity: object,
        candidate: ApprovalCandidate,
        *,
        approved_at_ms: int,
    ) -> ApprovalResult:
        caller = _require_identity(identity)
        if not may_approve(caller):
            raise RecordNotFoundError()
        if isinstance(approved_at_ms, bool) or not isinstance(approved_at_ms, int):
            raise RecordValueError()
        return self.repository.approve(
            caller,
            candidate,
            approved_at_ms=approved_at_ms,
        )

    def withdraw(
        self,
        identity: object,
        *,
        record_id: str,
        expected_version: int,
    ) -> ApprovalResult:
        caller = _require_identity(identity)
        if not may_approve(caller):
            raise RecordNotFoundError()
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise RecordConflictError()
        return self.repository.withdraw(
            caller,
            record_id=record_id,
            expected_version=expected_version,
        )


def _require_identity(identity: object) -> IdentityContext:
    if not isinstance(identity, IdentityContext) or not identity.login_key:
        raise RecordNotFoundError()
    return identity


def _require_login(identity: object) -> str:
    caller = _require_identity(identity)
    return parse_owner_login_key(caller.login_key)


_KIND_FILTERS = {
    "media": RecordKind.MEDIA,
    "search": RecordKind.SEARCH,
    "research": RecordKind.RESEARCH,
}
_CATEGORY_FILTERS = frozenset({"general", "meme", "movie", "youtube"})
_VISIBILITY_FILTERS = {
    "private": RecordVisibility.PRIVATE,
    "family": RecordVisibility.FAMILY,
}


def _page(limit: int, offset: int) -> RecordPageQuery:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or limit < 1
        or limit > MAX_RECORD_PAGE_LIMIT
        or offset < 0
    ):
        raise RecordValueError()
    return RecordPageQuery(limit=limit, offset=offset)


def _query(
    limit: int,
    offset: int,
    *,
    kind: str | None,
    content_category: str | None,
    visibility: str | None,
) -> RecordPageQuery:
    page = _page(limit, offset)
    parsed_kind = None
    if kind is not None:
        if not isinstance(kind, str) or kind not in _KIND_FILTERS:
            raise RecordValueError()
        parsed_kind = _KIND_FILTERS[kind]
    parsed_category = None
    if content_category is not None:
        if (
            not isinstance(content_category, str)
            or content_category not in _CATEGORY_FILTERS
            or parsed_kind is not RecordKind.MEDIA
        ):
            raise RecordValueError()
        parsed_category = content_category
    parsed_visibility = None
    if visibility is not None:
        if not isinstance(visibility, str) or visibility not in _VISIBILITY_FILTERS:
            raise RecordValueError()
        parsed_visibility = _VISIBILITY_FILTERS[visibility]
    return RecordPageQuery(
        limit=page.limit,
        offset=page.offset,
        kind=parsed_kind,
        content_category=parsed_category,
        visibility=parsed_visibility,
    )
