"""SQLite repository for Kronika records, documents, and approval."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.records import (
    ApprovalCandidate,
    ApprovalResult,
    RecordDetail,
    RecordPage,
    RecordPageQuery,
    RecordSummary,
)
from kronika.domain.content_publication import derive_content_publication_readiness
from kronika.domain.identity_access import IdentityContext
from kronika.domain.record_access import (
    READ_APPROVED,
    READ_CURRENT,
    BoundRecordView,
    decide_bound_read,
    may_approve,
)
from kronika.application.ports.research import (
    ResearchStoreError,
)
from kronika.domain.research import (
    ResearchErrorCode,
    ResearchLifecycleState,
    ResultCompletion,
    ResultCompletionReceipt,
)
from kronika.domain.records import (
    ApprovalProjection,
    ApprovedLocationSnapshot,
    ApprovedTagSnapshot,
    CompletedDocument,
    DocumentId,
    NormalizedCitation,
    RecordConflictError,
    RecordId,
    RecordKind,
    RecordNotFoundError,
    RecordStorageIntegrityError,
    RecordValueError,
    RecordVisibility,
    document_from_storage,
    parse_owner_login_key,
    projection_from_storage,
)
from kronika.infrastructure.persistence.catalog_schema import (
    research_requests,
    kronika_approved_media,
    kronika_approved_media_locations,
    kronika_approved_media_tags,
    kronika_documents,
    kronika_records,
    media_analysis_runs,
    media_canonical_tags,
    media_covers,
    media_metadata,
    physical_media_locations,
    canonical_tags,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_FAILURE = "Record storage failed."
_FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "api_key",
        "credential",
        "prompt",
        "provider_response",
        "raw",
        "raw_response",
        "reasoning",
        "secret",
        "token",
    }
)


class RecordRepositoryError(RuntimeError):
    """Sanitized record persistence failure."""

    def __init__(self) -> None:
        super().__init__(_FAILURE)


class SqliteRecordRepository:
    """Synchronous record store on the shared catalogue engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_completed_document(
        self,
        document: CompletedDocument,
        *,
        owner_login_key: str,
        record_id: RecordId,
    ) -> RecordDetail:
        owner = parse_owner_login_key(owner_login_key)

        def operation(connection: Connection) -> RecordDetail:
            existing = connection.execute(
                select(kronika_documents).where(
                    kronika_documents.c.operation_id == document.operation_id
                )
            ).mappings().first()
            if existing is not None:
                stored = document_from_storage(**_document_kwargs(existing))
                if not _same_document(stored, document):
                    raise RecordConflictError()
                record = connection.execute(
                    select(kronika_records).where(
                        kronika_records.c.document_id == stored.document_id.to_string()
                    )
                ).mappings().first()
                if record is None or str(record["owner_login_key"]) != owner:
                    raise RecordConflictError()
                return _detail_from_row(connection, record, READ_CURRENT)
            connection.execute(
                insert(kronika_documents).values(
                    id=document.document_id.to_string(),
                    operation_id=document.operation_id,
                    kind=document.kind.value,
                    question_text=document.question_text,
                    answer_text=document.answer_text,
                    citations_json=document.citations_json(),
                    completion_evidence_json=document.evidence_json(),
                    created_at_ms=document.created_at_ms,
                    completed_at_ms=document.completed_at_ms,
                )
            )
            connection.execute(
                insert(kronika_records).values(
                    id=record_id.to_string(),
                    kind=document.kind.value,
                    owner_login_key=owner,
                    visibility=RecordVisibility.PRIVATE.value,
                    document_id=document.document_id.to_string(),
                    final_operation_id=document.operation_id,
                    created_at_ms=document.created_at_ms,
                    completed_at_ms=document.completed_at_ms,
                    version=1,
                )
            )
            row = connection.execute(
                select(kronika_records).where(kronika_records.c.id == record_id.to_string())
            ).mappings().one()
            return _detail_from_row(connection, row, READ_CURRENT)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (RecordConflictError, RecordStorageIntegrityError, RecordValueError):
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise RecordRepositoryError() from exc

    def bound_record(self, media_id: object) -> BoundRecordView | None:
        """Return the binding used by the audience policy, or None when unbound."""
        media_text = getattr(media_id, "to_string", lambda: str(media_id))()

        def operation(connection: Connection) -> BoundRecordView | None:
            row = connection.execute(
                select(kronika_records).where(kronika_records.c.media_id == media_text)
            ).mappings().first()
            if row is None:
                return None
            return _bound_view(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise RecordRepositoryError() from exc

    def approved_projection_for_media(self, media_id: str) -> ApprovalProjection | None:
        """Return the frozen snapshot stored for this media, or None."""

        def operation(connection: Connection) -> ApprovalProjection | None:
            stored = connection.execute(
                select(kronika_records.c.approved_projection_json).where(
                    kronika_records.c.media_id == media_id
                )
            ).scalar()
            if stored is None:
                return None
            return projection_from_storage(str(stored))

        try:
            return run_in_transaction(self._engine, operation)
        except (RecordStorageIntegrityError, RecordRepositoryError):
            raise
        except SQLAlchemyError as exc:
            raise RecordRepositoryError() from exc

    def bind_media_record(
        self,
        connection: Connection,
        *,
        record_id: RecordId,
        media_id: str,
        owner_login_key: str,
        created_at_ms: int,
    ) -> None:
        """Insert one private media record on the caller's transaction."""
        owner = parse_owner_login_key(owner_login_key)
        if not isinstance(created_at_ms, int) or isinstance(created_at_ms, bool) or created_at_ms < 0:
            raise RecordValueError()
        connection.execute(
            insert(kronika_records).values(
                id=record_id.to_string(),
                kind=RecordKind.MEDIA.value,
                owner_login_key=owner,
                visibility=RecordVisibility.PRIVATE.value,
                media_id=media_id,
                created_at_ms=created_at_ms,
                version=1,
            )
        )

    def read_detail(self, identity: IdentityContext, record_id: str) -> RecordDetail:
        def operation(connection: Connection) -> RecordDetail:
            row = _lock_record(connection, record_id)
            if row is None:
                raise RecordNotFoundError()
            decision = decide_bound_read(identity, _bound_view(row))
            if decision == "deny":
                raise RecordNotFoundError()
            return _detail_from_row(connection, row, decision)

        try:
            return run_in_transaction(self._engine, operation)
        except (RecordNotFoundError, RecordStorageIntegrityError):
            raise
        except SQLAlchemyError as exc:
            raise RecordRepositoryError() from exc

    def list_own_history(self, identity: IdentityContext, query: RecordPageQuery) -> RecordPage:
        return self._list(
            identity,
            query,
            owner_login_key=identity.login_key,
            timeline_only=False,
        )

    def list_admin_inventory(
        self, identity: IdentityContext, query: RecordPageQuery
    ) -> RecordPage:
        if not may_approve(identity):
            raise RecordNotFoundError()
        return self._list(identity, query, owner_login_key=None, timeline_only=False)

    def list_timeline(self, identity: IdentityContext, query: RecordPageQuery) -> RecordPage:
        if not isinstance(identity, IdentityContext) or not identity.login_key:
            raise RecordNotFoundError()
        return self._list(identity, query, owner_login_key=None, timeline_only=True)

    def prepare_approval(self, identity: IdentityContext, record_id: str) -> ApprovalCandidate:
        if not may_approve(identity):
            raise RecordNotFoundError()

        def operation(connection: Connection) -> ApprovalCandidate:
            row = _lock_record(connection, record_id)
            if row is None:
                raise RecordNotFoundError()
            projection = _build_projection(connection, row)
            return ApprovalCandidate(
                record_id=str(row["id"]),
                version=int(row["version"]),
                analysis_run_id=projection.analysis_run_id,
                digest=projection.digest(),
            )

        try:
            return run_in_transaction(self._engine, operation)
        except (RecordNotFoundError, RecordConflictError, RecordValueError):
            raise
        except SQLAlchemyError as exc:
            raise RecordRepositoryError() from exc

    def approve(
        self,
        identity: IdentityContext,
        candidate: ApprovalCandidate,
        *,
        approved_at_ms: int,
    ) -> ApprovalResult:
        if not may_approve(identity):
            raise RecordNotFoundError()
        approver = parse_owner_login_key(identity.login_key)

        def operation(connection: Connection) -> ApprovalResult:
            row = _lock_record(connection, candidate.record_id)
            if row is None:
                raise RecordNotFoundError()
            version = int(row["version"])
            if version != candidate.version:
                raise RecordConflictError()
            projection = _build_projection(connection, row)
            if (
                projection.digest() != candidate.digest
                or projection.analysis_run_id != candidate.analysis_run_id
            ):
                raise RecordConflictError()
            if (
                str(row["visibility"]) == RecordVisibility.FAMILY.value
                and row["approved_projection_json"] is not None
                and row["approved_analysis_run_id"] == projection.analysis_run_id
                and _same_approval_substance(
                    projection_from_storage(str(row["approved_projection_json"])),
                    projection,
                )
            ):
                return ApprovalResult(
                    record_id=str(row["id"]),
                    version=version,
                    changed=False,
                )
            timeline = row["timeline_entered_at_ms"]
            if timeline is None:
                timeline = approved_at_ms
            if int(timeline) < int(row["completed_at_ms"] or 0):
                raise RecordConflictError()
            if approved_at_ms < int(row["completed_at_ms"]) or approved_at_ms < int(timeline):
                raise RecordConflictError()
            connection.execute(
                update(kronika_records)
                .where(kronika_records.c.id == row["id"])
                .values(
                    visibility=RecordVisibility.FAMILY.value,
                    timeline_entered_at_ms=int(timeline),
                    version=version + 1,
                    approved_analysis_run_id=projection.analysis_run_id,
                    approved_by_login_key=approver,
                    approved_at_ms=approved_at_ms,
                    approved_record_version=version,
                    approved_projection_json=projection.canonical_json(),
                )
            )
            if projection.record_kind is RecordKind.MEDIA:
                _replace_approved_media(connection, row_id=str(row["id"]), projection=projection)
            return ApprovalResult(
                record_id=str(row["id"]),
                version=version + 1,
                changed=True,
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (RecordNotFoundError, RecordConflictError, RecordValueError, RecordStorageIntegrityError):
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise RecordRepositoryError() from exc

    def withdraw(
        self,
        identity: IdentityContext,
        *,
        record_id: str,
        expected_version: int,
    ) -> ApprovalResult:
        if not may_approve(identity):
            raise RecordNotFoundError()

        def operation(connection: Connection) -> ApprovalResult:
            row = _lock_record(connection, record_id)
            if row is None:
                raise RecordNotFoundError()
            version = int(row["version"])
            if version != expected_version:
                raise RecordConflictError()
            if str(row["visibility"]) == RecordVisibility.PRIVATE.value:
                return ApprovalResult(record_id=str(row["id"]), version=version, changed=False)
            connection.execute(
                update(kronika_records)
                .where(kronika_records.c.id == row["id"])
                .values(
                    visibility=RecordVisibility.PRIVATE.value,
                    version=version + 1,
                )
            )
            return ApprovalResult(record_id=str(row["id"]), version=version + 1, changed=True)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (RecordNotFoundError, RecordConflictError):
            raise
        except SQLAlchemyError as exc:
            raise RecordRepositoryError() from exc

    def _list(
        self,
        identity: IdentityContext,
        query: RecordPageQuery,
        *,
        owner_login_key: str | None,
        timeline_only: bool,
    ) -> RecordPage:
        if not isinstance(identity, IdentityContext) or not identity.login_key:
            raise RecordNotFoundError()

        def operation(connection: Connection) -> RecordPage:
            statement = select(kronika_records.c.id)
            if owner_login_key is not None:
                statement = statement.where(
                    kronika_records.c.owner_login_key == owner_login_key
                )
            if timeline_only:
                statement = statement.where(
                    kronika_records.c.visibility == RecordVisibility.FAMILY.value,
                    kronika_records.c.approved_projection_json.is_not(None),
                    kronika_records.c.timeline_entered_at_ms.is_not(None),
                )
                order = (
                    kronika_records.c.timeline_entered_at_ms.desc(),
                    kronika_records.c.id.asc(),
                )
            else:
                order = (
                    kronika_records.c.created_at_ms.desc(),
                    kronika_records.c.id.asc(),
                )
                if owner_login_key is None and query.visibility is not None:
                    statement = statement.where(
                        kronika_records.c.visibility == query.visibility.value
                    )
            if query.kind is not None:
                statement = statement.where(kronika_records.c.kind == query.kind.value)
            if query.content_category is not None:
                if timeline_only:
                    statement = statement.where(
                        kronika_records.c.id.in_(
                            select(kronika_approved_media.c.record_id).where(
                                kronika_approved_media.c.content_category
                                == query.content_category
                            )
                        )
                    )
                else:
                    statement = statement.where(
                        kronika_records.c.media_id.in_(
                            select(media_metadata.c.media_id).where(
                                media_metadata.c.content_category == query.content_category
                            )
                        )
                    )
            filtered = statement.subquery()
            total = connection.execute(
                select(func.count()).select_from(filtered)
            ).scalar_one()
            rows = connection.execute(
                select(kronika_records)
                .where(kronika_records.c.id.in_(select(filtered.c.id)))
                .order_by(*order)
                .limit(query.limit)
                .offset(query.offset)
            ).mappings().all()
            labels = _summary_labels(connection, rows, timeline_only=timeline_only)
            items = []
            for row in rows:
                decision = (
                    READ_APPROVED
                    if timeline_only
                    else decide_bound_read(identity, _bound_view(row))
                )
                title, category = labels.get(str(row["id"]), (None, None))
                items.append(
                    _summary(
                        row,
                        decision,
                        display_title=title,
                        content_category=category,
                    )
                )
            return RecordPage(
                items=tuple(items),
                total=int(total),
                limit=query.limit,
                offset=query.offset,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise RecordRepositoryError() from exc


def reject_if_media_bound(connection: Connection, media_id: str) -> None:
    """Raise a conflict when a common record is bound to this media."""
    row = connection.execute(
        select(kronika_records.c.id).where(kronika_records.c.media_id == media_id)
    ).first()
    if row is not None:
        raise RecordConflictError()


def bump_bound_record_version(connection: Connection, media_id: str) -> None:
    """Increment version when an already-bound record's working state changes."""
    connection.execute(
        update(kronika_records)
        .where(kronika_records.c.media_id == media_id)
        .values(version=kronika_records.c.version + 1)
    )


def note_successful_analysis(connection: Connection, media_id: str, run_id: str) -> None:
    """Point a bound record at a successful run without touching the approval."""
    run = connection.execute(
        select(
            media_analysis_runs.c.id,
            media_analysis_runs.c.media_id,
            media_analysis_runs.c.state,
            media_analysis_runs.c.result_json,
        ).where(media_analysis_runs.c.id == run_id)
    ).mappings().first()
    if run is None:
        return
    if str(run["media_id"]) != media_id or str(run["state"]) != "analyzed":
        return
    if not run["result_json"]:
        return
    bound = connection.execute(
        select(kronika_records.c.id).where(kronika_records.c.media_id == media_id)
    ).first()
    if bound is None:
        return
    connection.execute(
        update(kronika_records)
        .where(kronika_records.c.media_id == media_id)
        .values(
            latest_successful_analysis_run_id=run_id,
            completed_at_ms=func.coalesce(
                kronika_records.c.completed_at_ms,
                select(media_analysis_runs.c.completed_at_ms).where(
                    media_analysis_runs.c.id == run_id
                ).scalar_subquery(),
            ),
            version=kronika_records.c.version + 1,
        )
    )


def _lock_record(connection: Connection, record_id: str) -> Mapping[str, object] | None:
    try:
        parsed = RecordId.from_string(record_id)
    except RecordValueError:
        return None
    return connection.execute(
        select(kronika_records).where(kronika_records.c.id == parsed.to_string())
    ).mappings().first()


def _bound_view(row: Mapping[str, object]) -> BoundRecordView:
    approved = row["approved_projection_json"] is not None and row["approved_by_login_key"] is not None
    return BoundRecordView(
        owner_login_key=str(row["owner_login_key"]),
        kind=RecordKind(str(row["kind"])),
        visibility=RecordVisibility(str(row["visibility"])),
        completed=row["completed_at_ms"] is not None,
        approved=bool(approved),
    )


_TITLE_CODE_POINTS = 240
_CONTENT_CATEGORIES = frozenset({"general", "meme", "movie", "youtube"})


def _question_display_title(question: object) -> str | None:
    """Collapse whitespace and keep at most 240 code points, ellipsis included."""
    if not isinstance(question, str):
        return None
    collapsed = " ".join(question.split())
    if not collapsed:
        return None
    if len(collapsed) <= _TITLE_CODE_POINTS:
        return collapsed
    return collapsed[: _TITLE_CODE_POINTS - 1] + "…"


def _media_display_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _category_value(value: object) -> str | None:
    if not isinstance(value, str) or value not in _CONTENT_CATEGORIES:
        return None
    return value


def _summary_labels(
    connection: Connection,
    rows: list[Mapping[str, object]],
    *,
    timeline_only: bool,
) -> dict[str, tuple[str | None, str | None]]:
    """Title and category columns only. Answers and locations stay unloaded."""
    labels: dict[str, tuple[str | None, str | None]] = {}
    if not rows:
        return labels
    ids = [str(row["id"]) for row in rows]
    if timeline_only:
        media_rows = connection.execute(
            select(
                kronika_approved_media.c.record_id,
                kronika_approved_media.c.display_title,
                kronika_approved_media.c.content_category,
            ).where(kronika_approved_media.c.record_id.in_(ids))
        ).mappings()
        for item in media_rows:
            labels[str(item["record_id"])] = (
                _media_display_title(item["display_title"]),
                _category_value(item["content_category"]),
            )
    else:
        media_rows = connection.execute(
            select(
                kronika_records.c.id,
                media_metadata.c.display_title,
                media_metadata.c.content_category,
            )
            .select_from(
                kronika_records.outerjoin(
                    media_metadata,
                    media_metadata.c.media_id == kronika_records.c.media_id,
                )
            )
            .where(
                kronika_records.c.id.in_(ids),
                kronika_records.c.kind == RecordKind.MEDIA.value,
            )
        ).mappings()
        for item in media_rows:
            labels[str(item["id"])] = (
                _media_display_title(item["display_title"]),
                _category_value(item["content_category"]),
            )
    document_rows = connection.execute(
        select(
            kronika_records.c.id,
            kronika_documents.c.question_text,
        )
        .select_from(
            kronika_records.join(
                kronika_documents,
                kronika_documents.c.id == kronika_records.c.document_id,
            )
        )
        .where(kronika_records.c.id.in_(ids))
    ).mappings()
    for item in document_rows:
        labels[str(item["id"])] = (_question_display_title(item["question_text"]), None)
    return labels


def _summary(
    row: Mapping[str, object],
    decision: str,
    *,
    display_title: str | None = None,
    content_category: str | None = None,
) -> RecordSummary:
    return RecordSummary(
        record_id=str(row["id"]),
        kind=RecordKind(str(row["kind"])),
        owner_login_key=str(row["owner_login_key"]),
        visibility=RecordVisibility(str(row["visibility"])),
        created_at_ms=int(row["created_at_ms"]),
        completed_at_ms=None if row["completed_at_ms"] is None else int(row["completed_at_ms"]),
        timeline_entered_at_ms=(
            None
            if row["timeline_entered_at_ms"] is None
            else int(row["timeline_entered_at_ms"])
        ),
        version=int(row["version"]),
        media_id=None if row["media_id"] is None else str(row["media_id"]),
        read_decision=decision,
        display_title=display_title,
        content_category=content_category,
    )


def _detail_from_row(
    connection: Connection,
    row: Mapping[str, object],
    decision: str,
) -> RecordDetail:
    document = None
    if row["document_id"] is not None:
        stored = connection.execute(
            select(kronika_documents).where(kronika_documents.c.id == row["document_id"])
        ).mappings().one()
        document = document_from_storage(**_document_kwargs(stored))
    projection = None
    if decision == READ_APPROVED:
        if row["approved_projection_json"] is None:
            raise RecordNotFoundError()
        projection = projection_from_storage(row["approved_projection_json"])
        document = projection.document
    elif decision == READ_CURRENT and row["approved_projection_json"] is not None:
        projection = None
    return RecordDetail(
        summary=_summary(row, decision),
        projection=projection,
        document=document,
        version=int(row["version"]),
    )


def _document_kwargs(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "document_id": row["id"],
        "operation_id": row["operation_id"],
        "kind": row["kind"],
        "question_text": row["question_text"],
        "answer_text": row["answer_text"],
        "citations_json": row["citations_json"],
        "completion_evidence_json": row["completion_evidence_json"],
        "created_at_ms": row["created_at_ms"],
        "completed_at_ms": row["completed_at_ms"],
    }


def _same_approval_substance(
    stored: ApprovalProjection,
    current: ApprovalProjection,
) -> bool:
    """Compare frozen approval content without the optimistic version token."""
    return stored.canonical_json() == _projection_at_version(
        current, stored.record_version
    ).canonical_json()


def _projection_at_version(
    projection: ApprovalProjection,
    record_version: int,
) -> ApprovalProjection:
    return ApprovalProjection(
        schema_version=projection.schema_version,
        record_kind=projection.record_kind,
        record_version=record_version,
        analysis_run_id=projection.analysis_run_id,
        media_id=projection.media_id,
        display_title=projection.display_title,
        description=projection.description,
        content_category=projection.content_category,
        acquisition_source=projection.acquisition_source,
        creator_attribution_kind=projection.creator_attribution_kind,
        creator_stable_id=projection.creator_stable_id,
        creator_handle=projection.creator_handle,
        creator_display_name=projection.creator_display_name,
        cover_artifact_digest=projection.cover_artifact_digest,
        tags=projection.tags,
        locations=projection.locations,
        analysis_result=projection.analysis_result,
        document=projection.document,
    )


def _same_document(stored: CompletedDocument, submitted: CompletedDocument) -> bool:
    return (
        stored.kind is submitted.kind
        and stored.question_text == submitted.question_text
        and stored.answer_text == submitted.answer_text
        and stored.citations_json() == submitted.citations_json()
        and stored.evidence_json() == submitted.evidence_json()
    )


def _build_projection(connection: Connection, row: Mapping[str, object]) -> ApprovalProjection:
    kind = RecordKind(str(row["kind"]))
    if row["completed_at_ms"] is None:
        raise RecordConflictError()
    if kind is RecordKind.MEDIA:
        return _media_projection(connection, row)
    if row["document_id"] is None:
        raise RecordConflictError()
    stored = connection.execute(
        select(kronika_documents).where(kronika_documents.c.id == row["document_id"])
    ).mappings().first()
    if stored is None:
        raise RecordConflictError()
    document = document_from_storage(**_document_kwargs(stored))
    if document.operation_id != row["final_operation_id"] or document.kind is not kind:
        raise RecordConflictError()
    if int(document.completed_at_ms) != int(row["completed_at_ms"]):
        raise RecordConflictError()
    return ApprovalProjection(
        schema_version=1,
        record_kind=kind,
        record_version=int(row["version"]),
        analysis_run_id=None,
        media_id=None,
        display_title=None,
        description=None,
        content_category=None,
        acquisition_source=None,
        creator_attribution_kind=None,
        creator_stable_id=None,
        creator_handle=None,
        creator_display_name=None,
        cover_artifact_digest=None,
        tags=(),
        locations=(),
        analysis_result=None,
        document=document,
    )


def _media_projection(connection: Connection, row: Mapping[str, object]) -> ApprovalProjection:
    media_id = str(row["media_id"])
    run_id = row["latest_successful_analysis_run_id"]
    if run_id is None:
        raise RecordConflictError()
    run = connection.execute(
        select(media_analysis_runs).where(media_analysis_runs.c.id == run_id)
    ).mappings().first()
    if (
        run is None
        or str(run["media_id"]) != media_id
        or str(run["state"]) != "analyzed"
        or not run["result_json"]
    ):
        raise RecordConflictError()
    metadata = connection.execute(
        select(media_metadata).where(media_metadata.c.media_id == media_id)
    ).mappings().first()
    tag_count = connection.execute(
        select(func.count())
        .select_from(media_canonical_tags)
        .where(media_canonical_tags.c.media_id == media_id)
    ).scalar_one()
    readiness = derive_content_publication_readiness(
        display_title=None if metadata is None else metadata["display_title"],
        description=None if metadata is None else metadata["description"],
        canonical_tag_count=int(tag_count),
    )
    if metadata is None or not readiness.ready:
        raise RecordConflictError()
    tag_rows = connection.execute(
        select(
            media_canonical_tags.c.tag_key,
            canonical_tags.c.display_name,
            media_canonical_tags.c.position,
        )
        .select_from(
            media_canonical_tags.join(
                canonical_tags,
                canonical_tags.c.key == media_canonical_tags.c.tag_key,
            )
        )
        .where(media_canonical_tags.c.media_id == media_id)
        .order_by(media_canonical_tags.c.position.asc(), media_canonical_tags.c.tag_key.asc())
    ).mappings()
    tags = tuple(
        ApprovedTagSnapshot(
            key=str(item["tag_key"]),
            display_name=str(item["display_name"]),
            position=int(item["position"]),
        )
        for item in tag_rows
    )
    location_rows = connection.execute(
        select(physical_media_locations)
        .where(physical_media_locations.c.media_id == media_id)
        .order_by(physical_media_locations.c.id.asc())
    ).mappings()
    locations = tuple(
        ApprovedLocationSnapshot(
            location_id=str(item["id"]),
            library_id=str(item["library_id"]),
            relative_path=str(item["relative_path"]),
            availability=str(item["availability"]),
            observed_size_bytes=(
                None if item["observed_size_bytes"] is None else int(item["observed_size_bytes"])
            ),
            observed_mtime_ns=(
                None if item["observed_mtime_ns"] is None else int(item["observed_mtime_ns"])
            ),
        )
        for item in location_rows
    )
    cover = connection.execute(
        select(media_covers.c.artifact_digest).where(media_covers.c.media_id == media_id)
    ).scalar()
    return ApprovalProjection(
        schema_version=1,
        record_kind=RecordKind.MEDIA,
        record_version=int(row["version"]),
        analysis_run_id=str(run_id),
        media_id=media_id,
        display_title=str(metadata["display_title"]),
        description=str(metadata["description"]),
        content_category=str(metadata["content_category"]),
        acquisition_source=str(metadata["acquisition_source"]),
        creator_attribution_kind=(
            None
            if metadata["creator_attribution_kind"] is None
            else str(metadata["creator_attribution_kind"])
        ),
        creator_stable_id=(
            None if metadata["creator_stable_id"] is None else str(metadata["creator_stable_id"])
        ),
        creator_handle=(
            None if metadata["creator_handle"] is None else str(metadata["creator_handle"])
        ),
        creator_display_name=(
            None
            if metadata["creator_display_name"] is None
            else str(metadata["creator_display_name"])
        ),
        cover_artifact_digest=None if cover is None else str(cover),
        tags=tags,
        locations=locations,
        analysis_result=_normalized_analysis(str(run["result_json"])),
        document=None,
    )


def _normalized_analysis(result_json: str) -> dict[str, object]:
    try:
        parsed = json.loads(result_json)
    except json.JSONDecodeError as exc:
        raise RecordConflictError() from exc
    if type(parsed) is not dict:
        raise RecordConflictError()
    return _drop_forbidden(parsed)


def _drop_forbidden(value: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, item in value.items():
        if str(key).casefold() in _FORBIDDEN_RESULT_KEYS:
            continue
        if type(item) is dict:
            cleaned[str(key)] = _drop_forbidden(item)
        else:
            cleaned[str(key)] = item
    return cleaned


def _replace_approved_media(
    connection: Connection,
    *,
    row_id: str,
    projection: ApprovalProjection,
) -> None:
    connection.execute(
        delete(kronika_approved_media_tags).where(
            kronika_approved_media_tags.c.record_id == row_id
        )
    )
    connection.execute(
        delete(kronika_approved_media_locations).where(
            kronika_approved_media_locations.c.record_id == row_id
        )
    )
    connection.execute(
        delete(kronika_approved_media).where(kronika_approved_media.c.record_id == row_id)
    )
    connection.execute(
        insert(kronika_approved_media).values(
            record_id=row_id,
            media_id=projection.media_id,
            approval_version=projection.record_version,
            content_category=projection.content_category,
            acquisition_source=projection.acquisition_source,
            display_title=projection.display_title,
            description=projection.description,
            creator_attribution_kind=projection.creator_attribution_kind,
            creator_stable_id=projection.creator_stable_id,
            creator_handle=projection.creator_handle,
            creator_display_name=projection.creator_display_name,
            cover_artifact_digest=projection.cover_artifact_digest,
        )
    )
    if projection.tags:
        connection.execute(
            insert(kronika_approved_media_tags),
            [
                {
                    "record_id": row_id,
                    "tag_key": tag.key,
                    "display_name": tag.display_name,
                    "position": tag.position,
                }
                for tag in projection.tags
            ],
        )
    if projection.locations:
        connection.execute(
            insert(kronika_approved_media_locations),
            [
                {
                    "record_id": row_id,
                    "location_id": item.location_id,
                    "library_id": item.library_id,
                    "relative_path": item.relative_path,
                    "availability": item.availability,
                    "observed_size_bytes": item.observed_size_bytes,
                    "observed_mtime_ns": item.observed_mtime_ns,
                }
                for item in projection.locations
            ],
        )


class SqliteResearchResultCompletion:
    """Atomic research completion over documents, records and the request bind.

    One immediate transaction creates the immutable completed document, the
    common record with a server-derived owner, and the research-request
    ``record_id`` binding. An exact replay returns the existing binding.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._engine = engine
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def complete(self, completion: ResultCompletion) -> ResultCompletionReceipt:
        def operation(connection: Connection) -> ResultCompletionReceipt:
            request = (
                connection.execute(
                    select(research_requests).where(
                        research_requests.c.operation_id
                        == completion.operation_id
                    )
                )
                .mappings()
                .first()
            )
            if request is None:
                raise ResearchStoreError(ResearchErrorCode.STORAGE)
            if request["record_id"] is not None:
                return ResultCompletionReceipt(
                    operation_id=completion.operation_id,
                    state=ResearchLifecycleState.SAVED,
                )
            now_ms = self._clock_ms()
            created_at_ms = int(request["created_at_ms"])
            document = CompletedDocument(
                document_id=DocumentId.new(),
                operation_id=completion.operation_id,
                kind=RecordKind(str(request["kind"])),
                question_text=str(request["prompt_text"]),
                answer_text=completion.answer.text,
                citations=tuple(
                    NormalizedCitation(url=citation.url, title=citation.title)
                    for citation in completion.answer.citations
                ),
                evidence=completion.answer.evidence,
                created_at_ms=created_at_ms,
                completed_at_ms=max(now_ms, created_at_ms),
            )
            record_id = RecordId.new()
            connection.execute(
                insert(kronika_documents).values(
                    id=document.document_id.to_string(),
                    operation_id=document.operation_id,
                    kind=document.kind.value,
                    question_text=document.question_text,
                    answer_text=document.answer_text,
                    citations_json=document.citations_json(),
                    completion_evidence_json=document.evidence_json(),
                    created_at_ms=document.created_at_ms,
                    completed_at_ms=document.completed_at_ms,
                )
            )
            connection.execute(
                insert(kronika_records).values(
                    id=record_id.to_string(),
                    kind=document.kind.value,
                    owner_login_key=parse_owner_login_key(
                        str(request["owner_login_key"])
                    ),
                    visibility=RecordVisibility.PRIVATE.value,
                    document_id=document.document_id.to_string(),
                    final_operation_id=document.operation_id,
                    created_at_ms=document.created_at_ms,
                    completed_at_ms=document.completed_at_ms,
                    version=1,
                )
            )
            connection.execute(
                update(research_requests)
                .where(
                    research_requests.c.operation_id == completion.operation_id
                )
                .values(record_id=record_id.to_string(), updated_at_ms=now_ms)
            )
            return ResultCompletionReceipt(
                operation_id=completion.operation_id,
                state=ResearchLifecycleState.SAVED,
            )

        return run_in_immediate_transaction(self._engine, operation)
