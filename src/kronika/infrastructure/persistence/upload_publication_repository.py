"""SQLite adapter for durable atomic upload publication provenance."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.media_repository import (
    FrameNestMediaRepositoryError,
    MediaAlreadyExistsError,
    MediaLocationAlreadyExistsError,
    MediaLocationNotUniqueError,
    MediaLocationReferenceNotFoundError,
)
from kronika.application.ports.upload_publications import (
    FrameNestUploadPublicationRepositoryError,
    UnsupportedLegacyUploadPublicationStateError,
    UploadCatalogInconsistencyError,
    UploadCatalogStateConflictError,
    UploadPublicationCandidate,
    UploadPublicationConcurrencyConflictError,
    UploadPublicationEvidenceConflictError,
    UploadPublicationNotFoundError,
    UploadPublicationStateConflictError,
    UploadPublicationTargetConflictError,
)
from kronika.domain.identities import (
    FrameNestIdentityError,
    LibraryId,
    MediaByteIdentityId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.media import LogicalMedia, MediaLocation
from kronika.domain.media_metadata import MediaMetadata, derive_collection_state
from kronika.domain.upload_publications import (
    FrameNestUploadPublicationError,
    UploadPublication,
    UploadPublicationCleanupState,
    UploadPublicationId,
    UploadPublicationRelativePath,
    UploadPublicationState,
    ensure_publication_matches_upload,
    ensure_upload_is_publication_candidate,
)
from kronika.domain.uploads import (
    FrameNestUploadSessionError,
    UploadDisplayFilename,
    UploadDuplicateDisposition,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.infrastructure.persistence.catalog_schema import (
    logical_media,
    media_canonical_tags,
    media_metadata,
    physical_media_locations,
    upload_publications,
    upload_sessions,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)
from kronika.infrastructure.persistence.media_repository import (
    _insert_location,
    _insert_media,
)

_REPOSITORY_FAILURE_MESSAGE = "Upload publication operation failed."


class SqliteUploadPublicationRepository:
    """Synchronous SQLite publication adapter with specialized state commits."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_candidate(
        self,
        upload_id: UploadSessionId,
    ) -> UploadPublicationCandidate | None:
        def operation(connection: Connection) -> UploadPublicationCandidate | None:
            upload_row = _upload_row(connection, upload_id)
            if upload_row is None:
                return None
            return _candidate_from_rows(
                upload_row,
                _publication_row(connection, upload_id),
            )

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadPublicationCandidate, ...]:
        _validate_limit_and_cursor(limit, after_updated_at_ms, after_id)

        def operation(connection: Connection) -> tuple[UploadPublicationCandidate, ...]:
            joined = upload_sessions.outerjoin(
                upload_publications,
                upload_publications.c.upload_id == upload_sessions.c.id,
            )
            filters = [
                or_(
                    upload_sessions.c.state == UploadSessionState.PUBLISH_PENDING.value,
                    and_(
                        upload_sessions.c.state == UploadSessionState.PUBLISHED.value,
                        upload_publications.c.state
                        == UploadPublicationState.VERIFIED.value,
                        upload_publications.c.cleanup_state
                        == UploadPublicationCleanupState.PENDING.value,
                    ),
                )
            ]
            if after_updated_at_ms is not None:
                assert after_id is not None
                filters.append(
                    or_(
                        upload_sessions.c.updated_at_ms > after_updated_at_ms,
                        and_(
                            upload_sessions.c.updated_at_ms == after_updated_at_ms,
                            upload_sessions.c.id > after_id,
                        ),
                    )
                )
            ids = connection.execute(
                select(upload_sessions.c.id)
                .select_from(joined)
                .where(and_(*filters))
                .order_by(
                    upload_sessions.c.updated_at_ms.asc(),
                    upload_sessions.c.id.asc(),
                )
                .limit(limit)
            ).scalars()
            candidates: list[UploadPublicationCandidate] = []
            for upload_id_text in ids:
                upload_id = UploadSessionId.from_string(str(upload_id_text))
                upload_row = _upload_row(connection, upload_id)
                assert upload_row is not None
                candidates.append(
                    _candidate_from_rows(
                        upload_row,
                        _publication_row(connection, upload_id),
                    )
                )
            return tuple(candidates)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except (FrameNestIdentityError, FrameNestUploadSessionError, SQLAlchemyError) as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def get_or_create_reservation(
        self,
        reservation: UploadPublication,
        *,
        expected_upload_version: int,
    ) -> UploadPublicationCandidate:
        _validate_non_negative(expected_upload_version)

        def operation(connection: Connection) -> UploadPublicationCandidate:
            upload_row = _upload_row(connection, reservation.upload_id)
            if upload_row is None:
                raise UploadPublicationNotFoundError("upload publication not found")
            upload = _upload_from_row(upload_row)
            existing_row = _publication_row(connection, reservation.upload_id)
            if existing_row is not None:
                existing = _publication_from_row(existing_row)
                _require_matching_evidence(existing, upload)
                return UploadPublicationCandidate(upload=upload, publication=existing)
            try:
                ensure_upload_is_publication_candidate(upload)
                ensure_publication_matches_upload(reservation, upload)
            except FrameNestUploadPublicationError as exc:
                raise UploadPublicationEvidenceConflictError(
                    "upload publication evidence conflict"
                ) from exc
            if upload.version != expected_upload_version:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            if reservation.state is not UploadPublicationState.RESERVED:
                raise UploadPublicationStateConflictError(
                    "upload publication state conflict"
                )
            try:
                connection.execute(
                    insert(upload_publications).values(_publication_values(reservation))
                )
            except IntegrityError as exc:
                raise UploadPublicationTargetConflictError(
                    "upload publication target conflict"
                ) from exc
            return _require_candidate(connection, reservation.upload_id)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def commit_verified_publication(
        self,
        upload_id: UploadSessionId,
        *,
        publication_id: UploadPublicationId,
        expected_upload_version: int,
        expected_publication_version: int,
        updated_at_ms: int,
    ) -> UploadPublicationCandidate:
        _validate_non_negative(expected_upload_version)
        _validate_non_negative(expected_publication_version)
        _validate_non_negative(updated_at_ms)

        def operation(connection: Connection) -> UploadPublicationCandidate:
            candidate = _require_candidate(connection, upload_id)
            publication = candidate.publication
            if publication is None:
                if candidate.upload.state in {
                    UploadSessionState.PUBLISHED,
                    UploadSessionState.CATALOGED,
                }:
                    raise UnsupportedLegacyUploadPublicationStateError(
                        "unsupported legacy upload publication state"
                    )
                raise UploadPublicationNotFoundError("upload publication not found")
            _require_publication_id(publication, publication_id)
            _require_matching_evidence(publication, candidate.upload)
            if (
                candidate.upload.state is UploadSessionState.PUBLISHED
                and publication.state is UploadPublicationState.VERIFIED
            ):
                return candidate
            if candidate.upload.state is not UploadSessionState.PUBLISH_PENDING:
                raise UploadPublicationStateConflictError(
                    "upload publication state conflict"
                )
            if publication.state is not UploadPublicationState.RESERVED:
                raise UploadPublicationStateConflictError(
                    "upload publication state conflict"
                )
            if (
                candidate.upload.version != expected_upload_version
                or publication.version != expected_publication_version
            ):
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            publication_result = connection.execute(
                update(upload_publications)
                .where(
                    and_(
                        upload_publications.c.upload_id == upload_id.to_string(),
                        upload_publications.c.publication_id
                        == publication_id.to_string(),
                        upload_publications.c.state
                        == UploadPublicationState.RESERVED.value,
                        upload_publications.c.version == expected_publication_version,
                    )
                )
                .values(
                    state=UploadPublicationState.VERIFIED.value,
                    cleanup_state=UploadPublicationCleanupState.PENDING.value,
                    verified_at_ms=updated_at_ms,
                    updated_at_ms=updated_at_ms,
                    version=upload_publications.c.version + 1,
                )
            )
            if publication_result.rowcount != 1:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            upload_result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == upload_id.to_string(),
                        upload_sessions.c.state
                        == UploadSessionState.PUBLISH_PENDING.value,
                        upload_sessions.c.version == expected_upload_version,
                        upload_sessions.c.received_size_bytes
                        == upload_sessions.c.declared_size_bytes,
                        upload_sessions.c.declared_size_bytes
                        == publication.expected_size_bytes,
                        upload_sessions.c.checksum_algorithm
                        == publication.checksum_algorithm,
                        upload_sessions.c.checksum_hex == publication.checksum_hex,
                        upload_sessions.c.validated_media_kind
                        == publication.validated_media_kind.value,
                        upload_sessions.c.validated_format
                        == publication.validated_format.value,
                        upload_sessions.c.byte_identity_id
                        == publication.byte_identity_id.to_string(),
                    )
                )
                .values(
                    state=UploadSessionState.PUBLISHED.value,
                    updated_at_ms=updated_at_ms,
                    failure_code=None,
                    version=upload_sessions.c.version + 1,
                )
            )
            if upload_result.rowcount != 1:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            return _require_candidate(connection, upload_id)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def mark_cleanup_complete(
        self,
        upload_id: UploadSessionId,
        *,
        publication_id: UploadPublicationId,
        expected_publication_version: int,
        updated_at_ms: int,
    ) -> UploadPublicationCandidate:
        _validate_non_negative(expected_publication_version)
        _validate_non_negative(updated_at_ms)

        def operation(connection: Connection) -> UploadPublicationCandidate:
            candidate = _require_candidate(connection, upload_id)
            publication = candidate.publication
            if publication is None:
                if candidate.upload.state in {
                    UploadSessionState.PUBLISHED,
                    UploadSessionState.CATALOGED,
                }:
                    raise UnsupportedLegacyUploadPublicationStateError(
                        "unsupported legacy upload publication state"
                    )
                raise UploadPublicationNotFoundError("upload publication not found")
            _require_publication_id(publication, publication_id)
            _require_matching_evidence(publication, candidate.upload)
            if publication.cleanup_state is UploadPublicationCleanupState.COMPLETE:
                return candidate
            if (
                candidate.upload.state is not UploadSessionState.PUBLISHED
                or publication.state is not UploadPublicationState.VERIFIED
                or publication.cleanup_state is not UploadPublicationCleanupState.PENDING
            ):
                raise UploadPublicationStateConflictError(
                    "upload publication state conflict"
                )
            if publication.version != expected_publication_version:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            result = connection.execute(
                update(upload_publications)
                .where(
                    and_(
                        upload_publications.c.upload_id == upload_id.to_string(),
                        upload_publications.c.publication_id
                        == publication_id.to_string(),
                        upload_publications.c.state
                        == UploadPublicationState.VERIFIED.value,
                        upload_publications.c.cleanup_state
                        == UploadPublicationCleanupState.PENDING.value,
                        upload_publications.c.version == expected_publication_version,
                    )
                )
                .values(
                    cleanup_state=UploadPublicationCleanupState.COMPLETE.value,
                    cleanup_completed_at_ms=updated_at_ms,
                    updated_at_ms=updated_at_ms,
                    version=upload_publications.c.version + 1,
                )
            )
            if result.rowcount != 1:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            return _require_candidate(connection, upload_id)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_catalog_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadPublicationCandidate, ...]:
        _validate_limit_and_cursor(limit, after_updated_at_ms, after_id)

        def operation(connection: Connection) -> tuple[UploadPublicationCandidate, ...]:
            joined = upload_sessions.join(
                upload_publications,
                upload_publications.c.upload_id == upload_sessions.c.id,
            )
            filters = [
                upload_sessions.c.state == UploadSessionState.PUBLISHED.value,
                upload_publications.c.state == UploadPublicationState.VERIFIED.value,
                upload_publications.c.cleanup_state
                == UploadPublicationCleanupState.COMPLETE.value,
                upload_publications.c.media_id.is_(None),
                upload_publications.c.media_location_id.is_(None),
            ]
            if after_updated_at_ms is not None:
                assert after_id is not None
                filters.append(
                    or_(
                        upload_sessions.c.updated_at_ms > after_updated_at_ms,
                        and_(
                            upload_sessions.c.updated_at_ms == after_updated_at_ms,
                            upload_sessions.c.id > after_id,
                        ),
                    )
                )
            ids = connection.execute(
                select(upload_sessions.c.id)
                .select_from(joined)
                .where(and_(*filters))
                .order_by(
                    upload_sessions.c.updated_at_ms.asc(),
                    upload_sessions.c.id.asc(),
                )
                .limit(limit)
            ).scalars()
            candidates: list[UploadPublicationCandidate] = []
            for upload_id_text in ids:
                loaded_id = UploadSessionId.from_string(str(upload_id_text))
                upload_row = _upload_row(connection, loaded_id)
                assert upload_row is not None
                candidates.append(
                    _candidate_from_rows(
                        upload_row,
                        _publication_row(connection, loaded_id),
                    )
                )
            return tuple(candidates)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except (FrameNestIdentityError, FrameNestUploadSessionError, SQLAlchemyError) as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def find_cataloged_by_byte_identity(
        self,
        byte_identity_id: MediaByteIdentityId,
        *,
        exclude_upload_id: UploadSessionId,
    ) -> UploadPublicationCandidate | None:
        def operation(connection: Connection) -> UploadPublicationCandidate | None:
            upload_id_text = connection.execute(
                select(upload_sessions.c.id)
                .join(
                    upload_publications,
                    upload_publications.c.upload_id == upload_sessions.c.id,
                )
                .where(
                    and_(
                        upload_sessions.c.id != exclude_upload_id.to_string(),
                        upload_sessions.c.byte_identity_id
                        == byte_identity_id.to_string(),
                        upload_sessions.c.state
                        == UploadSessionState.CATALOGED.value,
                        upload_publications.c.media_id.is_not(None),
                        upload_publications.c.media_location_id.is_not(None),
                    )
                )
                .order_by(
                    upload_sessions.c.updated_at_ms.asc(),
                    upload_sessions.c.id.asc(),
                )
                .limit(1)
            ).scalar_one_or_none()
            if upload_id_text is None:
                return None
            loaded_id = UploadSessionId.from_string(str(upload_id_text))
            return _require_candidate(connection, loaded_id)

        try:
            return run_in_transaction(self._engine, operation)
        except (
            FrameNestIdentityError,
            FrameNestUploadSessionError,
            SQLAlchemyError,
        ) as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def commit_cataloged_publication(
        self,
        upload_id: UploadSessionId,
        *,
        media: LogicalMedia,
        location: MediaLocation,
        expected_upload_version: int,
        expected_publication_version: int,
        updated_at_ms: int,
        metadata: MediaMetadata | None = None,
    ) -> UploadPublicationCandidate:
        _validate_non_negative(expected_upload_version)
        _validate_non_negative(expected_publication_version)
        _validate_non_negative(updated_at_ms)
        if location.media_id != media.id:
            raise UploadCatalogStateConflictError("upload catalog state conflict")
        if metadata is not None and (
            metadata.media_id != media.id or metadata.genre_keys
        ):
            raise UploadCatalogStateConflictError("upload catalog state conflict")

        def operation(connection: Connection) -> UploadPublicationCandidate:
            candidate = _require_candidate(connection, upload_id)
            publication = candidate.publication
            if publication is None:
                raise UploadPublicationNotFoundError("upload publication not found")
            _require_matching_evidence(publication, candidate.upload)
            if (publication.media_id is None) != (publication.media_location_id is None):
                raise UploadCatalogInconsistencyError(
                    "upload catalog linkage inconsistent"
                )
            if (
                candidate.upload.state is UploadSessionState.CATALOGED
                and publication.media_id is not None
                and publication.media_location_id is not None
            ):
                if not _catalog_links_resolve(connection, publication):
                    raise UploadCatalogInconsistencyError(
                        "upload catalog linkage inconsistent"
                    )
                return candidate
            if (
                candidate.upload.state is UploadSessionState.CATALOGED
                or publication.media_id is not None
                or publication.media_location_id is not None
            ):
                raise UploadCatalogInconsistencyError(
                    "upload catalog linkage inconsistent"
                )
            if (
                candidate.upload.state is not UploadSessionState.PUBLISHED
                or publication.state is not UploadPublicationState.VERIFIED
                or publication.cleanup_state
                is not UploadPublicationCleanupState.COMPLETE
            ):
                raise UploadCatalogStateConflictError("upload catalog state conflict")
            if (
                location.library_id != publication.destination_id
                or location.relative_path.value != publication.relative_path.value
                or location.observed_size_bytes != publication.expected_size_bytes
            ):
                raise UploadCatalogStateConflictError("upload catalog state conflict")
            if (
                candidate.upload.version != expected_upload_version
                or publication.version != expected_publication_version
            ):
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            try:
                _insert_media(connection, media)
                _insert_location(connection, location)
                if metadata is not None:
                    collection = derive_collection_state(
                        None,
                        None,
                        metadata.tag_keys,
                        metadata.updated_at_ms,
                    )
                    connection.execute(
                        insert(media_metadata).values(
                            media_id=metadata.media_id.to_string(),
                            display_title=None
                            if metadata.display_title is None
                            else metadata.display_title.value,
                            description=None
                            if metadata.description is None
                            else metadata.description.value,
                            content_category=metadata.content_category.value,
                            acquisition_source=metadata.acquisition_source.value,
                            creator_attribution_kind=(
                                None
                                if metadata.creator_attribution_kind is None
                                else metadata.creator_attribution_kind.value
                            ),
                            creator_stable_id=metadata.creator_stable_id,
                            creator_handle=metadata.creator_handle,
                            creator_display_name=metadata.creator_display_name,
                            collection_key=(
                                None
                                if collection.collection_key is None
                                else collection.collection_key.value
                            ),
                            processed_at_ms=collection.processed_at_ms,
                            created_at_ms=metadata.created_at_ms,
                            updated_at_ms=metadata.updated_at_ms,
                        )
                    )
                    for position, key in enumerate(metadata.tag_keys):
                        connection.execute(
                            insert(media_canonical_tags).values(
                                media_id=metadata.media_id.to_string(),
                                tag_key=key.value,
                                position=position,
                            )
                        )
            except (
                MediaAlreadyExistsError,
                MediaLocationAlreadyExistsError,
                MediaLocationNotUniqueError,
                MediaLocationReferenceNotFoundError,
                FrameNestMediaRepositoryError,
            ) as exc:
                raise FrameNestUploadPublicationRepositoryError(
                    _REPOSITORY_FAILURE_MESSAGE
                ) from exc
            publication_result = connection.execute(
                update(upload_publications)
                .where(
                    and_(
                        upload_publications.c.upload_id == upload_id.to_string(),
                        upload_publications.c.state
                        == UploadPublicationState.VERIFIED.value,
                        upload_publications.c.cleanup_state
                        == UploadPublicationCleanupState.COMPLETE.value,
                        upload_publications.c.media_id.is_(None),
                        upload_publications.c.media_location_id.is_(None),
                        upload_publications.c.version == expected_publication_version,
                    )
                )
                .values(
                    media_id=media.id.to_string(),
                    media_location_id=location.id.to_string(),
                    updated_at_ms=updated_at_ms,
                    version=upload_publications.c.version + 1,
                )
            )
            if publication_result.rowcount != 1:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            upload_result = connection.execute(
                update(upload_sessions)
                .where(
                    and_(
                        upload_sessions.c.id == upload_id.to_string(),
                        upload_sessions.c.state
                        == UploadSessionState.PUBLISHED.value,
                        upload_sessions.c.version == expected_upload_version,
                    )
                )
                .values(
                    state=UploadSessionState.CATALOGED.value,
                    updated_at_ms=updated_at_ms,
                    failure_code=None,
                    version=upload_sessions.c.version + 1,
                )
            )
            if upload_result.rowcount != 1:
                raise UploadPublicationConcurrencyConflictError(
                    "upload publication concurrency conflict"
                )
            return _require_candidate(connection, upload_id)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestUploadPublicationRepositoryError:
            raise
        except IntegrityError as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc
        except SQLAlchemyError as exc:
            raise FrameNestUploadPublicationRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _require_candidate(
    connection: Connection,
    upload_id: UploadSessionId,
) -> UploadPublicationCandidate:
    upload_row = _upload_row(connection, upload_id)
    if upload_row is None:
        raise UploadPublicationNotFoundError("upload publication not found")
    return _candidate_from_rows(upload_row, _publication_row(connection, upload_id))


def _candidate_from_rows(
    upload_row: Mapping[str, object],
    publication_row: Mapping[str, object] | None,
) -> UploadPublicationCandidate:
    return UploadPublicationCandidate(
        upload=_upload_from_row(upload_row),
        publication=None
        if publication_row is None
        else _publication_from_row(publication_row),
    )


def _upload_row(
    connection: Connection,
    upload_id: UploadSessionId,
) -> Mapping[str, object] | None:
    return (
        connection.execute(
            select(upload_sessions).where(upload_sessions.c.id == upload_id.to_string())
        )
        .mappings()
        .first()
    )


def _publication_row(
    connection: Connection,
    upload_id: UploadSessionId,
) -> Mapping[str, object] | None:
    return (
        connection.execute(
            select(upload_publications).where(
                upload_publications.c.upload_id == upload_id.to_string()
            )
        )
        .mappings()
        .first()
    )


def _publication_values(publication: UploadPublication) -> dict[str, object]:
    return {
        "upload_id": publication.upload_id.to_string(),
        "publication_id": publication.publication_id.to_string(),
        "destination_id": publication.destination_id.to_string(),
        "relative_target": publication.relative_path.value,
        "byte_identity_id": publication.byte_identity_id.to_string(),
        "expected_size_bytes": publication.expected_size_bytes,
        "checksum_algorithm": publication.checksum_algorithm,
        "checksum_hex": publication.checksum_hex,
        "validated_media_kind": publication.validated_media_kind.value,
        "validated_format": publication.validated_format.value,
        "state": publication.state.value,
        "cleanup_state": publication.cleanup_state.value,
        "created_at_ms": publication.created_at_ms,
        "updated_at_ms": publication.updated_at_ms,
        "verified_at_ms": publication.verified_at_ms,
        "cleanup_completed_at_ms": publication.cleanup_completed_at_ms,
        "version": publication.version,
        "media_id": None
        if publication.media_id is None
        else publication.media_id.to_string(),
        "media_location_id": None
        if publication.media_location_id is None
        else publication.media_location_id.to_string(),
    }


def _publication_from_row(row: Mapping[str, object]) -> UploadPublication:
    try:
        return UploadPublication(
            upload_id=UploadSessionId.from_string(str(row["upload_id"])),
            publication_id=UploadPublicationId.from_string(str(row["publication_id"])),
            destination_id=LibraryId.from_string(str(row["destination_id"])),
            relative_path=UploadPublicationRelativePath(str(row["relative_target"])),
            byte_identity_id=MediaByteIdentityId.from_string(
                str(row["byte_identity_id"])
            ),
            expected_size_bytes=int(row["expected_size_bytes"]),
            checksum_algorithm=str(row["checksum_algorithm"]),
            checksum_hex=str(row["checksum_hex"]),
            validated_media_kind=UploadValidatedMediaKind(
                str(row["validated_media_kind"])
            ),
            validated_format=UploadValidatedFormat(str(row["validated_format"])),
            state=UploadPublicationState(str(row["state"])),
            cleanup_state=UploadPublicationCleanupState(str(row["cleanup_state"])),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            verified_at_ms=None
            if row["verified_at_ms"] is None
            else int(row["verified_at_ms"]),
            cleanup_completed_at_ms=None
            if row["cleanup_completed_at_ms"] is None
            else int(row["cleanup_completed_at_ms"]),
            version=int(row["version"]),
            media_id=None
            if row["media_id"] is None
            else MediaId.from_string(str(row["media_id"])),
            media_location_id=None
            if row["media_location_id"] is None
            else MediaLocationId.from_string(str(row["media_location_id"])),
        )
    except (
        FrameNestIdentityError,
        FrameNestUploadPublicationError,
        ValueError,
    ) as exc:
        raise FrameNestUploadPublicationRepositoryError(
            _REPOSITORY_FAILURE_MESSAGE
        ) from exc


def _catalog_links_resolve(
    connection: Connection,
    publication: UploadPublication,
) -> bool:
    assert publication.media_id is not None
    assert publication.media_location_id is not None
    media_exists = (
        connection.execute(
            select(logical_media.c.id).where(
                logical_media.c.id == publication.media_id.to_string()
            )
        ).first()
        is not None
    )
    location_row = (
        connection.execute(
            select(
                physical_media_locations.c.id,
                physical_media_locations.c.media_id,
                physical_media_locations.c.library_id,
                physical_media_locations.c.relative_path,
            ).where(
                physical_media_locations.c.id
                == publication.media_location_id.to_string()
            )
        )
        .mappings()
        .first()
    )
    if not media_exists or location_row is None:
        return False
    return (
        str(location_row["media_id"]) == publication.media_id.to_string()
        and str(location_row["library_id"]) == publication.destination_id.to_string()
        and str(location_row["relative_path"]) == publication.relative_path.value
    )


def _upload_from_row(row: Mapping[str, object]) -> UploadSession:
    try:
        return UploadSession(
            id=UploadSessionId.from_string(str(row["id"])),
            state=UploadSessionState(str(row["state"])),
            storage_key=UploadStorageKey(str(row["storage_key"])),
            display_filename=UploadDisplayFilename(str(row["display_filename"])),
            declared_size_bytes=int(row["declared_size_bytes"]),
            received_size_bytes=int(row["received_size_bytes"]),
            checksum_algorithm=None
            if row["checksum_algorithm"] is None
            else str(row["checksum_algorithm"]),
            checksum_hex=None
            if row["checksum_hex"] is None
            else str(row["checksum_hex"]),
            validated_media_kind=None
            if row["validated_media_kind"] is None
            else UploadValidatedMediaKind(str(row["validated_media_kind"])),
            validated_format=None
            if row["validated_format"] is None
            else UploadValidatedFormat(str(row["validated_format"])),
            byte_identity_id=None
            if row["byte_identity_id"] is None
            else MediaByteIdentityId.from_string(str(row["byte_identity_id"])),
            duplicate_disposition=None
            if row["duplicate_disposition"] is None
            else UploadDuplicateDisposition(str(row["duplicate_disposition"])),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            expires_at_ms=int(row["expires_at_ms"]),
            failure_code=None
            if row["failure_code"] is None
            else str(row["failure_code"]),
            version=int(row["version"]),
        )
    except (FrameNestIdentityError, FrameNestUploadSessionError, ValueError) as exc:
        raise FrameNestUploadPublicationRepositoryError(
            _REPOSITORY_FAILURE_MESSAGE
        ) from exc


def _require_matching_evidence(
    publication: UploadPublication,
    upload: UploadSession,
) -> None:
    try:
        ensure_publication_matches_upload(publication, upload)
    except FrameNestUploadPublicationError as exc:
        raise UploadPublicationEvidenceConflictError(
            "upload publication evidence conflict"
        ) from exc


def _require_publication_id(
    publication: UploadPublication,
    expected: UploadPublicationId,
) -> None:
    if publication.publication_id != expected:
        raise UploadPublicationEvidenceConflictError(
            "upload publication evidence conflict"
        )


def _validate_limit_and_cursor(
    limit: object,
    after_updated_at_ms: object,
    after_id: object,
) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise FrameNestUploadPublicationRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
    if after_updated_at_ms is None and after_id is None:
        return
    if (
        isinstance(after_updated_at_ms, bool)
        or not isinstance(after_updated_at_ms, int)
        or after_updated_at_ms < 0
        or not isinstance(after_id, str)
        or not after_id
    ):
        raise FrameNestUploadPublicationRepositoryError(_REPOSITORY_FAILURE_MESSAGE)


def _validate_non_negative(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UploadPublicationConcurrencyConflictError(
            "upload publication concurrency conflict"
        )
