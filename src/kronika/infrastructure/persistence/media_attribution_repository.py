"""Read-only contribution attribution queries over existing login-key stamps."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import exists, func, or_, select, union
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import ColumnElement

from kronika.application.ports.media_attribution import (
    CONTRIBUTION_SOURCE_ORDER,
    CONTRIBUTION_SOURCE_UPLOAD,
    CONTRIBUTION_SOURCE_X,
    CONTRIBUTION_SOURCE_YOUTUBE,
    FrameNestMediaAttributionRepositoryError,
    MediaContributionAttribution,
    WorkspaceMediaItem,
    WorkspaceMediaPage,
)
from kronika.domain.content_publication import derive_content_publication_readiness
from kronika.domain.identities import MediaId
from kronika.infrastructure.persistence.catalog_schema import (
    kronika_records,
    logical_media,
    media_canonical_tags,
    media_content_publications,
    media_metadata,
    upload_publications,
    upload_sessions,
    x_assets,
    x_post_claims,
    youtube_acquisition_claims,
)
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Contribution attribution query failed."


class SqliteMediaAttributionRepository:
    """SELECT-only adapter over upload, YouTube, and X attribution stamps."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        if not login_key:
            return False

        def operation(connection: Connection) -> bool:
            row = connection.execute(
                select(upload_publications.c.media_id)
                .select_from(
                    upload_publications.join(
                        upload_sessions,
                        upload_sessions.c.id == upload_publications.c.upload_id,
                    )
                )
                .where(
                    upload_publications.c.media_id == media_id.to_string(),
                    upload_sessions.c.created_by_login_key == login_key,
                )
                .limit(1)
            ).first()
            return row is not None

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestMediaAttributionRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_workspace_media(
        self,
        *,
        login_key: str,
        limit: int,
        offset: int,
    ) -> WorkspaceMediaPage:
        def operation(connection: Connection) -> WorkspaceMediaPage:
            attributed = _attributed_media_ids(login_key).subquery("attributed")
            filtered = (
                select(
                    logical_media.c.id.label("media_id"),
                    logical_media.c.media_kind,
                    logical_media.c.created_at_ms,
                    logical_media.c.updated_at_ms,
                    media_metadata.c.display_title,
                    media_metadata.c.description,
                    media_metadata.c.content_category,
                    media_metadata.c.acquisition_source,
                    media_content_publications.c.media_id.label("publication_media_id"),
                )
                .select_from(
                    attributed.join(
                        logical_media,
                        logical_media.c.id == attributed.c.media_id,
                    )
                    .outerjoin(
                        media_metadata,
                        media_metadata.c.media_id == logical_media.c.id,
                    )
                    .outerjoin(
                        media_content_publications,
                        media_content_publications.c.media_id == logical_media.c.id,
                    )
                )
                .subquery()
            )
            total = connection.execute(
                select(func.count()).select_from(filtered)
            ).scalar_one()
            page_rows = connection.execute(
                select(filtered)
                .order_by(
                    filtered.c.created_at_ms.desc(),
                    filtered.c.media_id.asc(),
                )
                .limit(limit)
                .offset(offset)
            ).mappings().all()
            media_ids = tuple(str(row["media_id"]) for row in page_rows)
            tag_counts = _load_tag_counts(connection, media_ids)
            sources_by_media = _load_caller_sources(
                connection, media_ids, login_key
            )
            items: list[WorkspaceMediaItem] = []
            for row in page_rows:
                media_id = str(row["media_id"])
                readiness = derive_content_publication_readiness(
                    display_title=row["display_title"],
                    description=row["description"],
                    canonical_tag_count=tag_counts.get(media_id, 0),
                )
                items.append(
                    WorkspaceMediaItem(
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
                        contribution_sources=sources_by_media.get(media_id, ()),
                        content_publication_state=(
                            "published"
                            if row["publication_media_id"] is not None
                            else "unpublished"
                        ),
                        publication_ready=readiness.ready,
                        missing_fields=readiness.missing_fields,
                    )
                )
            return WorkspaceMediaPage(
                items=tuple(items),
                total=int(total),
                limit=limit,
                offset=offset,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestMediaAttributionRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_contributions_for_media_ids(
        self,
        media_ids: tuple[str, ...],
    ) -> dict[str, tuple[MediaContributionAttribution, ...]]:
        def operation(
            connection: Connection,
        ) -> dict[str, tuple[MediaContributionAttribution, ...]]:
            return load_media_contributions(connection, media_ids)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestMediaAttributionRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def contributor_match_clause(contributor_login_key: str) -> ColumnElement[bool]:
    """Return an OR of EXISTS stamps matching one normalized login key."""
    upload_match = exists(
        select(upload_publications.c.media_id)
        .select_from(
            upload_publications.join(
                upload_sessions,
                upload_sessions.c.id == upload_publications.c.upload_id,
            )
        )
        .where(
            upload_publications.c.media_id == logical_media.c.id,
            upload_sessions.c.created_by_login_key == contributor_login_key,
        )
    )
    youtube_match = exists(
        select(youtube_acquisition_claims.c.id).where(
            youtube_acquisition_claims.c.media_id == logical_media.c.id,
            youtube_acquisition_claims.c.created_by_login_key
            == contributor_login_key,
        )
    )
    x_match = exists(
        select(x_assets.c.id)
        .select_from(
            x_assets.join(
                x_post_claims,
                x_assets.c.claim_id == x_post_claims.c.id,
            )
        )
        .where(
            x_assets.c.media_id == logical_media.c.id,
            x_post_claims.c.created_by_login_key == contributor_login_key,
        )
    )
    return or_(upload_match, youtube_match, x_match)


def load_media_contributions(
    connection: Connection,
    media_ids: tuple[str, ...],
) -> dict[str, tuple[MediaContributionAttribution, ...]]:
    """Load contribution stamps for a bounded media-id page."""
    grouped: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    if not media_ids:
        return {}
    for media_id, login_key, source in _contribution_rows(connection, media_ids):
        if not login_key:
            continue
        grouped[media_id][login_key].add(source)
    result: dict[str, tuple[MediaContributionAttribution, ...]] = {}
    for media_id, by_login in grouped.items():
        attributions = []
        for login_key in sorted(by_login):
            sources = tuple(
                source
                for source in CONTRIBUTION_SOURCE_ORDER
                if source in by_login[login_key]
            )
            attributions.append(
                MediaContributionAttribution(
                    login_key=login_key,
                    sources=sources,
                )
            )
        result[media_id] = tuple(attributions)
    return result


def _attributed_media_ids(login_key: str):
    """Own common records precede contributions. Contributions cover unbound media."""
    def bound_media():
        return select(kronika_records.c.media_id).where(
            kronika_records.c.media_id.is_not(None)
        )

    owned = select(kronika_records.c.media_id.label("media_id")).where(
        kronika_records.c.owner_login_key == login_key,
        kronika_records.c.media_id.is_not(None),
    )
    upload_ids = (
        select(upload_publications.c.media_id.label("media_id"))
        .select_from(
            upload_publications.join(
                upload_sessions,
                upload_sessions.c.id == upload_publications.c.upload_id,
            )
        )
        .where(
            upload_sessions.c.created_by_login_key == login_key,
            upload_publications.c.media_id.is_not(None),
            upload_publications.c.media_id.not_in(bound_media()),
        )
    )
    youtube_ids = select(
        youtube_acquisition_claims.c.media_id.label("media_id")
    ).where(
        youtube_acquisition_claims.c.created_by_login_key == login_key,
        youtube_acquisition_claims.c.media_id.is_not(None),
        youtube_acquisition_claims.c.media_id.not_in(bound_media()),
    )
    x_ids = (
        select(x_assets.c.media_id.label("media_id"))
        .select_from(
            x_assets.join(
                x_post_claims,
                x_assets.c.claim_id == x_post_claims.c.id,
            )
        )
        .where(
            x_post_claims.c.created_by_login_key == login_key,
            x_assets.c.media_id.is_not(None),
            x_assets.c.media_id.not_in(bound_media()),
        )
    )
    return union(owned, upload_ids, youtube_ids, x_ids)


def _load_tag_counts(
    connection: Connection,
    media_ids: tuple[str, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not media_ids:
        return counts
    rows = connection.execute(
        select(
            media_canonical_tags.c.media_id,
            func.count().label("tag_count"),
        )
        .where(media_canonical_tags.c.media_id.in_(media_ids))
        .group_by(media_canonical_tags.c.media_id)
    )
    for row in rows:
        counts[str(row.media_id)] = int(row.tag_count)
    return counts


def _load_caller_sources(
    connection: Connection,
    media_ids: tuple[str, ...],
    login_key: str,
) -> dict[str, tuple[str, ...]]:
    by_media: dict[str, set[str]] = defaultdict(set)
    if not media_ids:
        return {}
    for media_id, found_login, source in _contribution_rows(connection, media_ids):
        if found_login == login_key:
            by_media[media_id].add(source)
    return {
        media_id: tuple(
            source
            for source in CONTRIBUTION_SOURCE_ORDER
            if source in sources
        )
        for media_id, sources in by_media.items()
    }


def _contribution_rows(
    connection: Connection,
    media_ids: tuple[str, ...],
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    upload_rows = connection.execute(
        select(
            upload_publications.c.media_id,
            upload_sessions.c.created_by_login_key,
        )
        .select_from(
            upload_publications.join(
                upload_sessions,
                upload_sessions.c.id == upload_publications.c.upload_id,
            )
        )
        .where(
            upload_publications.c.media_id.in_(media_ids),
            upload_sessions.c.created_by_login_key.is_not(None),
        )
    )
    for row in upload_rows:
        rows.append(
            (str(row.media_id), str(row.created_by_login_key), CONTRIBUTION_SOURCE_UPLOAD)
        )
    youtube_rows = connection.execute(
        select(
            youtube_acquisition_claims.c.media_id,
            youtube_acquisition_claims.c.created_by_login_key,
        ).where(
            youtube_acquisition_claims.c.media_id.in_(media_ids),
            youtube_acquisition_claims.c.created_by_login_key.is_not(None),
        )
    )
    for row in youtube_rows:
        rows.append(
            (
                str(row.media_id),
                str(row.created_by_login_key),
                CONTRIBUTION_SOURCE_YOUTUBE,
            )
        )
    x_rows = connection.execute(
        select(
            x_assets.c.media_id,
            x_post_claims.c.created_by_login_key,
        )
        .select_from(
            x_assets.join(
                x_post_claims,
                x_assets.c.claim_id == x_post_claims.c.id,
            )
        )
        .where(
            x_assets.c.media_id.in_(media_ids),
            x_post_claims.c.created_by_login_key.is_not(None),
        )
    )
    for row in x_rows:
        rows.append(
            (str(row.media_id), str(row.created_by_login_key), CONTRIBUTION_SOURCE_X)
        )
    return rows
