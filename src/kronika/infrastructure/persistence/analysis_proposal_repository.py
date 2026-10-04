"""SQLite adapter for durable analysis proposals. Never runs analysis."""

from __future__ import annotations

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.analysis_proposal import (
    ANALYSIS_PROPOSAL_STATUS_OPEN,
    AdminAnalysisProposalItem,
    AdminAnalysisProposalPage,
    AnalysisProposal,
    AnalysisProposalMediaNotFoundError,
    FrameNestAnalysisProposalRepositoryError,
)
from kronika.domain.content_publication import derive_content_publication_readiness
from kronika.domain.identities import MediaId
from kronika.infrastructure.persistence.catalog_schema import (
    kronika_records,
    logical_media,
    media_analysis_proposals,
    media_canonical_tags,
    media_content_publications,
    media_metadata,
)
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Analysis proposal operation failed."


class SqliteAnalysisProposalRepository:
    """Synchronous SQLite adapter for analysis proposals."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_proposal(
        self,
        *,
        proposal_id: str,
        media_id: MediaId,
        login_key: str,
        created_at_ms: int,
        status: str = ANALYSIS_PROPOSAL_STATUS_OPEN,
    ) -> AnalysisProposal:
        media_id_text = media_id.to_string()

        def operation(connection: Connection) -> AnalysisProposal:
            exists = connection.execute(
                select(logical_media.c.id).where(logical_media.c.id == media_id_text)
            ).first()
            if exists is None:
                raise AnalysisProposalMediaNotFoundError()
            owner = connection.execute(
                select(kronika_records.c.owner_login_key).where(
                    kronika_records.c.media_id == media_id_text
                )
            ).scalar_one_or_none()
            if owner is not None and owner != login_key:
                raise AnalysisProposalMediaNotFoundError()
            connection.execute(
                insert(media_analysis_proposals).values(
                    id=proposal_id,
                    media_id=media_id_text,
                    proposed_by_login_key=login_key,
                    created_at_ms=created_at_ms,
                    status=status,
                )
            )
            return AnalysisProposal(
                proposal_id=proposal_id,
                media_id=media_id_text,
                proposed_by_login_key=login_key,
                created_at_ms=created_at_ms,
                status=status,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except AnalysisProposalMediaNotFoundError:
            raise
        except IntegrityError as exc:
            raise AnalysisProposalMediaNotFoundError() from exc
        except SQLAlchemyError as exc:
            raise FrameNestAnalysisProposalRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_open_proposals(
        self,
        *,
        limit: int,
        offset: int,
    ) -> AdminAnalysisProposalPage:
        def operation(connection: Connection) -> AdminAnalysisProposalPage:
            filtered = (
                select(
                    media_analysis_proposals.c.id.label("proposal_id"),
                    media_analysis_proposals.c.media_id,
                    media_analysis_proposals.c.proposed_by_login_key,
                    media_analysis_proposals.c.created_at_ms,
                    media_analysis_proposals.c.status,
                    media_metadata.c.display_title,
                    media_metadata.c.description,
                    media_content_publications.c.media_id.label("publication_media_id"),
                )
                .select_from(
                    media_analysis_proposals.outerjoin(
                        media_metadata,
                        media_metadata.c.media_id == media_analysis_proposals.c.media_id,
                    ).outerjoin(
                        media_content_publications,
                        media_content_publications.c.media_id
                        == media_analysis_proposals.c.media_id,
                    )
                )
                .where(media_analysis_proposals.c.status == ANALYSIS_PROPOSAL_STATUS_OPEN)
                .subquery()
            )
            total = connection.execute(
                select(func.count()).select_from(filtered)
            ).scalar_one()
            page_rows = connection.execute(
                select(filtered)
                .order_by(
                    filtered.c.created_at_ms.desc(),
                    filtered.c.proposal_id.desc(),
                )
                .limit(limit)
                .offset(offset)
            ).mappings().all()
            media_ids = tuple(str(row["media_id"]) for row in page_rows)
            tag_counts = _load_tag_counts(connection, media_ids)
            items: list[AdminAnalysisProposalItem] = []
            for row in page_rows:
                media_id = str(row["media_id"])
                readiness = derive_content_publication_readiness(
                    display_title=row["display_title"],
                    description=row["description"],
                    canonical_tag_count=tag_counts.get(media_id, 0),
                )
                items.append(
                    AdminAnalysisProposalItem(
                        proposal_id=str(row["proposal_id"]),
                        media_id=media_id,
                        proposer_login=str(row["proposed_by_login_key"]),
                        created_at_ms=int(row["created_at_ms"]),
                        status=str(row["status"]),
                        display_title=(
                            None
                            if row["display_title"] is None
                            else str(row["display_title"])
                        ),
                        content_publication_state=(
                            "published"
                            if row["publication_media_id"] is not None
                            else "unpublished"
                        ),
                        publication_ready=readiness.ready,
                        missing_fields=readiness.missing_fields,
                    )
                )
            return AdminAnalysisProposalPage(
                items=tuple(items),
                total=int(total),
                limit=limit,
                offset=offset,
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestAnalysisProposalRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def count_created_since(
        self,
        *,
        login_key: str,
        since_ms: int,
    ) -> int:
        def operation(connection: Connection) -> int:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(media_analysis_proposals)
                    .where(
                        media_analysis_proposals.c.proposed_by_login_key
                        == login_key,
                        media_analysis_proposals.c.created_at_ms >= since_ms,
                    )
                ).scalar_one()
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestAnalysisProposalRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


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
