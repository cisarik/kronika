"""SQLAlchemy Core adapter for durable YouTube acquisition claims."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import and_, exists, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.youtube_acquisition_claims import (
    FrameNestYouTubeClaimRepositoryError,
    YouTubeClaimAlreadyExistsError,
    YouTubeClaimConcurrencyConflictError,
    YouTubeClaimNotFoundError,
    YouTubeClaimSourceIdentityConflictError,
)
from kronika.domain.identities import (
    FrameNestIdentityError,
    MediaId,
    MediaLocationId,
    YouTubeAcquisitionClaimId,
)
from kronika.domain.media_classification import AcquisitionSource
from kronika.domain.uploads import UploadSessionId
from kronika.domain.youtube_acquisition import (
    ACTIVE_YOUTUBE_ACQUISITION_STATES,
    FrameNestYouTubeAcquisitionError,
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
    YouTubeConfirmationMethod,
    YouTubeFailureStage,
    YouTubeStagingCleanupState,
    ensure_youtube_transition_allowed,
)
from kronika.infrastructure.persistence.catalog_schema import (
    kronika_records,
    media_content_publications,
    youtube_acquisition_claims,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_REPOSITORY_FAILURE_MESSAGE = "YouTube acquisition claim operation failed."
_ACTIVE_STATE_VALUES = tuple(state.value for state in ACTIVE_YOUTUBE_ACQUISITION_STATES)
_SUCCESS_STATE_VALUES = (
    YouTubeAcquisitionState.CATALOGED.value,
    YouTubeAcquisitionState.DUPLICATE_RESOLVED.value,
)


class SqliteYouTubeAcquisitionClaimRepository:
    """Synchronous SQLite adapter with transactional source-identity ownership."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_or_get_active(
        self,
        claim: YouTubeAcquisitionClaim,
    ) -> tuple[YouTubeAcquisitionClaim, bool]:
        if claim.state is not YouTubeAcquisitionState.CLAIMED:
            raise FrameNestYouTubeClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)

        def operation(
            connection: Connection,
        ) -> tuple[YouTubeAcquisitionClaim, bool]:
            if _row_by_id(connection, claim.id) is not None:
                raise YouTubeClaimAlreadyExistsError("claim already exists")
            active = _active_row_by_source(
                connection,
                extractor_key=claim.extractor_key,
                youtube_video_id=claim.youtube_video_id,
                created_by_login_key=claim.created_by_login_key,
            )
            if active is not None:
                return _claim_from_row(active), False
            connection.execute(
                insert(youtube_acquisition_claims).values(_values_from_claim(claim))
            )
            return claim, True

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except YouTubeClaimAlreadyExistsError:
            raise
        except IntegrityError as exc:
            try:
                existing = self.find_active_by_source_identity(
                    extractor_key=claim.extractor_key,
                    youtube_video_id=claim.youtube_video_id,
                    created_by_login_key=claim.created_by_login_key,
                )
            except FrameNestYouTubeClaimRepositoryError:
                raise FrameNestYouTubeClaimRepositoryError(
                    _REPOSITORY_FAILURE_MESSAGE
                ) from exc
            if existing is not None:
                return existing, False
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def create(self, claim: YouTubeAcquisitionClaim) -> None:
        def operation(connection: Connection) -> None:
            if _row_by_id(connection, claim.id) is not None:
                raise YouTubeClaimAlreadyExistsError("claim already exists")
            active = _active_row_by_source(
                connection,
                extractor_key=claim.extractor_key,
                youtube_video_id=claim.youtube_video_id,
                created_by_login_key=claim.created_by_login_key,
            )
            if active is not None:
                raise YouTubeClaimSourceIdentityConflictError(
                    "source identity already active"
                )
            connection.execute(
                insert(youtube_acquisition_claims).values(_values_from_claim(claim))
            )

        try:
            run_in_immediate_transaction(self._engine, operation)
        except (
            YouTubeClaimAlreadyExistsError,
            YouTubeClaimSourceIdentityConflictError,
        ):
            raise
        except IntegrityError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get(
        self,
        claim_id: YouTubeAcquisitionClaimId,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = _row_by_id(connection, claim_id)
            return None if row is None else _claim_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_active_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
        created_by_login_key: str | None = None,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = _active_row_by_source(
                connection,
                extractor_key=extractor_key,
                youtube_video_id=youtube_video_id,
                created_by_login_key=created_by_login_key,
            )
            return None if row is None else _claim_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_latest_cataloged_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = (
                connection.execute(
                    select(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.extractor_key
                            == extractor_key,
                            youtube_acquisition_claims.c.youtube_video_id
                            == youtube_video_id,
                            youtube_acquisition_claims.c.state.in_(
                                _SUCCESS_STATE_VALUES
                            ),
                            youtube_acquisition_claims.c.media_id.is_not(None),
                            youtube_acquisition_claims.c.media_location_id.is_not(
                                None
                            ),
                        )
                    )
                    .order_by(
                        youtube_acquisition_claims.c.completed_at_ms.desc(),
                        youtube_acquisition_claims.c.created_at_ms.desc(),
                        youtube_acquisition_claims.c.id.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            return None if row is None else _claim_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_owned_live_successful_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
        created_by_login_key: str,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = (
                connection.execute(
                    select(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.extractor_key
                            == extractor_key,
                            youtube_acquisition_claims.c.youtube_video_id
                            == youtube_video_id,
                            youtube_acquisition_claims.c.created_by_login_key
                            == created_by_login_key,
                            youtube_acquisition_claims.c.state.in_(
                                _SUCCESS_STATE_VALUES
                            ),
                            youtube_acquisition_claims.c.media_id.is_not(None),
                            youtube_acquisition_claims.c.media_location_id.is_not(
                                None
                            ),
                        )
                    )
                    .order_by(
                        youtube_acquisition_claims.c.completed_at_ms.desc(),
                        youtube_acquisition_claims.c.created_at_ms.desc(),
                        youtube_acquisition_claims.c.id.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            return None if row is None else _claim_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_published_successful_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = (
                connection.execute(
                    select(youtube_acquisition_claims)
                    .join(
                        media_content_publications,
                        media_content_publications.c.media_id
                        == youtube_acquisition_claims.c.media_id,
                    )
                    .where(
                        and_(
                            youtube_acquisition_claims.c.extractor_key
                            == extractor_key,
                            youtube_acquisition_claims.c.youtube_video_id
                            == youtube_video_id,
                            youtube_acquisition_claims.c.state.in_(
                                _SUCCESS_STATE_VALUES
                            ),
                            youtube_acquisition_claims.c.media_id.is_not(None),
                            youtube_acquisition_claims.c.media_location_id.is_not(
                                None
                            ),
                            ~exists(
                                select(kronika_records.c.id).where(
                                    kronika_records.c.media_id
                                    == youtube_acquisition_claims.c.media_id
                                )
                            ),
                        )
                    )
                    .order_by(
                        youtube_acquisition_claims.c.completed_at_ms.desc(),
                        youtube_acquisition_claims.c.created_at_ms.desc(),
                        youtube_acquisition_claims.c.id.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            return None if row is None else _claim_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_by_upload_id(
        self,
        upload_id: UploadSessionId,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = (
                connection.execute(
                    select(youtube_acquisition_claims).where(
                        youtube_acquisition_claims.c.upload_id
                        == upload_id.to_string()
                    )
                )
                .mappings()
                .first()
            )
            return None if row is None else _claim_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_owned(
        self,
        claim_id: YouTubeAcquisitionClaimId,
        *,
        created_by_login_key: str,
    ) -> YouTubeAcquisitionClaim | None:
        def operation(connection: Connection) -> YouTubeAcquisitionClaim | None:
            row = _row_by_id(connection, claim_id)
            if row is None:
                return None
            claim = _claim_from_row(row)
            if claim.created_by_login_key != created_by_login_key:
                return None
            return claim

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_owned(
        self,
        *,
        created_by_login_key: str,
        limit: int,
        after_created_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[YouTubeAcquisitionClaim, ...]:
        _validate_owned_cursor(limit, after_created_at_ms, after_id)

        def operation(
            connection: Connection,
        ) -> tuple[YouTubeAcquisitionClaim, ...]:
            filters = [
                youtube_acquisition_claims.c.created_by_login_key
                == created_by_login_key
            ]
            if after_created_at_ms is not None:
                assert after_id is not None
                filters.append(
                    or_(
                        youtube_acquisition_claims.c.created_at_ms
                        < after_created_at_ms,
                        and_(
                            youtube_acquisition_claims.c.created_at_ms
                            == after_created_at_ms,
                            youtube_acquisition_claims.c.id < after_id,
                        ),
                    )
                )
            rows = (
                connection.execute(
                    select(youtube_acquisition_claims)
                    .where(and_(*filters))
                    .order_by(
                        youtube_acquisition_claims.c.created_at_ms.desc(),
                        youtube_acquisition_claims.c.id.desc(),
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return tuple(_claim_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def count_active_for_requester(self, *, created_by_login_key: str) -> int:
        def operation(connection: Connection) -> int:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.created_by_login_key
                            == created_by_login_key,
                            youtube_acquisition_claims.c.state.in_(
                                _ACTIVE_STATE_VALUES
                            ),
                        )
                    )
                ).scalar_one()
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def count_global_active_ordinary(self) -> int:
        def operation(connection: Connection) -> int:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.created_by_login_key.is_not(
                                None
                            ),
                            youtube_acquisition_claims.c.state.in_(
                                _ACTIVE_STATE_VALUES
                            ),
                        )
                    )
                ).scalar_one()
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def count_submits_since(
        self,
        *,
        created_by_login_key: str,
        since_ms: int,
    ) -> int:
        def operation(connection: Connection) -> int:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.created_by_login_key
                            == created_by_login_key,
                            youtube_acquisition_claims.c.created_at_ms >= since_ms,
                        )
                    )
                ).scalar_one()
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def count_failed_transitions_since(
        self,
        *,
        created_by_login_key: str,
        since_ms: int,
    ) -> int:
        def operation(connection: Connection) -> int:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.created_by_login_key
                            == created_by_login_key,
                            youtube_acquisition_claims.c.state
                            == YouTubeAcquisitionState.FAILED.value,
                            youtube_acquisition_claims.c.completed_at_ms >= since_ms,
                        )
                    )
                ).scalar_one()
            )

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def private_successful_quota(
        self,
        *,
        created_by_login_key: str,
    ) -> tuple[int, int]:
        def operation(connection: Connection) -> tuple[int, int]:
            rows = (
                connection.execute(
                    select(
                        youtube_acquisition_claims.c.id,
                        youtube_acquisition_claims.c.downloaded_size_bytes,
                    )
                    .select_from(
                        youtube_acquisition_claims.outerjoin(
                            media_content_publications,
                            media_content_publications.c.media_id
                            == youtube_acquisition_claims.c.media_id,
                        )
                    )
                    .where(
                        and_(
                            youtube_acquisition_claims.c.created_by_login_key
                            == created_by_login_key,
                            youtube_acquisition_claims.c.state.in_(
                                _SUCCESS_STATE_VALUES
                            ),
                            youtube_acquisition_claims.c.media_id.is_not(None),
                            media_content_publications.c.media_id.is_(None),
                        )
                    )
                )
                .mappings()
                .all()
            )
            aggregate = 0
            for row in rows:
                size = row["downloaded_size_bytes"]
                if size is not None:
                    aggregate += int(size)
            return len(rows), aggregate

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        def operation(connection: Connection) -> bool:
            row = (
                connection.execute(
                    select(youtube_acquisition_claims.c.id)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.media_id
                            == media_id.to_string(),
                            youtube_acquisition_claims.c.created_by_login_key
                            == login_key,
                            youtube_acquisition_claims.c.state.in_(
                                _SUCCESS_STATE_VALUES
                            ),
                        )
                    )
                    .limit(1)
                )
                .first()
            )
            return row is not None

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_recovery_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[YouTubeAcquisitionClaim, ...]:
        _validate_cursor(limit, after_updated_at_ms, after_id)

        def operation(
            connection: Connection,
        ) -> tuple[YouTubeAcquisitionClaim, ...]:
            filters = [
                youtube_acquisition_claims.c.state.in_(_ACTIVE_STATE_VALUES)
            ]
            if after_updated_at_ms is not None:
                assert after_id is not None
                filters.append(
                    or_(
                        youtube_acquisition_claims.c.updated_at_ms
                        > after_updated_at_ms,
                        and_(
                            youtube_acquisition_claims.c.updated_at_ms
                            == after_updated_at_ms,
                            youtube_acquisition_claims.c.id > after_id,
                        ),
                    )
                )
            rows = (
                connection.execute(
                    select(youtube_acquisition_claims)
                    .where(and_(*filters))
                    .order_by(
                        youtube_acquisition_claims.c.updated_at_ms.asc(),
                        youtube_acquisition_claims.c.id.asc(),
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return tuple(_claim_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_cleanup_candidates(
        self,
        *,
        limit: int,
    ) -> tuple[YouTubeAcquisitionClaim, ...]:
        _validate_cursor(limit, None, None)

        def operation(
            connection: Connection,
        ) -> tuple[YouTubeAcquisitionClaim, ...]:
            rows = (
                connection.execute(
                    select(youtube_acquisition_claims)
                    .where(
                        and_(
                            youtube_acquisition_claims.c.state.in_(
                                _SUCCESS_STATE_VALUES
                                + (YouTubeAcquisitionState.FAILED.value,)
                            ),
                            youtube_acquisition_claims.c.cleanup_state
                            == YouTubeStagingCleanupState.PENDING.value,
                        )
                    )
                    .order_by(
                        youtube_acquisition_claims.c.updated_at_ms.asc(),
                        youtube_acquisition_claims.c.id.asc(),
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return tuple(_claim_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def save(
        self,
        claim: YouTubeAcquisitionClaim,
        *,
        expected_state: YouTubeAcquisitionState,
        expected_version: int,
    ) -> YouTubeAcquisitionClaim:
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
            or claim.version != expected_version + 1
        ):
            raise YouTubeClaimConcurrencyConflictError(
                "claim concurrency conflict"
            )
        if claim.state is not expected_state:
            try:
                ensure_youtube_transition_allowed(expected_state, claim.state)
            except FrameNestYouTubeAcquisitionError as exc:
                raise YouTubeClaimConcurrencyConflictError(
                    "claim concurrency conflict"
                ) from exc

        def operation(connection: Connection) -> YouTubeAcquisitionClaim:
            result = connection.execute(
                update(youtube_acquisition_claims)
                .where(
                    and_(
                        youtube_acquisition_claims.c.id == claim.id.to_string(),
                        youtube_acquisition_claims.c.state
                        == expected_state.value,
                        youtube_acquisition_claims.c.version == expected_version,
                    )
                )
                .values(_values_from_claim(claim))
            )
            if result.rowcount == 1:
                return claim
            row = _row_by_id(connection, claim.id)
            if row is None:
                raise YouTubeClaimNotFoundError("claim not found")
            current = _claim_from_row(row)
            if current == claim:
                return current
            raise YouTubeClaimConcurrencyConflictError(
                "claim concurrency conflict"
            )

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (
            YouTubeClaimNotFoundError,
            YouTubeClaimConcurrencyConflictError,
        ):
            raise
        except IntegrityError as exc:
            raise YouTubeClaimSourceIdentityConflictError(
                "source identity already active"
            ) from exc
        except SQLAlchemyError as exc:
            raise FrameNestYouTubeClaimRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _row_by_id(
    connection: Connection,
    claim_id: YouTubeAcquisitionClaimId,
) -> Mapping[str, object] | None:
    return (
        connection.execute(
            select(youtube_acquisition_claims).where(
                youtube_acquisition_claims.c.id == claim_id.to_string()
            )
        )
        .mappings()
        .first()
    )


def _active_row_by_source(
    connection: Connection,
    *,
    extractor_key: str,
    youtube_video_id: str,
    created_by_login_key: str | None,
) -> Mapping[str, object] | None:
    filters = [
        youtube_acquisition_claims.c.extractor_key == extractor_key,
        youtube_acquisition_claims.c.youtube_video_id == youtube_video_id,
        youtube_acquisition_claims.c.state.in_(_ACTIVE_STATE_VALUES),
    ]
    if created_by_login_key is None:
        filters.append(youtube_acquisition_claims.c.created_by_login_key.is_(None))
    else:
        filters.append(
            youtube_acquisition_claims.c.created_by_login_key
            == created_by_login_key
        )
    return (
        connection.execute(
            select(youtube_acquisition_claims)
            .where(and_(*filters))
            .limit(1)
        )
        .mappings()
        .first()
    )


def _values_from_claim(claim: YouTubeAcquisitionClaim) -> dict[str, object]:
    return {
        "id": claim.id.to_string(),
        "state": claim.state.value,
        "acquisition_source": claim.acquisition_source.value,
        "submitted_url": claim.submitted_url,
        "canonical_url": claim.canonical_url,
        "youtube_video_id": claim.youtube_video_id,
        "extractor_key": claim.extractor_key,
        "retry_of_claim_id": _identity_value(claim.retry_of_claim_id),
        "resolved_claim_id": _identity_value(claim.resolved_claim_id),
        "upload_id": _identity_value(claim.upload_id),
        "media_id": _identity_value(claim.media_id),
        "media_location_id": _identity_value(claim.media_location_id),
        "confirmation_method": claim.confirmation_method.value,
        "confirmed_at_ms": claim.confirmed_at_ms,
        "upstream_title": claim.upstream_title,
        "upstream_channel": claim.upstream_channel,
        "upstream_channel_id": claim.upstream_channel_id,
        "upstream_source_date": claim.upstream_source_date,
        "downloader_name": claim.downloader_name,
        "downloader_version": claim.downloader_version,
        "extractor_version": claim.extractor_version,
        "selected_video_format_id": claim.selected_video_format_id,
        "selected_audio_format_id": claim.selected_audio_format_id,
        "remote_filename": claim.remote_filename,
        "generated_filename": claim.generated_filename,
        "staging_key": claim.staging_key,
        "downloaded_size_bytes": claim.downloaded_size_bytes,
        "created_at_ms": claim.created_at_ms,
        "updated_at_ms": claim.updated_at_ms,
        "downloaded_at_ms": claim.downloaded_at_ms,
        "completed_at_ms": claim.completed_at_ms,
        "catalog_removed_at_ms": claim.catalog_removed_at_ms,
        "failure_stage": None
        if claim.failure_stage is None
        else claim.failure_stage.value,
        "failure_code": claim.failure_code,
        "cleanup_state": claim.cleanup_state.value,
        "cleanup_completed_at_ms": claim.cleanup_completed_at_ms,
        "version": claim.version,
        "created_by_login_key": claim.created_by_login_key,
    }


def _claim_from_row(row: Mapping[str, object]) -> YouTubeAcquisitionClaim:
    try:
        owner = row["created_by_login_key"] if "created_by_login_key" in row.keys() else None
        return YouTubeAcquisitionClaim(
            id=YouTubeAcquisitionClaimId.from_string(str(row["id"])),
            state=YouTubeAcquisitionState(str(row["state"])),
            acquisition_source=AcquisitionSource(str(row["acquisition_source"])),
            submitted_url=str(row["submitted_url"]),
            canonical_url=str(row["canonical_url"]),
            youtube_video_id=str(row["youtube_video_id"]),
            extractor_key=str(row["extractor_key"]),
            retry_of_claim_id=_optional_claim_id(row["retry_of_claim_id"]),
            resolved_claim_id=_optional_claim_id(row["resolved_claim_id"]),
            upload_id=_optional_upload_id(row["upload_id"]),
            media_id=_optional_media_id(row["media_id"]),
            media_location_id=_optional_location_id(row["media_location_id"]),
            confirmation_method=YouTubeConfirmationMethod(
                str(row["confirmation_method"])
            ),
            confirmed_at_ms=int(row["confirmed_at_ms"]),
            upstream_title=_optional_text(row["upstream_title"]),
            upstream_channel=_optional_text(row["upstream_channel"]),
            upstream_channel_id=_optional_text(row["upstream_channel_id"]),
            upstream_source_date=_optional_text(row["upstream_source_date"]),
            downloader_name=_optional_text(row["downloader_name"]),
            downloader_version=_optional_text(row["downloader_version"]),
            extractor_version=_optional_text(row["extractor_version"]),
            selected_video_format_id=_optional_text(
                row["selected_video_format_id"]
            ),
            selected_audio_format_id=_optional_text(
                row["selected_audio_format_id"]
            ),
            remote_filename=_optional_text(row["remote_filename"]),
            generated_filename=str(row["generated_filename"]),
            staging_key=str(row["staging_key"]),
            downloaded_size_bytes=_optional_int(row["downloaded_size_bytes"]),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            downloaded_at_ms=_optional_int(row["downloaded_at_ms"]),
            completed_at_ms=_optional_int(row["completed_at_ms"]),
            catalog_removed_at_ms=_optional_int(row["catalog_removed_at_ms"])
            if "catalog_removed_at_ms" in row
            else None,
            failure_stage=None
            if row["failure_stage"] is None
            else YouTubeFailureStage(str(row["failure_stage"])),
            failure_code=_optional_text(row["failure_code"]),
            cleanup_state=YouTubeStagingCleanupState(str(row["cleanup_state"])),
            cleanup_completed_at_ms=_optional_int(
                row["cleanup_completed_at_ms"]
            ),
            version=int(row["version"]),
            created_by_login_key=_optional_text(owner),
        )
    except (
        FrameNestIdentityError,
        FrameNestYouTubeAcquisitionError,
        TypeError,
        ValueError,
    ) as exc:
        raise FrameNestYouTubeClaimRepositoryError(
            _REPOSITORY_FAILURE_MESSAGE
        ) from exc


def _identity_value(value: object) -> str | None:
    if value is None:
        return None
    to_string = getattr(value, "to_string", None)
    if not callable(to_string):
        raise FrameNestYouTubeClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
    return str(to_string())


def _optional_claim_id(value: object) -> YouTubeAcquisitionClaimId | None:
    return (
        None
        if value is None
        else YouTubeAcquisitionClaimId.from_string(str(value))
    )


def _optional_upload_id(value: object) -> UploadSessionId | None:
    return None if value is None else UploadSessionId.from_string(str(value))


def _optional_media_id(value: object) -> MediaId | None:
    return None if value is None else MediaId.from_string(str(value))


def _optional_location_id(value: object) -> MediaLocationId | None:
    return None if value is None else MediaLocationId.from_string(str(value))


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _validate_cursor(
    limit: object,
    after_updated_at_ms: object,
    after_id: object,
) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or limit > 1_000
    ):
        raise FrameNestYouTubeClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
    if after_updated_at_ms is None and after_id is None:
        return
    if (
        isinstance(after_updated_at_ms, bool)
        or not isinstance(after_updated_at_ms, int)
        or after_updated_at_ms < 0
        or not isinstance(after_id, str)
        or not after_id
    ):
        raise FrameNestYouTubeClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)


def _validate_owned_cursor(
    limit: object,
    after_created_at_ms: object,
    after_id: object,
) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or limit > 1_000
    ):
        raise FrameNestYouTubeClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
    if after_created_at_ms is None and after_id is None:
        return
    if (
        isinstance(after_created_at_ms, bool)
        or not isinstance(after_created_at_ms, int)
        or after_created_at_ms < 0
        or not isinstance(after_id, str)
        or not after_id
    ):
        raise FrameNestYouTubeClaimRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
