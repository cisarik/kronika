"""SQLite adapter for companion review inbox and requester-private own-history."""

from __future__ import annotations

from sqlalchemy import (
    Integer,
    Text,
    and_,
    cast,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql import Select

from kronika.application.companion_review import (
    CanonicalTagView,
    CompanionReviewApplyCanonical,
    CompanionReviewApplyPublication,
    CompanionReviewApplyResult,
    CompanionReviewCanonicalTag,
    CompanionReviewCodecError,
    CompanionReviewDetail,
    CompanionReviewFieldSource,
    CompanionReviewInboxItem,
    CompanionReviewInboxPage,
    CompanionReviewOpenedResult,
    CompanionReviewSuggestion,
    canonical_field_digest,
    decode_stored_suggestion_result,
    derive_review_readiness,
    eligible_mapped_tag_keys,
    encode_companion_review_cursor,
    encode_companion_review_inbox_cursor,
    inbox_title,
    is_ordered_subsequence,
    map_suggested_tags,
    pending_inbox_title,
)
from kronika.application.ports.companion_review_repository import (
    CompanionReviewAnalysisRunNotFoundError,
    CompanionReviewMediaNotFoundError,
    CompanionReviewMovieExcludedError,
    CompanionReviewRunNotEligibleError,
    CompanionReviewStaleMappingError,
    CompanionReviewStoredResultError,
    CompanionReviewTagLimitConflictError,
    FrameNestCompanionReviewRepositoryError,
)
from kronika.domain.content_publication import ContentPublication, ContentPublicationOrigin
from kronika.domain.identities import MediaId
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    RESULT_SCHEMA_VERSION,
    MediaAnalysisRunId,
)
from kronika.domain.media_classification import (
    AnalysisProfile,
    ContentCategory,
    DEFAULT_ACQUISITION_SOURCE,
    DEFAULT_CONTENT_CATEGORY,
    MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION,
)
from kronika.domain.media_metadata import (
    MAX_MEDIA_TAGS,
    CanonicalTagKey,
    MediaCollectionKey,
    derive_collection_state,
)
from kronika.infrastructure.persistence.catalog_schema import (
    canonical_tags,
    companion_review_field_sources,
    companion_review_open_states,
    companion_review_tag_sources,
    logical_media,
    media_analysis_runs,
    media_canonical_tags,
    media_content_publications,
    media_metadata,
    x_assets,
    x_post_claims,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_REPOSITORY_FAILURE_MESSAGE = "Companion review query failed."
_MEDIA_NOT_FOUND_MESSAGE = "The requested media item was not found."
_MOVIE_EXCLUDED_MESSAGE = "Movie workflows are excluded from companion review."
_STORED_RESULT_MESSAGE = "Stored analysis result is invalid."
_RUN_NOT_FOUND_MESSAGE = "The requested analysis run was not found."
_RUN_NOT_ELIGIBLE_MESSAGE = "The requested analysis run is not eligible."
_STALE_MAPPING_MESSAGE = "Submitted tag keys are not an eligible mapping."
_TAG_LIMIT_MESSAGE = "Applying the selected tags would exceed the canonical tag limit."
_REVIEW_FIELDS = ("display_title", "tags", "description")
_FIELD_DISPLAY_TITLE = "display_title"
_FIELD_DESCRIPTION = "description"
_FIELD_TAGS = "tags"


class SqliteCompanionReviewRepository:
    """Synchronous SQLite adapter for companion review reads."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_inbox(
        self,
        *,
        actor_login_key: str,
        limit: int,
        cursor: tuple[int, bool, str] | None,
    ) -> CompanionReviewInboxPage:
        return self._list_history_page(
            actor_login_key=actor_login_key,
            limit=limit,
            cursor=cursor,
            own_history=False,
        )

    def list_own_history(
        self,
        *,
        actor_login_key: str,
        limit: int,
        cursor: tuple[int, bool, str] | None,
    ) -> CompanionReviewInboxPage:
        return self._list_history_page(
            actor_login_key=actor_login_key,
            limit=limit,
            cursor=cursor,
            own_history=True,
        )

    def _list_history_page(
        self,
        *,
        actor_login_key: str,
        limit: int,
        cursor: tuple[int, bool, str] | None,
        own_history: bool,
    ) -> CompanionReviewInboxPage:
        def operation(connection: Connection) -> CompanionReviewInboxPage:
            latest = _latest_successful_generic().subquery("latest_generic")
            opened = companion_review_open_states
            if own_history:
                unopened_latest = _own_analyzed_latest(
                    latest, actor_login_key
                ).subquery("own_analyzed_latest")
                history_rows = _own_history_rows(latest, actor_login_key)
            else:
                unopened_latest = latest
                history_rows = _analyzed_inbox_rows(latest)
            unopened_count = connection.execute(
                _unopened_count_statement(unopened_latest, actor_login_key)
            ).scalar_one()
            mixed = history_rows.subquery("review_history_rows")
            statement = select(
                mixed,
                opened.c.opened_run_id,
            ).select_from(
                mixed.outerjoin(
                    opened,
                    and_(
                        opened.c.actor_login_key == actor_login_key,
                        opened.c.media_id == mixed.c.media_id,
                    ),
                )
            )
            statement = _apply_inbox_keyset(statement, mixed, cursor)
            statement = statement.order_by(
                mixed.c.activity_at_ms.desc(),
                mixed.c.analyzed.desc(),
                mixed.c.sort_id.desc(),
            ).limit(limit + 1)
            rows = connection.execute(statement).mappings().all()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = tuple(_inbox_item_from_row(row) for row in page_rows)
            next_cursor = None
            if has_more and items:
                last = items[-1]
                next_cursor = encode_companion_review_inbox_cursor(
                    activity_at_ms=(
                        last.completed_at_ms
                        if last.analyzed and last.completed_at_ms is not None
                        else last.created_at_ms
                    ),
                    analyzed=last.analyzed,
                    sort_id=(
                        last.analysis_run_id
                        if last.analyzed and last.analysis_run_id is not None
                        else last.media_id
                    ),
                )
            return CompanionReviewInboxPage(
                items=items,
                unopened_count=int(unopened_count),
                next_cursor=next_cursor,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestCompanionReviewRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestCompanionReviewRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_detail(
        self,
        *,
        media_id: MediaId,
        actor_login_key: str,
        limit: int,
        cursor: tuple[int, str] | None,
    ) -> CompanionReviewDetail:
        del actor_login_key

        def operation(connection: Connection) -> CompanionReviewDetail:
            media_id_text = media_id.to_string()
            exists = connection.execute(
                select(logical_media.c.id).where(logical_media.c.id == media_id_text)
            ).first()
            if exists is None:
                raise CompanionReviewMediaNotFoundError(_MEDIA_NOT_FOUND_MESSAGE)
            metadata_row = connection.execute(
                select(
                    media_metadata.c.display_title,
                    media_metadata.c.description,
                    media_metadata.c.content_category,
                ).where(media_metadata.c.media_id == media_id_text)
            ).mappings().first()
            content_category = DEFAULT_CONTENT_CATEGORY.value
            display_title = None
            description = None
            if metadata_row is not None:
                content_category = str(
                    metadata_row["content_category"] or DEFAULT_CONTENT_CATEGORY.value
                )
                display_title = metadata_row["display_title"]
                description = metadata_row["description"]
                if isinstance(display_title, str) and not display_title.strip():
                    display_title = None
                if isinstance(description, str) and not description.strip():
                    description = None
            if content_category == ContentCategory.MOVIE.value:
                raise CompanionReviewMovieExcludedError(_MOVIE_EXCLUDED_MESSAGE)

            tag_rows = connection.execute(
                select(
                    canonical_tags.c.key,
                    canonical_tags.c.display_name,
                    media_canonical_tags.c.position,
                )
                .select_from(
                    media_canonical_tags.join(
                        canonical_tags,
                        canonical_tags.c.key == media_canonical_tags.c.tag_key,
                    )
                )
                .where(media_canonical_tags.c.media_id == media_id_text)
                .order_by(media_canonical_tags.c.position, canonical_tags.c.key)
            ).mappings().all()
            tags = tuple(
                CompanionReviewCanonicalTag(
                    key=str(row["key"]),
                    display_name=str(row["display_name"]),
                    position=int(row["position"]),
                )
                for row in tag_rows
            )
            publication = _load_publication(connection, media_id_text)
            tag_keys = tuple(tag.key for tag in tags)
            field_sources = _load_field_sources(
                connection,
                media_id=media_id_text,
                display_title=display_title if isinstance(display_title, str) else None,
                description=description if isinstance(description, str) else None,
                tag_keys=tag_keys,
            )
            tag_sources = _load_tag_sources(
                connection, media_id=media_id_text, tag_keys=tag_keys
            )
            definition_catalog = _load_canonical_catalog(connection)
            runs = _load_history_page(
                connection,
                media_id=media_id_text,
                limit=limit,
                cursor=cursor,
            )
            has_more = len(runs) > limit
            page_runs = runs[:limit]
            suggestions = tuple(
                _suggestion_from_run(run, definition_catalog) for run in page_runs
            )
            next_cursor = None
            if has_more and suggestions:
                last = suggestions[-1]
                next_cursor = encode_companion_review_cursor(
                    completed_at_ms=last.completed_at_ms,
                    analysis_run_id=last.analysis_run_id,
                )
            return CompanionReviewDetail(
                media_id=media_id_text,
                display_title=display_title if isinstance(display_title, str) else None,
                description=description if isinstance(description, str) else None,
                tags=tags,
                field_sources=field_sources,
                tag_sources=tag_sources,
                publication=publication,
                readiness=derive_review_readiness(
                    display_title=display_title if isinstance(display_title, str) else None,
                    description=description if isinstance(description, str) else None,
                    tag_count=len(tags),
                ),
                suggestions=suggestions,
                next_cursor=next_cursor,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestCompanionReviewRepositoryError:
            raise
        except CompanionReviewCodecError as exc:
            raise CompanionReviewStoredResultError(_STORED_RESULT_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestCompanionReviewRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def mark_opened(
        self,
        *,
        media_id: MediaId,
        actor_login_key: str,
        analysis_run_id: MediaAnalysisRunId,
        now_ms: int,
        require_owner: bool = False,
    ) -> CompanionReviewOpenedResult:
        def operation(connection: Connection) -> CompanionReviewOpenedResult:
            media_id_text = media_id.to_string()
            requested_run_id = analysis_run_id.to_string()
            if require_owner and not _actor_owns_cataloged_x(
                connection,
                media_id=media_id_text,
                actor_login_key=actor_login_key,
            ):
                raise CompanionReviewMediaNotFoundError(_MEDIA_NOT_FOUND_MESSAGE)
            _require_non_movie_media(connection, media_id_text)
            requested = _require_eligible_run(
                connection, media_id=media_id_text, analysis_run_id=requested_run_id
            )
            current = connection.execute(
                select(
                    companion_review_open_states.c.opened_run_id,
                    companion_review_open_states.c.opened_at_ms,
                ).where(
                    companion_review_open_states.c.actor_login_key == actor_login_key,
                    companion_review_open_states.c.media_id == media_id_text,
                )
            ).mappings().first()
            keep_current = False
            if current is not None:
                current_run = _load_run_order_key(
                    connection, str(current["opened_run_id"])
                )
                requested_key = (
                    int(requested["completed_at_ms"]),
                    str(requested["id"]),
                )
                if current_run is not None and current_run > requested_key:
                    keep_current = True
            if keep_current:
                opened_run_id = str(current["opened_run_id"])
                opened_at_ms = int(current["opened_at_ms"])
            else:
                opened_run_id = requested_run_id
                opened_at_ms = now_ms
                _upsert_opened_state(
                    connection,
                    actor_login_key=actor_login_key,
                    media_id=media_id_text,
                    opened_run_id=opened_run_id,
                    opened_at_ms=opened_at_ms,
                )
            latest = _latest_run_id_for_media(connection, media_id_text)
            unopened = latest is None or latest != opened_run_id
            return CompanionReviewOpenedResult(
                media_id=media_id_text,
                opened_run_id=opened_run_id,
                opened_at_ms=opened_at_ms,
                unopened=unopened,
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestCompanionReviewRepositoryError:
            raise
        except CompanionReviewCodecError as exc:
            raise CompanionReviewStoredResultError(_STORED_RESULT_MESSAGE) from exc
        except (IntegrityError, SQLAlchemyError) as exc:
            raise FrameNestCompanionReviewRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def apply_review(
        self,
        *,
        media_id: MediaId,
        actor_login_key: str,
        analysis_run_id: MediaAnalysisRunId,
        fields: tuple[str, ...],
        tag_keys: tuple[str, ...],
        now_ms: int,
    ) -> CompanionReviewApplyResult:
        def operation(connection: Connection) -> CompanionReviewApplyResult:
            media_id_text = media_id.to_string()
            run_id_text = analysis_run_id.to_string()
            _require_non_movie_media(connection, media_id_text)
            selected_run = _require_eligible_run(
                connection, media_id=media_id_text, analysis_run_id=run_id_text
            )
            stored = decode_stored_suggestion_result(str(selected_run["result_json"]))
            catalog = _load_canonical_catalog(connection)
            mapped = map_suggested_tags(stored.tags, catalog)
            eligible_keys = eligible_mapped_tag_keys(mapped)
            if _FIELD_TAGS in fields and not is_ordered_subsequence(
                tag_keys, eligible_keys
            ):
                raise CompanionReviewStaleMappingError(_STALE_MAPPING_MESSAGE)
            current = _load_apply_canonical(connection, media_id_text)
            new_title = (
                stored.title
                if _FIELD_DISPLAY_TITLE in fields
                else current["display_title"]
            )
            new_description = (
                stored.description
                if _FIELD_DESCRIPTION in fields
                else current["description"]
            )
            if _FIELD_TAGS in fields:
                current_keys = current["tag_keys"]
                new_tag_keys = _preserve_append_tag_keys(current_keys, tag_keys)
                if len(new_tag_keys) > MAX_MEDIA_TAGS:
                    raise CompanionReviewTagLimitConflictError(_TAG_LIMIT_MESSAGE)
                appended_keys = tuple(
                    key for key in tag_keys if key not in set(current_keys)
                )
            else:
                new_tag_keys = current["tag_keys"]
                appended_keys = ()
            new_collection_key = current["collection_key"]
            new_processed_at_ms = current["processed_at_ms"]
            if _FIELD_TAGS in fields:
                derived = derive_collection_state(
                    (
                        None
                        if current["collection_key"] is None
                        else MediaCollectionKey(current["collection_key"])
                    ),
                    current["processed_at_ms"],
                    tuple(CanonicalTagKey(key) for key in new_tag_keys),
                    now_ms,
                )
                new_collection_key = (
                    None
                    if derived.collection_key is None
                    else derived.collection_key.value
                )
                new_processed_at_ms = derived.processed_at_ms
            values_changed = (
                new_title != current["display_title"]
                or new_description != current["description"]
                or new_tag_keys != current["tag_keys"]
                or new_collection_key != current["collection_key"]
                or new_processed_at_ms != current["processed_at_ms"]
            )
            if not current["persisted"]:
                metadata_status = "created"
                connection.execute(
                    insert(media_metadata).values(
                        media_id=media_id_text,
                        display_title=new_title,
                        description=new_description,
                        content_category=DEFAULT_CONTENT_CATEGORY.value,
                        acquisition_source=DEFAULT_ACQUISITION_SOURCE.value,
                        collection_key=new_collection_key,
                        processed_at_ms=new_processed_at_ms,
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                    )
                )
                _replace_tag_assignments(connection, media_id_text, new_tag_keys)
            elif values_changed:
                metadata_status = "updated"
                connection.execute(
                    update(media_metadata)
                    .where(media_metadata.c.media_id == media_id_text)
                    .values(
                        display_title=new_title,
                        description=new_description,
                        collection_key=new_collection_key,
                        processed_at_ms=new_processed_at_ms,
                        updated_at_ms=now_ms,
                    )
                )
                if _FIELD_TAGS in fields:
                    _replace_tag_assignments(connection, media_id_text, new_tag_keys)
            else:
                metadata_status = "unchanged"
            selected_values = {
                _FIELD_DISPLAY_TITLE: new_title,
                _FIELD_DESCRIPTION: new_description,
                _FIELD_TAGS: new_tag_keys,
            }
            for field_name in fields:
                _upsert_field_source(
                    connection,
                    media_id=media_id_text,
                    field_name=field_name,
                    analysis_run_id=run_id_text,
                    actor_login_key=actor_login_key,
                    applied_at_ms=now_ms,
                    value=selected_values[field_name],
                )
            _insert_new_tag_sources(
                connection,
                media_id=media_id_text,
                tag_keys=appended_keys,
                analysis_run_id=run_id_text,
                actor_login_key=actor_login_key,
                applied_at_ms=now_ms,
            )
            tags = _load_canonical_tags(connection, media_id_text)
            readiness = derive_review_readiness(
                display_title=new_title,
                description=new_description,
                tag_count=len(tags),
            )
            publication = _load_publication(connection, media_id_text)
            if publication is not None:
                publication_status = "already_published"
            elif readiness.ready:
                publication_status = "requires_administrator_publish"
            else:
                publication_status = "not_ready"
            field_sources = _load_field_sources(
                connection,
                media_id=media_id_text,
                display_title=new_title if isinstance(new_title, str) else None,
                description=(
                    new_description if isinstance(new_description, str) else None
                ),
                tag_keys=tuple(tag.key for tag in tags),
            )
            tag_sources = _load_tag_sources(
                connection,
                media_id=media_id_text,
                tag_keys=tuple(tag.key for tag in tags),
            )
            from kronika.infrastructure.persistence.record_repository import (
                bump_bound_record_version,
            )

            bump_bound_record_version(connection, media_id_text)
            return CompanionReviewApplyResult(
                metadata_status=metadata_status,
                canonical=CompanionReviewApplyCanonical(
                    display_title=new_title if isinstance(new_title, str) else None,
                    description=(
                        new_description if isinstance(new_description, str) else None
                    ),
                    tags=tags,
                    field_sources=field_sources,
                    tag_sources=tag_sources,
                ),
                publication=CompanionReviewApplyPublication(
                    status=publication_status,
                    state="published" if publication is not None else "unpublished",
                    origin=(
                        None
                        if publication is None
                        else publication.publication_origin.value
                    ),
                    published_at_ms=(
                        None if publication is None else publication.published_at_ms
                    ),
                    ready=readiness.ready,
                    missing_fields=readiness.missing_fields,
                ),
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestCompanionReviewRepositoryError:
            raise
        except CompanionReviewCodecError as exc:
            raise CompanionReviewStoredResultError(_STORED_RESULT_MESSAGE) from exc
        except (IntegrityError, SQLAlchemyError) as exc:
            raise FrameNestCompanionReviewRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _latest_successful_generic():
    ranked = (
        select(
            media_analysis_runs.c.id,
            media_analysis_runs.c.media_id,
            media_analysis_runs.c.completed_at_ms,
            media_analysis_runs.c.result_json,
            media_metadata.c.display_title,
            func.row_number()
            .over(
                partition_by=media_analysis_runs.c.media_id,
                order_by=(
                    media_analysis_runs.c.completed_at_ms.desc(),
                    media_analysis_runs.c.id.desc(),
                ),
            )
            .label("rn"),
        )
        .select_from(
            media_analysis_runs.outerjoin(
                media_metadata,
                media_metadata.c.media_id == media_analysis_runs.c.media_id,
            )
        )
        .where(*_analyzed_inbox_predicates())
    ).subquery("ranked_generic")
    return select(ranked).where(ranked.c.rn == 1)


def _analyzed_inbox_rows(latest) -> Select:
    return _analyzed_row_select(latest).select_from(
        latest.join(logical_media, logical_media.c.id == latest.c.media_id)
    )


def _own_history_rows(latest, actor_login_key: str) -> Select:
    owned = _owned_cataloged_x_media_ids(actor_login_key).subquery(
        "owned_x_for_analyzed"
    )
    analyzed_rows = _analyzed_row_select(latest).select_from(
        latest.join(logical_media, logical_media.c.id == latest.c.media_id).join(
            owned, owned.c.media_id == latest.c.media_id
        )
    )
    pending_ranked = (
        select(
            x_assets.c.media_id,
            logical_media.c.created_at_ms,
            media_metadata.c.display_title,
            x_post_claims.c.title.label("claim_title"),
            x_post_claims.c.x_post_id,
            func.row_number()
            .over(
                partition_by=x_assets.c.media_id,
                order_by=(
                    x_assets.c.completed_at_ms.desc(),
                    x_assets.c.id.desc(),
                ),
            )
            .label("rn"),
        )
        .select_from(
            x_assets.join(
                x_post_claims, x_post_claims.c.id == x_assets.c.claim_id
            )
            .join(logical_media, logical_media.c.id == x_assets.c.media_id)
            .outerjoin(
                media_metadata, media_metadata.c.media_id == x_assets.c.media_id
            )
            .outerjoin(latest, latest.c.media_id == x_assets.c.media_id)
        )
        .where(
            *_owned_cataloged_x_predicates(actor_login_key),
            latest.c.media_id.is_(None),
        )
    ).subquery("ranked_pending_x")
    pending = select(pending_ranked).where(pending_ranked.c.rn == 1).subquery(
        "pending_x"
    )
    pending_rows = select(
        pending.c.media_id.label("media_id"),
        pending.c.created_at_ms.label("created_at_ms"),
        literal(False).label("analyzed"),
        cast(literal(None), Text).label("analysis_run_id"),
        literal(None).label("completed_at_ms"),
        cast(literal(None), Text).label("result_json"),
        pending.c.display_title.label("display_title"),
        pending.c.claim_title.label("claim_title"),
        pending.c.x_post_id.label("x_post_id"),
        pending.c.created_at_ms.label("activity_at_ms"),
        pending.c.media_id.label("sort_id"),
    )
    return analyzed_rows.union_all(pending_rows)


def _analyzed_row_select(latest) -> Select:
    return select(
        latest.c.media_id.label("media_id"),
        logical_media.c.created_at_ms.label("created_at_ms"),
        literal(True).label("analyzed"),
        latest.c.id.label("analysis_run_id"),
        latest.c.completed_at_ms.label("completed_at_ms"),
        latest.c.result_json.label("result_json"),
        latest.c.display_title.label("display_title"),
        cast(literal(None), Text).label("claim_title"),
        cast(literal(None), Text).label("x_post_id"),
        latest.c.completed_at_ms.label("activity_at_ms"),
        latest.c.id.label("sort_id"),
    )


def _owned_cataloged_x_predicates(actor_login_key: str) -> tuple[object, ...]:
    return (
        x_assets.c.state == "cataloged",
        x_assets.c.media_id.is_not(None),
        x_post_claims.c.created_by_login_key == actor_login_key,
        or_(
            x_post_claims.c.requested_content_category.is_(None),
            x_post_claims.c.requested_content_category
            != ContentCategory.MOVIE.value,
        ),
        func.coalesce(
            media_metadata.c.content_category, DEFAULT_CONTENT_CATEGORY.value
        )
        != ContentCategory.MOVIE.value,
    )


def _owned_cataloged_x_media_ids(actor_login_key: str) -> Select:
    return (
        select(x_assets.c.media_id)
        .select_from(
            x_assets.join(
                x_post_claims, x_post_claims.c.id == x_assets.c.claim_id
            ).outerjoin(
                media_metadata, media_metadata.c.media_id == x_assets.c.media_id
            )
        )
        .where(*_owned_cataloged_x_predicates(actor_login_key))
        .distinct()
    )


def _own_analyzed_latest(latest, actor_login_key: str) -> Select:
    owned = _owned_cataloged_x_media_ids(actor_login_key).subquery(
        "owned_cataloged_x"
    )
    return select(latest).select_from(
        latest.join(owned, owned.c.media_id == latest.c.media_id)
    )


def _analyzed_inbox_predicates() -> tuple[object, ...]:
    return (
        media_analysis_runs.c.state == "analyzed",
        media_analysis_runs.c.analysis_definition
        == AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        media_analysis_runs.c.analysis_definition
        != MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION,
        or_(
            media_analysis_runs.c.analysis_profile
            == AnalysisProfile.GENERIC_MEDIA.value,
            media_analysis_runs.c.analysis_profile.is_(None),
        ),
        media_analysis_runs.c.completed_at_ms.is_not(None),
        func.coalesce(
            media_metadata.c.content_category, DEFAULT_CONTENT_CATEGORY.value
        )
        != ContentCategory.MOVIE.value,
    )


def _successful_generic_predicates() -> tuple[object, ...]:
    return (
        *_analyzed_inbox_predicates(),
        media_analysis_runs.c.result_schema_version == RESULT_SCHEMA_VERSION,
    )


def _unopened_count_statement(latest, actor_login_key: str) -> Select:
    opened = companion_review_open_states
    joined = latest.outerjoin(
        opened,
        and_(
            opened.c.actor_login_key == actor_login_key,
            opened.c.media_id == latest.c.media_id,
        ),
    )
    return (
        select(func.count())
        .select_from(joined)
        .where(
            or_(
                opened.c.opened_run_id.is_(None),
                opened.c.opened_run_id != latest.c.id,
            )
        )
    )


def _apply_inbox_keyset(
    statement: Select,
    mixed,
    cursor: tuple[int, bool, str] | None,
) -> Select:
    if cursor is None:
        return statement
    activity_at_ms, analyzed, sort_id = cursor
    analyzed_rank = cast(mixed.c.analyzed, Integer)
    cursor_rank = int(analyzed)
    return statement.where(
        or_(
            mixed.c.activity_at_ms < activity_at_ms,
            and_(
                mixed.c.activity_at_ms == activity_at_ms,
                analyzed_rank < cursor_rank,
            ),
            and_(
                mixed.c.activity_at_ms == activity_at_ms,
                analyzed_rank == cursor_rank,
                mixed.c.sort_id < sort_id,
            ),
        )
    )


def _inbox_item_from_row(row: object) -> CompanionReviewInboxItem:
    mapping = dict(row)  # type: ignore[arg-type]
    analyzed = bool(mapping["analyzed"])
    canonical_title = mapping.get("display_title")
    canonical_display_title = (
        str(canonical_title) if isinstance(canonical_title, str) else None
    )
    analysis_run_id: str | None = None
    completed_at_ms: int | None = None
    unopened = False
    if analyzed:
        stored = None
        raw_result = mapping.get("result_json")
        if isinstance(raw_result, str):
            try:
                stored = decode_stored_suggestion_result(raw_result)
            except CompanionReviewCodecError:
                stored = None
        title = inbox_title(
            canonical_display_title=canonical_display_title,
            stored=stored,
        )
        analysis_run_id = str(mapping["analysis_run_id"])
        completed_at_ms = int(mapping["completed_at_ms"])
        opened_run_id = mapping.get("opened_run_id")
        unopened = opened_run_id is None or str(opened_run_id) != analysis_run_id
    else:
        title = pending_inbox_title(
            canonical_display_title=canonical_display_title,
            claim_title=(
                str(mapping["claim_title"])
                if isinstance(mapping.get("claim_title"), str)
                else None
            ),
            x_post_id=str(mapping["x_post_id"]),
        )
    return CompanionReviewInboxItem(
        media_id=str(mapping["media_id"]),
        title=title,
        created_at_ms=int(mapping["created_at_ms"]),
        analyzed=analyzed,
        analysis_run_id=analysis_run_id,
        completed_at_ms=completed_at_ms,
        unopened=unopened,
    )


def _load_publication(
    connection: Connection, media_id: str
) -> ContentPublication | None:
    row = connection.execute(
        select(media_content_publications).where(
            media_content_publications.c.media_id == media_id
        )
    ).mappings().first()
    if row is None:
        return None
    return ContentPublication(
        media_id=str(row["media_id"]),
        published_at_ms=int(row["published_at_ms"]),
        publication_origin=ContentPublicationOrigin(str(row["publication_origin"])),
    )


def _load_canonical_catalog(connection: Connection) -> tuple[CanonicalTagView, ...]:
    rows = connection.execute(
        select(canonical_tags.c.key, canonical_tags.c.display_name).order_by(
            canonical_tags.c.display_name, canonical_tags.c.key
        )
    ).mappings().all()
    return tuple(
        CanonicalTagView(key=str(row["key"]), display_name=str(row["display_name"]))
        for row in rows
    )


def _load_field_sources(
    connection: Connection,
    *,
    media_id: str,
    display_title: str | None,
    description: str | None,
    tag_keys: tuple[str, ...],
) -> dict[str, CompanionReviewFieldSource | None]:
    receipts: dict[str, CompanionReviewFieldSource | None] = {
        field: None for field in _REVIEW_FIELDS
    }
    current_digests = {
        "display_title": (
            None
            if display_title is None
            else canonical_field_digest("display_title", display_title)
        ),
        "description": (
            None
            if description is None
            else canonical_field_digest("description", description)
        ),
        "tags": canonical_field_digest("tags", tag_keys),
    }
    rows = connection.execute(
        select(
            companion_review_field_sources.c.field_name,
            companion_review_field_sources.c.analysis_run_id,
            companion_review_field_sources.c.applied_at_ms,
            companion_review_field_sources.c.value_digest,
            media_analysis_runs.c.completed_at_ms,
            media_analysis_runs.c.provider_id,
            media_analysis_runs.c.model_id,
        )
        .select_from(
            companion_review_field_sources.join(
                media_analysis_runs,
                media_analysis_runs.c.id
                == companion_review_field_sources.c.analysis_run_id,
            )
        )
        .where(companion_review_field_sources.c.media_id == media_id)
    ).mappings().all()
    for row in rows:
        field_name = str(row["field_name"])
        expected = current_digests.get(field_name)
        if expected is None or str(row["value_digest"]) != expected:
            continue
        receipts[field_name] = CompanionReviewFieldSource(
            analysis_run_id=str(row["analysis_run_id"]),
            completed_at_ms=int(row["completed_at_ms"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            applied_at_ms=int(row["applied_at_ms"]),
        )
    return receipts


def _load_history_page(
    connection: Connection,
    *,
    media_id: str,
    limit: int,
    cursor: tuple[int, str] | None,
) -> list[object]:
    statement = (
        select(
            media_analysis_runs.c.id,
            media_analysis_runs.c.completed_at_ms,
            media_analysis_runs.c.provider_id,
            media_analysis_runs.c.model_id,
            media_analysis_runs.c.prompt_version,
            media_analysis_runs.c.result_json,
        )
        .select_from(
            media_analysis_runs.outerjoin(
                media_metadata,
                media_metadata.c.media_id == media_analysis_runs.c.media_id,
            )
        )
        .where(
            media_analysis_runs.c.media_id == media_id,
            *_successful_generic_predicates(),
        )
    )
    if cursor is not None:
        completed_at_ms, analysis_run_id = cursor
        statement = statement.where(
            or_(
                media_analysis_runs.c.completed_at_ms < completed_at_ms,
                and_(
                    media_analysis_runs.c.completed_at_ms == completed_at_ms,
                    media_analysis_runs.c.id < analysis_run_id,
                ),
            )
        )
    statement = statement.order_by(
        media_analysis_runs.c.completed_at_ms.desc(),
        media_analysis_runs.c.id.desc(),
    ).limit(limit + 1)
    return list(connection.execute(statement).mappings().all())


def _suggestion_from_run(
    row: object, catalog: tuple[CanonicalTagView, ...]
) -> CompanionReviewSuggestion:
    mapping = dict(row)  # type: ignore[arg-type]
    try:
        stored = decode_stored_suggestion_result(str(mapping["result_json"]))
    except CompanionReviewCodecError as exc:
        raise CompanionReviewStoredResultError(_STORED_RESULT_MESSAGE) from exc
    return CompanionReviewSuggestion(
        analysis_run_id=str(mapping["id"]),
        completed_at_ms=int(mapping["completed_at_ms"]),
        provider_id=str(mapping["provider_id"]),
        model_id=str(mapping["model_id"]),
        prompt_version=str(mapping["prompt_version"]),
        title=stored.title,
        description=stored.description,
        tags=map_suggested_tags(stored.tags, catalog),
        suggested_filename=stored.suggested_filename,
    )


def _actor_owns_cataloged_x(
    connection: Connection, *, media_id: str, actor_login_key: str
) -> bool:
    count = connection.execute(
        select(func.count())
        .select_from(
            x_assets.join(
                x_post_claims, x_post_claims.c.id == x_assets.c.claim_id
            )
        )
        .where(
            x_assets.c.media_id == media_id,
            x_assets.c.state == "cataloged",
            x_post_claims.c.created_by_login_key == actor_login_key,
        )
    ).scalar_one()
    return int(count) > 0


def _require_non_movie_media(connection: Connection, media_id: str) -> None:
    exists = connection.execute(
        select(logical_media.c.id).where(logical_media.c.id == media_id)
    ).first()
    if exists is None:
        raise CompanionReviewMediaNotFoundError(_MEDIA_NOT_FOUND_MESSAGE)
    metadata_row = connection.execute(
        select(media_metadata.c.content_category).where(
            media_metadata.c.media_id == media_id
        )
    ).first()
    content_category = DEFAULT_CONTENT_CATEGORY.value
    if metadata_row is not None and metadata_row[0] is not None:
        content_category = str(metadata_row[0])
    if content_category == ContentCategory.MOVIE.value:
        raise CompanionReviewMovieExcludedError(_MOVIE_EXCLUDED_MESSAGE)


def _require_eligible_run(
    connection: Connection, *, media_id: str, analysis_run_id: str
):
    existing = connection.execute(
        select(media_analysis_runs.c.id).where(
            media_analysis_runs.c.id == analysis_run_id
        )
    ).first()
    if existing is None:
        raise CompanionReviewAnalysisRunNotFoundError(_RUN_NOT_FOUND_MESSAGE)
    row = connection.execute(
        select(
            media_analysis_runs.c.id,
            media_analysis_runs.c.media_id,
            media_analysis_runs.c.completed_at_ms,
            media_analysis_runs.c.result_json,
            media_analysis_runs.c.provider_id,
            media_analysis_runs.c.model_id,
        )
        .select_from(
            media_analysis_runs.outerjoin(
                media_metadata,
                media_metadata.c.media_id == media_analysis_runs.c.media_id,
            )
        )
        .where(
            media_analysis_runs.c.id == analysis_run_id,
            media_analysis_runs.c.media_id == media_id,
            *_successful_generic_predicates(),
        )
    ).mappings().first()
    if row is None:
        raise CompanionReviewRunNotEligibleError(_RUN_NOT_ELIGIBLE_MESSAGE)
    return row


def _load_run_order_key(
    connection: Connection, analysis_run_id: str
) -> tuple[int, str] | None:
    row = connection.execute(
        select(
            media_analysis_runs.c.completed_at_ms,
            media_analysis_runs.c.id,
        ).where(media_analysis_runs.c.id == analysis_run_id)
    ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0]), str(row[1])


def _latest_run_id_for_media(connection: Connection, media_id: str) -> str | None:
    latest = _latest_successful_generic().subquery("latest_generic")
    row = connection.execute(
        select(latest.c.id).where(latest.c.media_id == media_id)
    ).first()
    if row is None:
        return None
    return str(row[0])


def _upsert_opened_state(
    connection: Connection,
    *,
    actor_login_key: str,
    media_id: str,
    opened_run_id: str,
    opened_at_ms: int,
) -> None:
    statement = sqlite_insert(companion_review_open_states).values(
        actor_login_key=actor_login_key,
        media_id=media_id,
        opened_run_id=opened_run_id,
        opened_at_ms=opened_at_ms,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[
            companion_review_open_states.c.actor_login_key,
            companion_review_open_states.c.media_id,
        ],
        set_={
            "opened_run_id": statement.excluded.opened_run_id,
            "opened_at_ms": statement.excluded.opened_at_ms,
        },
    )
    connection.execute(statement)


def _load_apply_canonical(connection: Connection, media_id: str) -> dict[str, object]:
    metadata_row = connection.execute(
        select(
            media_metadata.c.display_title,
            media_metadata.c.description,
            media_metadata.c.collection_key,
            media_metadata.c.processed_at_ms,
        ).where(media_metadata.c.media_id == media_id)
    ).mappings().first()
    tag_rows = connection.execute(
        select(media_canonical_tags.c.tag_key)
        .where(media_canonical_tags.c.media_id == media_id)
        .order_by(media_canonical_tags.c.position, media_canonical_tags.c.tag_key)
    ).mappings().all()
    tag_keys = tuple(str(row["tag_key"]) for row in tag_rows)
    if metadata_row is None:
        return {
            "persisted": False,
            "display_title": None,
            "description": None,
            "tag_keys": tag_keys,
            "collection_key": None,
            "processed_at_ms": None,
        }
    display_title = metadata_row["display_title"]
    description = metadata_row["description"]
    if isinstance(display_title, str) and not display_title.strip():
        display_title = None
    if isinstance(description, str) and not description.strip():
        description = None
    return {
        "persisted": True,
        "display_title": display_title if isinstance(display_title, str) else None,
        "description": description if isinstance(description, str) else None,
        "tag_keys": tag_keys,
        "collection_key": metadata_row["collection_key"],
        "processed_at_ms": metadata_row["processed_at_ms"],
    }


def _preserve_append_tag_keys(
    current: tuple[str, ...], submitted: tuple[str, ...]
) -> tuple[str, ...]:
    present = set(current)
    appended = tuple(key for key in submitted if key not in present)
    return current + appended


def _replace_tag_assignments(
    connection: Connection, media_id: str, tag_keys: tuple[str, ...]
) -> None:
    connection.execute(
        delete(media_canonical_tags).where(media_canonical_tags.c.media_id == media_id)
    )
    for position, key in enumerate(tag_keys):
        connection.execute(
            insert(media_canonical_tags).values(
                media_id=media_id,
                tag_key=key,
                position=position,
            )
        )


def _upsert_field_source(
    connection: Connection,
    *,
    media_id: str,
    field_name: str,
    analysis_run_id: str,
    actor_login_key: str,
    applied_at_ms: int,
    value: object,
) -> None:
    digest = canonical_field_digest(field_name, value)
    statement = sqlite_insert(companion_review_field_sources).values(
        media_id=media_id,
        field_name=field_name,
        analysis_run_id=analysis_run_id,
        applied_by_login_key=actor_login_key,
        applied_at_ms=applied_at_ms,
        value_digest=digest,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[
            companion_review_field_sources.c.media_id,
            companion_review_field_sources.c.field_name,
        ],
        set_={
            "analysis_run_id": statement.excluded.analysis_run_id,
            "applied_by_login_key": statement.excluded.applied_by_login_key,
            "applied_at_ms": statement.excluded.applied_at_ms,
            "value_digest": statement.excluded.value_digest,
        },
    )
    connection.execute(statement)


def _insert_new_tag_sources(
    connection: Connection,
    *,
    media_id: str,
    tag_keys: tuple[str, ...],
    analysis_run_id: str,
    actor_login_key: str,
    applied_at_ms: int,
) -> None:
    for key in tag_keys:
        statement = sqlite_insert(companion_review_tag_sources).values(
            media_id=media_id,
            tag_key=key,
            analysis_run_id=analysis_run_id,
            applied_by_login_key=actor_login_key,
            applied_at_ms=applied_at_ms,
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=[
                companion_review_tag_sources.c.media_id,
                companion_review_tag_sources.c.tag_key,
            ]
        )
        connection.execute(statement)


def _load_tag_sources(
    connection: Connection,
    *,
    media_id: str,
    tag_keys: tuple[str, ...],
) -> dict[str, CompanionReviewFieldSource]:
    if not tag_keys:
        return {}
    rows = connection.execute(
        select(
            companion_review_tag_sources.c.tag_key,
            companion_review_tag_sources.c.analysis_run_id,
            companion_review_tag_sources.c.applied_at_ms,
            media_analysis_runs.c.completed_at_ms,
            media_analysis_runs.c.provider_id,
            media_analysis_runs.c.model_id,
        )
        .select_from(
            companion_review_tag_sources.join(
                media_analysis_runs,
                media_analysis_runs.c.id
                == companion_review_tag_sources.c.analysis_run_id,
            )
        )
        .where(
            companion_review_tag_sources.c.media_id == media_id,
            companion_review_tag_sources.c.tag_key.in_(tag_keys),
        )
    ).mappings().all()
    sources: dict[str, CompanionReviewFieldSource] = {}
    for row in rows:
        sources[str(row["tag_key"])] = CompanionReviewFieldSource(
            analysis_run_id=str(row["analysis_run_id"]),
            completed_at_ms=int(row["completed_at_ms"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            applied_at_ms=int(row["applied_at_ms"]),
        )
    return sources


def _load_canonical_tags(
    connection: Connection, media_id: str
) -> tuple[CompanionReviewCanonicalTag, ...]:
    tag_rows = connection.execute(
        select(
            canonical_tags.c.key,
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
        .order_by(media_canonical_tags.c.position, canonical_tags.c.key)
    ).mappings().all()
    return tuple(
        CompanionReviewCanonicalTag(
            key=str(row["key"]),
            display_name=str(row["display_name"]),
            position=int(row["position"]),
        )
        for row in tag_rows
    )
