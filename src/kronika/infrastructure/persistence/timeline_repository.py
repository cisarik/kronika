"""Authorized Timeline union. One snapshot, two or three SELECTs, no file I/O."""

from __future__ import annotations

from sqlalchemy import (
    and_,
    case,
    cast,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    true,
    union,
    union_all,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import ColumnElement
from sqlalchemy.types import Text

from kronika.application.ports.timeline import (
    TimelineItem,
    TimelinePage,
    TimelinePageQuery,
    TimelineUnavailableError,
)
from kronika.domain.identity_access import (
    CAPABILITY_GALLERY_READ,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_RECORDS_APPROVE,
    CAPABILITY_RESEARCH_RUN,
    ROLE_ADMIN,
    IdentityContext,
)
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
)
from kronika.infrastructure.persistence.catalog_schema import (
    kronika_approved_media,
    kronika_approved_media_locations,
    kronika_documents,
    kronika_records,
    logical_media,
    media_analysis_runs,
    media_content_publications,
    media_metadata,
    physical_media_locations,
    research_requests,
    upload_publications,
    upload_sessions,
    x_assets,
    x_post_claims,
    youtube_acquisition_claims,
)

_TITLE_CODE_POINTS = 240
_UNTITLED_MEDIA = "Untitled media"
_LIKE_ESCAPE = "\\"
_PREVIEW_KINDS = ("video", "animated_image")
_YOUTUBE_SUCCESS = ("cataloged", "duplicate_resolved")
_STATUS_GROUPS = {
    "unanalysed": ("not_requested",),
    "in_progress": (
        "pending",
        "analyzing",
        "admitted",
        "submitting",
        "running",
        "validating",
        "cancel_requested",
    ),
    "complete": ("analyzed", "saved"),
    "failed": (
        "failed",
        "refused",
        "incomplete",
        "timeout",
        "submission_unknown",
    ),
    "cancelled": ("cancelled",),
}


def _text(prefix: str, column: ColumnElement[object]) -> ColumnElement[object]:
    return literal(prefix).op("||")(column)


def _null_text() -> ColumnElement[object]:
    return cast(literal(None), Text())


def _like_pattern(value: str) -> str:
    escaped = (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def _display_title(kind: str, value: object) -> str | None:
    collapsed = " ".join(value.split()) if isinstance(value, str) else ""
    if not collapsed:
        return _UNTITLED_MEDIA if kind == "media" else None
    if len(collapsed) <= _TITLE_CODE_POINTS:
        return collapsed
    return collapsed[: _TITLE_CODE_POINTS - 1] + "…"


class SqliteTimelineReader:
    """Read the chronicle for one verified caller."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_page(
        self, identity: IdentityContext, query: TimelinePageQuery
    ) -> TimelinePage:
        if not isinstance(identity, IdentityContext) or not identity.login_key:
            raise TimelineUnavailableError()
        with self._engine.connect() as connection:
            # SQLAlchemy's logical transaction does not by itself keep one
            # sqlite3 snapshot across the count, page and locator statements.
            raw = connection.connection.driver_connection
            raw.execute("BEGIN")
            try:
                total, broken = self._count(connection, identity, query)
                if broken:
                    raise TimelineUnavailableError()
                rows = self._page(connection, identity, query)
                locators = (
                    self._locators(connection, rows) if _locator_needed(rows) else {}
                )
            except TimelineUnavailableError:
                raw.execute("ROLLBACK")
                raise
            except SQLAlchemyError as exc:
                raw.execute("ROLLBACK")
                raise TimelineUnavailableError() from exc
            else:
                raw.execute("ROLLBACK")
        items = tuple(
            _item(row, locators.get(str(row["media_id"] or ""))) for row in rows
        )
        return TimelinePage(
            items=items,
            total=total,
            limit=query.limit,
            offset=query.offset,
        )

    def _count(
        self,
        connection: Connection,
        identity: IdentityContext,
        query: TimelinePageQuery,
    ) -> tuple[int, bool]:
        filtered = self._filtered(identity, query).subquery("timeline_count")
        row = connection.execute(
            select(
                func.count().label("total"),
                func.coalesce(func.sum(filtered.c.integrity_error), 0).label("broken"),
            ).select_from(filtered)
        ).one()
        return int(row.total), int(row.broken) > 0

    def _page(
        self,
        connection: Connection,
        identity: IdentityContext,
        query: TimelinePageQuery,
    ) -> list[object]:
        filtered = self._filtered(identity, query).subquery("timeline_page")
        return list(
            connection.execute(
                select(filtered)
                .order_by(
                    filtered.c.created_at_ms.desc(),
                    filtered.c.item_id.asc(),
                )
                .limit(query.limit)
                .offset(query.offset)
            ).mappings()
        )

    def _locators(self, connection: Connection, rows: list[object]) -> dict[str, str]:
        approved_ids = [
            str(row["media_id"])
            for row in rows
            if row["read_decision"] == "approved" and row["media_kind"] in _PREVIEW_KINDS
        ]
        current_ids = [
            str(row["media_id"])
            for row in rows
            if row["read_decision"] in {"current", "legacy"}
            and row["media_kind"] in _PREVIEW_KINDS
        ]
        branches = []
        if approved_ids:
            branches.append(
                select(
                    kronika_approved_media.c.media_id.label("media_id"),
                    kronika_approved_media_locations.c.location_id.label("location_id"),
                )
                .select_from(
                    kronika_approved_media_locations.join(
                        kronika_approved_media,
                        kronika_approved_media.c.record_id
                        == kronika_approved_media_locations.c.record_id,
                    )
                )
                .where(
                    kronika_approved_media.c.media_id.in_(approved_ids),
                    kronika_approved_media_locations.c.availability == "available",
                )
            )
        if current_ids:
            branches.append(
                select(
                    physical_media_locations.c.media_id.label("media_id"),
                    physical_media_locations.c.id.label("location_id"),
                ).where(
                    physical_media_locations.c.media_id.in_(current_ids),
                    physical_media_locations.c.availability == "available",
                )
            )
        if not branches:
            return {}
        combined = branches[0] if len(branches) == 1 else union_all(*branches)
        ranked = combined.subquery("timeline_locations")
        found = connection.execute(
            select(ranked.c.media_id, func.min(ranked.c.location_id)).group_by(
                ranked.c.media_id
            )
        )
        return {
            str(media_id): str(location_id)
            for media_id, location_id in found
            if location_id is not None
        }

    def _filtered(self, identity: IdentityContext, query: TimelinePageQuery):
        projection = _union(identity).subquery("timeline_union")
        statement = select(projection)
        if query.kind is not None:
            statement = statement.where(projection.c.kind == query.kind)
        if query.content_category is not None:
            statement = statement.where(
                projection.c.content_category == query.content_category
            )
        if query.visibility is not None:
            statement = statement.where(projection.c.visibility == query.visibility)
        if query.publication is not None:
            statement = statement.where(
                projection.c.publication_state == query.publication
            )
        if query.status is not None:
            statement = statement.where(
                projection.c.status.in_(_STATUS_GROUPS[query.status])
            )
        if query.q is not None:
            statement = statement.where(
                projection.c.search_text.collate("NOCASE").like(
                    _like_pattern(query.q),
                    escape=_LIKE_ESCAPE,
                )
            )
        return statement


def _locator_needed(rows: list[object]) -> bool:
    return any(row["media_kind"] in _PREVIEW_KINDS for row in rows)


def _item(row: object, locator: str | None) -> TimelineItem:
    kind = str(row["kind"])
    media_kind = None if row["media_kind"] is None else str(row["media_kind"])
    return TimelineItem(
        item_id=str(row["item_id"]),
        kind=kind,
        media_kind=media_kind,
        record_id=None if row["record_id"] is None else str(row["record_id"]),
        media_id=None if row["media_id"] is None else str(row["media_id"]),
        operation_id=None if row["operation_id"] is None else str(row["operation_id"]),
        display_title=_display_title(kind, row["title_source"]),
        content_category=(
            None if row["content_category"] is None else str(row["content_category"])
        ),
        created_at_ms=int(row["created_at_ms"]),
        status=str(row["status"]),
        visibility=str(row["visibility"]),
        publication_state=str(row["publication_state"]),
        read_decision=str(row["read_decision"]),
        preview_location_id=locator if media_kind in _PREVIEW_KINDS else None,
    )


def _union(identity: IdentityContext):
    login = identity.login_key
    return union_all(
        _media_arm(
            login,
            media_admin=identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ),
            enabled=identity.has_capability(CAPABILITY_GALLERY_READ),
        ),
        _question_arm(
            login,
            media_admin=identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ),
        ),
        _request_arm(
            login,
            enabled=identity.has_capability(CAPABILITY_RESEARCH_RUN),
            request_admin=(
                identity.role == ROLE_ADMIN
                or identity.has_capability(CAPABILITY_RECORDS_APPROVE)
            ),
        ),
    )


def _columns(
    *,
    item_id: ColumnElement[object],
    kind: ColumnElement[object],
    media_kind: ColumnElement[object],
    record_id: ColumnElement[object],
    media_id: ColumnElement[object],
    operation_id: ColumnElement[object],
    title: ColumnElement[object],
    category: ColumnElement[object],
    created_at_ms: ColumnElement[object],
    status: ColumnElement[object],
    visibility: ColumnElement[object],
    publication: ColumnElement[object],
    decision: ColumnElement[object],
    integrity: ColumnElement[object],
):
    return (
        item_id.label("item_id"),
        kind.label("kind"),
        media_kind.label("media_kind"),
        record_id.label("record_id"),
        media_id.label("media_id"),
        operation_id.label("operation_id"),
        title.label("title_source"),
        title.label("search_text"),
        category.label("content_category"),
        created_at_ms.label("created_at_ms"),
        status.label("status"),
        visibility.label("visibility"),
        publication.label("publication_state"),
        decision.label("read_decision"),
        integrity.label("integrity_error"),
    )


def _latest_runs():
    ranked = (
        select(
            media_analysis_runs.c.media_id.label("media_id"),
            media_analysis_runs.c.state.label("state"),
            func.row_number()
            .over(
                partition_by=media_analysis_runs.c.media_id,
                order_by=(
                    media_analysis_runs.c.created_at_ms.desc(),
                    media_analysis_runs.c.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            media_analysis_runs.c.analysis_definition
            == AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION
        )
        .subquery("timeline_ranked_runs")
    )
    return (
        select(ranked.c.media_id, ranked.c.state)
        .where(ranked.c.rank == 1)
        .subquery("timeline_latest_runs")
    )


def _requester_ids(login: str):
    upload_ids = (
        select(upload_publications.c.media_id.label("media_id"))
        .select_from(
            upload_publications.join(
                upload_sessions,
                upload_sessions.c.id == upload_publications.c.upload_id,
            )
        )
        .where(
            upload_publications.c.media_id.is_not(None),
            upload_sessions.c.created_by_login_key == login,
        )
    )
    youtube_ids = select(
        youtube_acquisition_claims.c.media_id.label("media_id")
    ).where(
        youtube_acquisition_claims.c.media_id.is_not(None),
        youtube_acquisition_claims.c.created_by_login_key == login,
        youtube_acquisition_claims.c.state.in_(_YOUTUBE_SUCCESS),
    )
    x_ids = (
        select(x_assets.c.media_id.label("media_id"))
        .select_from(
            x_assets.join(x_post_claims, x_assets.c.claim_id == x_post_claims.c.id)
        )
        .where(
            x_assets.c.media_id.is_not(None),
            x_assets.c.state == "cataloged",
            x_post_claims.c.created_by_login_key == login,
        )
    )
    return union(upload_ids, youtube_ids, x_ids).subquery("timeline_requesters")


def _media_arm(login: str, *, media_admin: bool, enabled: bool):
    records = kronika_records.alias("timeline_media_records")
    approved = kronika_approved_media.alias("timeline_approved_media")
    latest = _latest_runs()
    requesters = _requester_ids(login)
    bound = records.c.id.is_not(None)
    published = media_content_publications.c.media_id.is_not(None)
    requester = requesters.c.media_id.is_not(None)
    approved_family = and_(
        records.c.visibility == "family",
        records.c.approved_projection_json.is_not(None),
        records.c.completed_at_ms.is_not(None),
    )
    if media_admin:
        decision: ColumnElement[object] = literal("current")
        included: ColumnElement[object] = true()
    else:
        decision = case(
            (and_(bound, records.c.owner_login_key == login), literal("current")),
            (and_(bound, approved_family), literal("approved")),
            (and_(~bound, published), literal("legacy")),
            (and_(~bound, requester), literal("legacy")),
            else_=literal(None),
        )
        included = decision.is_not(None)
    if not enabled:
        included = false()
    visibility = case(
        (and_(bound, records.c.visibility == "family"), literal("family")),
        (and_(~bound, published), literal("family")),
        else_=literal("private"),
    )
    publication = case(
        (and_(bound, records.c.visibility == "family"), literal("approved")),
        (
            and_(bound, records.c.approved_projection_json.is_not(None)),
            literal("withdrawn"),
        ),
        (and_(~bound, published), literal("legacy_published")),
        else_=literal("unpublished"),
    )
    status = case(
        (decision == literal("approved"), literal("analyzed")),
        (latest.c.state.is_(None), literal("not_requested")),
        else_=latest.c.state,
    )
    title = case(
        (decision == literal("approved"), approved.c.display_title),
        else_=media_metadata.c.display_title,
    )
    category = case(
        (decision == literal("approved"), approved.c.content_category),
        else_=media_metadata.c.content_category,
    )
    json_ok = func.coalesce(
        func.json_valid(records.c.approved_projection_json), 0
    ) == 1
    integrity = case(
        (
            and_(
                decision == literal("approved"),
                or_(approved.c.media_id.is_(None), ~json_ok),
            ),
            literal(1),
        ),
        else_=literal(0),
    )
    return select(
        *_columns(
            item_id=_text("media:", logical_media.c.id),
            kind=literal("media"),
            media_kind=logical_media.c.media_kind,
            record_id=records.c.id,
            media_id=logical_media.c.id,
            operation_id=_null_text(),
            title=title,
            category=category,
            created_at_ms=logical_media.c.created_at_ms,
            status=status,
            visibility=visibility,
            publication=publication,
            decision=decision,
            integrity=integrity,
        )
    ).select_from(
        logical_media.outerjoin(records, records.c.media_id == logical_media.c.id)
        .outerjoin(media_metadata, media_metadata.c.media_id == logical_media.c.id)
        .outerjoin(approved, approved.c.media_id == logical_media.c.id)
        .outerjoin(
            media_content_publications,
            media_content_publications.c.media_id == logical_media.c.id,
        )
        .outerjoin(latest, latest.c.media_id == logical_media.c.id)
        .outerjoin(requesters, requesters.c.media_id == logical_media.c.id)
    ).where(included)


def _question_arm(login: str, *, media_admin: bool):
    approved_family = and_(
        kronika_records.c.visibility == "family",
        kronika_records.c.approved_projection_json.is_not(None),
        kronika_records.c.completed_at_ms.is_not(None),
    )
    if media_admin:
        decision: ColumnElement[object] = literal("current")
        included: ColumnElement[object] = kronika_records.c.kind.in_(
            ("search", "research")
        )
    else:
        decision = case(
            (kronika_records.c.owner_login_key == login, literal("current")),
            (approved_family, literal("approved")),
            else_=literal(None),
        )
        included = and_(
            kronika_records.c.kind.in_(("search", "research")),
            decision.is_not(None),
        )
    projection = kronika_records.c.approved_projection_json
    valid_json = func.coalesce(func.json_valid(projection), 0) == 1
    extracted = func.json_extract(projection, "$.document.question_text")
    approved_text = case((valid_json, extracted), else_=literal(None))
    title = case(
        (decision == literal("approved"), approved_text),
        else_=kronika_documents.c.question_text,
    )
    publication = case(
        (kronika_records.c.visibility == "family", literal("approved")),
        (kronika_records.c.approved_projection_json.is_not(None), literal("withdrawn")),
        else_=literal("unpublished"),
    )
    created = func.coalesce(
        select(research_requests.c.admitted_at_ms)
        .where(research_requests.c.operation_id == kronika_records.c.final_operation_id)
        .scalar_subquery(),
        kronika_records.c.created_at_ms,
    )
    blank_extract = and_(
        valid_json,
        or_(extracted.is_(None), func.length(func.trim(extracted)) == 0),
    )
    integrity = case(
        (
            and_(decision == literal("approved"), or_(~valid_json, blank_extract)),
            literal(1),
        ),
        else_=literal(0),
    )
    return (
        select(
            *_columns(
                item_id=_text("question:", kronika_records.c.final_operation_id),
                kind=kronika_records.c.kind,
                media_kind=_null_text(),
                record_id=kronika_records.c.id,
                media_id=_null_text(),
                operation_id=kronika_records.c.final_operation_id,
                title=title,
                category=_null_text(),
                created_at_ms=created,
                status=literal("saved"),
                visibility=kronika_records.c.visibility,
                publication=publication,
                decision=decision,
                integrity=integrity,
            )
        )
        .select_from(
            kronika_records.join(
                kronika_documents,
                kronika_documents.c.id == kronika_records.c.document_id,
            )
        )
        .where(included)
    )


def _request_arm(login: str, *, enabled: bool, request_admin: bool):
    completed = exists(
        select(literal(1)).where(
            kronika_records.c.final_operation_id == research_requests.c.operation_id
        )
    )
    linked_matches = exists(
        select(literal(1)).where(
            kronika_records.c.id == research_requests.c.record_id,
            kronika_records.c.final_operation_id == research_requests.c.operation_id,
        )
    )
    consistent_completed = and_(
        completed,
        or_(research_requests.c.record_id.is_(None), linked_matches),
    )
    contradictory = and_(
        research_requests.c.record_id.is_not(None),
        ~linked_matches,
    )
    saved_without_record = and_(
        research_requests.c.lifecycle_state == "saved",
        ~completed,
    )
    integrity = case(
        (or_(contradictory, saved_without_record), literal(1)),
        else_=literal(0),
    )
    if request_admin:
        audience: ColumnElement[object] = true()
    else:
        audience = research_requests.c.owner_login_key == login
    included: ColumnElement[object] = and_(~consistent_completed, audience)
    if not enabled:
        included = false()
    return select(
        *_columns(
            item_id=_text("question:", research_requests.c.operation_id),
            kind=research_requests.c.kind,
            media_kind=_null_text(),
            record_id=research_requests.c.record_id,
            media_id=_null_text(),
            operation_id=research_requests.c.operation_id,
            title=research_requests.c.prompt_text,
            category=_null_text(),
            created_at_ms=research_requests.c.admitted_at_ms,
            status=research_requests.c.lifecycle_state,
            visibility=literal("private"),
            publication=literal("unpublished"),
            decision=literal("current"),
            integrity=integrity,
        )
    ).where(included)
