"""SQLite adapter for durable content publication and admin workflow reads."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.content_publication_repository import (
    ContentPublicationBoundRecordError,
    AdminMediaItem,
    AdminMediaPage,
    AdminMediaQuery,
    ContentPublicationMediaNotFoundError,
    FrameNestContentPublicationRepositoryError,
    MediaWorkflowStatus,
    PublishContentResult,
)
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaLocation,
    CatalogMediaTag,
)
from kronika.domain.content_publication import (
    ContentPublication,
    ContentPublicationOrigin,
    derive_content_publication_readiness,
)
from kronika.domain.identities import MediaId
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
)
from kronika.infrastructure.persistence.catalog_schema import (
    canonical_tags,
    logical_media,
    media_analysis_runs,
    media_canonical_tags,
    media_content_publications,
    media_metadata,
    physical_media_locations,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)
from kronika.infrastructure.persistence.media_attribution_repository import (
    contributor_match_clause,
    load_media_contributions,
)

_REPOSITORY_FAILURE_MESSAGE = "Content publication operation failed."
_LIKE_ESCAPE = "\\"


class SqliteContentPublicationRepository:
    """Short-transaction content-publication persistence adapter."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def media_exists(self, media_id: MediaId) -> bool:
        def operation(connection: Connection) -> bool:
            return (
                connection.execute(
                    select(logical_media.c.id).where(
                        logical_media.c.id == media_id.to_string()
                    )
                ).first()
                is not None
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def bound_record_owner(self, media_id: MediaId) -> str | None:
        def operation(connection: Connection) -> str | None:
            from kronika.infrastructure.persistence.catalog_schema import (
                kronika_records,
            )

            row = connection.execute(
                select(kronika_records.c.owner_login_key).where(
                    kronika_records.c.media_id == media_id.to_string()
                )
            ).scalar()
            return None if row is None else str(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def is_published(self, media_id: MediaId) -> bool:
        def operation(connection: Connection) -> bool:
            from kronika.infrastructure.persistence.catalog_schema import (
                kronika_records,
            )

            bound = connection.execute(
                select(kronika_records.c.id).where(
                    kronika_records.c.media_id == media_id.to_string()
                )
            ).first()
            if bound is not None:
                return False
            return (
                connection.execute(
                    select(media_content_publications.c.media_id).where(
                        media_content_publications.c.media_id
                        == media_id.to_string()
                    )
                ).first()
                is not None
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_media_workflow_status(self, media_id: MediaId) -> MediaWorkflowStatus:
        def operation(connection: Connection) -> MediaWorkflowStatus:
            media_id_text = media_id.to_string()
            if (
                connection.execute(
                    select(logical_media.c.id).where(
                        logical_media.c.id == media_id_text
                    )
                ).first()
                is None
            ):
                raise ContentPublicationMediaNotFoundError(
                    _REPOSITORY_FAILURE_MESSAGE
                )
            readiness = _load_readiness(connection, media_id_text)
            publication = _get_publication(connection, media_id_text)
            return MediaWorkflowStatus(
                metadata_state="complete" if readiness.ready else "incomplete",
                missing_metadata_fields=readiness.missing_fields,
                publication_state=(
                    "published" if publication is not None else "unpublished"
                ),
            )

        try:
            return run_in_transaction(self._engine, operation)
        except ContentPublicationMediaNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_admin_media(self, query: AdminMediaQuery) -> AdminMediaPage:
        def operation(connection: Connection) -> AdminMediaPage:
            filtered = _admin_filtered_select(query).subquery()
            total = connection.execute(
                select(func.count()).select_from(filtered)
            ).scalar_one()
            page_rows = connection.execute(
                select(filtered)
                .order_by(
                    filtered.c.created_at_ms.desc(),
                    filtered.c.media_id.asc(),
                )
                .limit(query.limit)
                .offset(query.offset)
            ).mappings().all()
            media_ids = tuple(str(row["media_id"]) for row in page_rows)
            tags_by_media = _load_tags(connection, media_ids)
            locations_by_media = _load_locations(connection, media_ids)
            analysis_by_media = _load_latest_analysis_states(connection, media_ids)
            contributions_by_media = load_media_contributions(connection, media_ids)
            items: list[AdminMediaItem] = []
            for row in page_rows:
                media_id = str(row["media_id"])
                tags = tuple(tags_by_media[media_id])
                publication = _publication_from_row(row)
                items.append(
                    AdminMediaItem(
                        media_id=media_id,
                        media_kind=str(row["media_kind"]),
                        created_at_ms=int(row["created_at_ms"]),
                        updated_at_ms=int(row["updated_at_ms"]),
                        display_title=(
                            None
                            if row["display_title"] is None
                            else str(row["display_title"])
                        ),
                        description=(
                            None
                            if row["description"] is None
                            else str(row["description"])
                        ),
                        collection_key=(
                            None
                            if row["collection_key"] is None
                            else str(row["collection_key"])
                        ),
                        processed_at_ms=(
                            None
                            if row["processed_at_ms"] is None
                            else int(row["processed_at_ms"])
                        ),
                        content_category=(
                            "general"
                            if row["content_category"] is None
                            else str(row["content_category"])
                        ),
                        acquisition_source=(
                            "unknown"
                            if row["acquisition_source"] is None
                            else str(row["acquisition_source"])
                        ),
                        tags=tags,
                        locations=tuple(locations_by_media[media_id]),
                        publication=publication,
                        readiness=derive_content_publication_readiness(
                            display_title=row["display_title"],
                            description=row["description"],
                            canonical_tag_count=len(tags),
                        ),
                        analysis_state=analysis_by_media.get(
                            media_id,
                            "not_requested",
                        ),
                        contributors=contributions_by_media.get(media_id, ()),
                    )
                )
            return AdminMediaPage(
                items=tuple(items),
                total=int(total),
                limit=query.limit,
                offset=query.offset,
                q=query.q,
                tag_keys=query.tag_keys,
                publication=query.publication,
                readiness=query.readiness,
                analysis=query.analysis,
                contributor=query.contributor,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestContentPublicationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def publish(self, media_id: MediaId, published_at_ms: int) -> PublishContentResult:
        def operation(connection: Connection) -> PublishContentResult:
            media_id_text = media_id.to_string()
            _reject_bound_record(connection, media_id_text)
            media_exists = connection.execute(
                select(logical_media.c.id).where(logical_media.c.id == media_id_text)
            ).first()
            if media_exists is None:
                raise ContentPublicationMediaNotFoundError(
                    _REPOSITORY_FAILURE_MESSAGE
                )
            existing = _get_publication(connection, media_id_text)
            readiness = _load_readiness(connection, media_id_text)
            if existing is not None:
                return PublishContentResult(
                    status="already_published",
                    publication=existing,
                    readiness=readiness,
                )
            if not readiness.ready:
                return PublishContentResult(
                    status="not_ready",
                    publication=None,
                    readiness=readiness,
                )
            connection.execute(
                insert(media_content_publications).values(
                    media_id=media_id_text,
                    published_at_ms=published_at_ms,
                    publication_origin=ContentPublicationOrigin.ADMIN_EXPLICIT.value,
                )
            )
            publication = _get_publication(connection, media_id_text)
            if publication is None:
                raise FrameNestContentPublicationRepositoryError(
                    _REPOSITORY_FAILURE_MESSAGE
                )
            return PublishContentResult(
                status="published",
                publication=publication,
                readiness=readiness,
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except ContentPublicationMediaNotFoundError:
            raise
        except FrameNestContentPublicationRepositoryError:
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def unpublish(self, media_id: MediaId) -> PublishContentResult:
        def operation(connection: Connection) -> PublishContentResult:
            media_id_text = media_id.to_string()
            _reject_bound_record(connection, media_id_text)
            media_exists = connection.execute(
                select(logical_media.c.id).where(logical_media.c.id == media_id_text)
            ).first()
            if media_exists is None:
                raise ContentPublicationMediaNotFoundError(
                    _REPOSITORY_FAILURE_MESSAGE
                )
            existing = _get_publication(connection, media_id_text)
            readiness = _load_readiness(connection, media_id_text)
            if existing is None:
                return PublishContentResult(
                    status="already_unpublished",
                    publication=None,
                    readiness=readiness,
                )
            connection.execute(
                delete(media_content_publications).where(
                    media_content_publications.c.media_id == media_id_text
                )
            )
            return PublishContentResult(
                status="unpublished",
                publication=None,
                readiness=readiness,
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except ContentPublicationMediaNotFoundError:
            raise
        except FrameNestContentPublicationRepositoryError:
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise FrameNestContentPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _admin_filtered_select(query: AdminMediaQuery):
    tag_exists = exists(
        select(media_canonical_tags.c.media_id).where(
            media_canonical_tags.c.media_id == logical_media.c.id
        )
    )
    title_ready = func.trim(func.coalesce(media_metadata.c.display_title, "")) != ""
    description_ready = (
        func.trim(func.coalesce(media_metadata.c.description, "")) != ""
    )
    ready = title_ready & description_ready & tag_exists
    latest_analysis_state = (
        select(media_analysis_runs.c.state)
        .where(
            media_analysis_runs.c.media_id == logical_media.c.id,
            media_analysis_runs.c.analysis_definition
            == AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        )
        .order_by(
            media_analysis_runs.c.created_at_ms.desc(),
            media_analysis_runs.c.id.desc(),
        )
        .limit(1)
        .correlate(logical_media)
        .scalar_subquery()
    )
    joined = (
        logical_media.outerjoin(
            media_metadata,
            media_metadata.c.media_id == logical_media.c.id,
        )
        .outerjoin(
            media_content_publications,
            media_content_publications.c.media_id == logical_media.c.id,
        )
    )
    if query.tag_keys:
        joined = joined.join(
            media_canonical_tags,
            media_canonical_tags.c.media_id == logical_media.c.id,
        )
    statement = select(
        logical_media.c.id.label("media_id"),
        logical_media.c.media_kind,
        logical_media.c.created_at_ms,
        logical_media.c.updated_at_ms,
        media_metadata.c.display_title,
        media_metadata.c.description,
        media_metadata.c.collection_key,
        media_metadata.c.processed_at_ms,
        media_metadata.c.content_category,
        media_metadata.c.acquisition_source,
        media_content_publications.c.published_at_ms,
        media_content_publications.c.publication_origin,
    ).select_from(joined)
    if query.q is not None:
        statement = statement.where(
            media_metadata.c.display_title.collate("NOCASE").like(
                _like_pattern(query.q),
                escape=_LIKE_ESCAPE,
            )
        )
    if query.publication == "unpublished":
        statement = statement.where(
            media_content_publications.c.media_id.is_(None)
        )
    elif query.publication == "published":
        statement = statement.where(
            media_content_publications.c.media_id.is_not(None)
        )
    if query.readiness == "ready":
        statement = statement.where(ready)
    elif query.readiness == "incomplete":
        statement = statement.where(~ready)
    if query.analysis == "not_requested":
        statement = statement.where(latest_analysis_state.is_(None))
    elif query.analysis != "all":
        statement = statement.where(latest_analysis_state == query.analysis)
    if query.contributor is not None:
        statement = statement.where(contributor_match_clause(query.contributor))
    if query.tag_keys:
        statement = (
            statement.where(media_canonical_tags.c.tag_key.in_(query.tag_keys))
            .group_by(
                logical_media.c.id,
                logical_media.c.media_kind,
                logical_media.c.created_at_ms,
                logical_media.c.updated_at_ms,
                media_metadata.c.display_title,
                media_metadata.c.description,
                media_metadata.c.collection_key,
                media_metadata.c.processed_at_ms,
                media_metadata.c.content_category,
                media_metadata.c.acquisition_source,
                media_content_publications.c.published_at_ms,
                media_content_publications.c.publication_origin,
            )
            .having(
                func.count(func.distinct(media_canonical_tags.c.tag_key))
                == len(query.tag_keys)
            )
        )
    return statement


def _load_tags(
    connection: Connection,
    media_ids: tuple[str, ...],
) -> dict[str, list[CatalogMediaTag]]:
    result: dict[str, list[CatalogMediaTag]] = defaultdict(list)
    if not media_ids:
        return result
    rows = connection.execute(
        select(
            media_canonical_tags.c.media_id,
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
        .where(media_canonical_tags.c.media_id.in_(media_ids))
        .order_by(
            media_canonical_tags.c.media_id.asc(),
            media_canonical_tags.c.position.asc(),
            media_canonical_tags.c.tag_key.asc(),
        )
    ).mappings()
    for row in rows:
        result[str(row["media_id"])].append(
            CatalogMediaTag(
                key=str(row["tag_key"]),
                display_name=str(row["display_name"]),
                position=int(row["position"]),
            )
        )
    return result


def _load_locations(
    connection: Connection,
    media_ids: tuple[str, ...],
) -> dict[str, list[CatalogMediaLocation]]:
    result: dict[str, list[CatalogMediaLocation]] = defaultdict(list)
    if not media_ids:
        return result
    rows = connection.execute(
        select(
            physical_media_locations.c.media_id,
            physical_media_locations.c.id,
            physical_media_locations.c.library_id,
            physical_media_locations.c.relative_path,
            physical_media_locations.c.availability,
            physical_media_locations.c.observed_size_bytes,
            physical_media_locations.c.observed_mtime_ns,
        )
        .where(physical_media_locations.c.media_id.in_(media_ids))
        .order_by(
            physical_media_locations.c.media_id.asc(),
            physical_media_locations.c.library_id.asc(),
            physical_media_locations.c.relative_path.asc(),
            physical_media_locations.c.id.asc(),
        )
    ).mappings()
    for row in rows:
        result[str(row["media_id"])].append(
            CatalogMediaLocation(
                location_id=str(row["id"]),
                library_id=str(row["library_id"]),
                relative_path=str(row["relative_path"]),
                availability=str(row["availability"]),
                observed_size_bytes=(
                    None
                    if row["observed_size_bytes"] is None
                    else int(row["observed_size_bytes"])
                ),
                observed_mtime_ns=(
                    None
                    if row["observed_mtime_ns"] is None
                    else int(row["observed_mtime_ns"])
                ),
            )
        )
    return result


def _load_latest_analysis_states(
    connection: Connection,
    media_ids: tuple[str, ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    if not media_ids:
        return result
    ranked = (
        select(
            media_analysis_runs.c.media_id,
            media_analysis_runs.c.state,
            func.row_number()
            .over(
                partition_by=media_analysis_runs.c.media_id,
                order_by=(
                    media_analysis_runs.c.created_at_ms.desc(),
                    media_analysis_runs.c.id.desc(),
                ),
            )
            .label("recency_rank"),
        )
        .where(
            media_analysis_runs.c.media_id.in_(media_ids),
            media_analysis_runs.c.analysis_definition
            == AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        )
        .subquery()
    )
    rows = connection.execute(
        select(
            ranked.c.media_id,
            ranked.c.state,
        )
        .where(ranked.c.recency_rank == 1)
        .order_by(ranked.c.media_id.asc())
    ).mappings()
    for row in rows:
        result[str(row["media_id"])] = str(row["state"])
    return result


def _load_readiness(
    connection: Connection,
    media_id: str,
):
    row = connection.execute(
        select(
            media_metadata.c.display_title,
            media_metadata.c.description,
            func.count(media_canonical_tags.c.tag_key).label("tag_count"),
        )
        .select_from(
            logical_media.outerjoin(
                media_metadata,
                media_metadata.c.media_id == logical_media.c.id,
            ).outerjoin(
                media_canonical_tags,
                media_canonical_tags.c.media_id == logical_media.c.id,
            )
        )
        .where(logical_media.c.id == media_id)
        .group_by(
            logical_media.c.id,
            media_metadata.c.display_title,
            media_metadata.c.description,
        )
    ).mappings().one()
    return derive_content_publication_readiness(
        display_title=row["display_title"],
        description=row["description"],
        canonical_tag_count=int(row["tag_count"]),
    )


def _reject_bound_record(connection: Connection, media_id: str) -> None:
    from kronika.infrastructure.persistence.record_repository import (
        reject_if_media_bound,
    )
    from kronika.domain.records import RecordConflictError

    try:
        reject_if_media_bound(connection, media_id)
    except RecordConflictError as exc:
        raise ContentPublicationBoundRecordError(
            "Legacy publication cannot change a bound record."
        ) from exc


def _get_publication(
    connection: Connection,
    media_id: str,
) -> ContentPublication | None:
    row = connection.execute(
        select(media_content_publications).where(
            media_content_publications.c.media_id == media_id
        )
    ).mappings().first()
    return None if row is None else _publication_from_row(row)


def _publication_from_row(row: object) -> ContentPublication | None:
    mapping = dict(row)  # type: ignore[arg-type]
    if mapping.get("published_at_ms") is None:
        return None
    return ContentPublication(
        media_id=str(mapping["media_id"]),
        published_at_ms=int(mapping["published_at_ms"]),
        publication_origin=ContentPublicationOrigin(
            str(mapping["publication_origin"])
        ),
    )


def _like_pattern(value: str) -> str:
    escaped = (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"
