"""SQLAlchemy Core adapter for administrator catalog removal."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.catalog_removal import (
    CATALOG_OUTCOME_REMOVED,
    ORIGINAL_BYTES_POLICY,
    CatalogRemovalAnalysisSnapshot,
    CatalogRemovalCoverSnapshot,
    CatalogRemovalError,
    CatalogRemovalLocationSnapshot,
    CatalogRemovalNotFoundError,
    CatalogRemovalReceipt,
    CatalogRemovalSnapshot,
    CatalogRemovalStateConflictError,
    CatalogRemovalUploadSnapshot,
    CatalogRemovalYouTubeSnapshot,
    CleanupState,
    StorageClass,
    compute_consequence_fingerprint,
)
from kronika.domain.identities import FrameNestIdentityError, MediaId
from kronika.domain.youtube_acquisition import (
    LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES,
    FrameNestYouTubeAcquisitionError,
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
)
from kronika.infrastructure.persistence.catalog_schema import (
    logical_media,
    media_analysis_runs,
    media_canonical_tags,
    media_catalog_removal_receipts,
    media_content_publications,
    media_covers,
    media_genres,
    media_metadata,
    media_user_alias_tags,
    media_user_aliases,
    physical_media_locations,
    upload_publications,
    youtube_acquisition_claims,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)
from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
    _claim_from_row,
    _values_from_claim,
)

_REPOSITORY_FAILURE_MESSAGE = "Catalog removal operation failed."


class SqliteCatalogRemovalRepository:
    """Synchronous SQLite catalog-removal adapter."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_snapshot(self, media_id: MediaId) -> CatalogRemovalSnapshot | None:
        def operation(connection: Connection) -> CatalogRemovalSnapshot | None:
            return _load_snapshot(connection, media_id)

        try:
            return run_in_transaction(self._engine, operation)
        except (FrameNestIdentityError, SQLAlchemyError) as exc:
            raise CatalogRemovalError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def remove_catalog_media(
        self,
        *,
        media_id: MediaId,
        expected_fingerprint: str,
        request_id: str,
        actor_key: str,
        now_ms: int,
    ) -> CatalogRemovalReceipt:
        def operation(connection: Connection) -> CatalogRemovalReceipt:
            from kronika.application.catalog_removal import (
                CatalogRemovalBoundRecordError,
            )
            from kronika.domain.records import RecordConflictError
            from kronika.infrastructure.persistence.record_repository import (
                reject_if_media_bound,
            )

            try:
                reject_if_media_bound(connection, media_id.to_string())
            except RecordConflictError as exc:
                raise CatalogRemovalBoundRecordError() from exc
            snapshot = _load_snapshot(connection, media_id)
            if snapshot is None:
                raise CatalogRemovalNotFoundError("Media not found.")
            fingerprint = compute_consequence_fingerprint(snapshot)
            if fingerprint != expected_fingerprint:
                raise CatalogRemovalStateConflictError(
                    "Catalog removal consequences changed."
                )
            receipt = _build_receipt(
                snapshot=snapshot,
                fingerprint=fingerprint,
                request_id=request_id,
                actor_key=actor_key,
                now_ms=now_ms,
            )
            connection.execute(
                media_catalog_removal_receipts.insert().values(
                    _receipt_values(receipt)
                )
            )
            _transition_youtube_claims(connection, media_id, now_ms)
            _detach_upload_publications(connection, media_id)
            _delete_analysis_runs(connection, media_id)
            _delete_metadata_graph(connection, media_id)
            connection.execute(
                delete(media_covers).where(
                    media_covers.c.media_id == media_id.to_string()
                )
            )
            connection.execute(
                delete(media_content_publications).where(
                    media_content_publications.c.media_id == media_id.to_string()
                )
            )
            connection.execute(
                delete(physical_media_locations).where(
                    physical_media_locations.c.media_id == media_id.to_string()
                )
            )
            result = connection.execute(
                delete(logical_media).where(
                    logical_media.c.id == media_id.to_string()
                )
            )
            if result.rowcount != 1:
                raise CatalogRemovalNotFoundError("Media not found.")
            return receipt

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (
            CatalogRemovalNotFoundError,
            CatalogRemovalStateConflictError,
        ):
            raise
        except (
            FrameNestIdentityError,
            FrameNestYouTubeAcquisitionError,
            IntegrityError,
            SQLAlchemyError,
        ) as exc:
            raise CatalogRemovalError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_receipt(self, receipt_id: str) -> CatalogRemovalReceipt | None:
        def operation(connection: Connection) -> CatalogRemovalReceipt | None:
            row = (
                connection.execute(
                    select(media_catalog_removal_receipts).where(
                        media_catalog_removal_receipts.c.id == receipt_id
                    )
                )
                .mappings()
                .first()
            )
            return None if row is None else _receipt_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except (TypeError, ValueError, SQLAlchemyError) as exc:
            raise CatalogRemovalError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def update_cleanup_states(
        self,
        receipt_id: str,
        *,
        cover_cleanup_state: CleanupState,
        preview_cleanup_state: CleanupState,
        now_ms: int,
    ) -> CatalogRemovalReceipt:
        def operation(connection: Connection) -> CatalogRemovalReceipt:
            result = connection.execute(
                update(media_catalog_removal_receipts)
                .where(media_catalog_removal_receipts.c.id == receipt_id)
                .values(
                    cover_cleanup_state=cover_cleanup_state,
                    preview_cleanup_state=preview_cleanup_state,
                    cleanup_updated_at_ms=now_ms,
                )
            )
            if result.rowcount != 1:
                raise CatalogRemovalNotFoundError(
                    "Catalog removal receipt not found."
                )
            row = (
                connection.execute(
                    select(media_catalog_removal_receipts).where(
                        media_catalog_removal_receipts.c.id == receipt_id
                    )
                )
                .mappings()
                .one()
            )
            return _receipt_from_row(row)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except CatalogRemovalNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise CatalogRemovalError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _load_snapshot(
    connection: Connection, media_id: MediaId
) -> CatalogRemovalSnapshot | None:
    media_id_text = media_id.to_string()
    media_row = (
        connection.execute(
            select(logical_media).where(logical_media.c.id == media_id_text)
        )
        .mappings()
        .first()
    )
    if media_row is None:
        return None
    metadata_row = (
        connection.execute(
            select(media_metadata).where(media_metadata.c.media_id == media_id_text)
        )
        .mappings()
        .first()
    )
    publication_row = (
        connection.execute(
            select(media_content_publications).where(
                media_content_publications.c.media_id == media_id_text
            )
        )
        .mappings()
        .first()
    )
    location_rows = (
        connection.execute(
            select(physical_media_locations)
            .where(physical_media_locations.c.media_id == media_id_text)
            .order_by(physical_media_locations.c.id.asc())
        )
        .mappings()
        .all()
    )
    upload_rows = (
        connection.execute(
            select(upload_publications)
            .where(upload_publications.c.media_id == media_id_text)
            .order_by(upload_publications.c.upload_id.asc())
        )
        .mappings()
        .all()
    )
    youtube_rows = (
        connection.execute(
            select(youtube_acquisition_claims)
            .where(youtube_acquisition_claims.c.media_id == media_id_text)
            .order_by(youtube_acquisition_claims.c.id.asc())
        )
        .mappings()
        .all()
    )
    cover_row = (
        connection.execute(
            select(media_covers).where(media_covers.c.media_id == media_id_text)
        )
        .mappings()
        .first()
    )
    analysis_rows = (
        connection.execute(
            select(media_analysis_runs)
            .where(media_analysis_runs.c.media_id == media_id_text)
            .order_by(media_analysis_runs.c.id.asc())
        )
        .mappings()
        .all()
    )
    acquisition_source = (
        "unknown"
        if metadata_row is None
        else str(metadata_row["acquisition_source"])
    )
    content_category = (
        "general" if metadata_row is None else str(metadata_row["content_category"])
    )
    storage_class = _storage_class(acquisition_source, upload_rows)
    return CatalogRemovalSnapshot(
        media_id=media_id_text,
        media_kind=str(media_row["media_kind"]),
        media_updated_at_ms=int(media_row["updated_at_ms"]),
        display_title=None
        if metadata_row is None or metadata_row["display_title"] is None
        else str(metadata_row["display_title"]),
        acquisition_source=acquisition_source,
        content_category=content_category,
        publication_state="published" if publication_row is not None else "unpublished",
        published_at_ms=None
        if publication_row is None
        else int(publication_row["published_at_ms"]),
        publication_origin=None
        if publication_row is None
        else str(publication_row["publication_origin"]),
        locations=tuple(
            CatalogRemovalLocationSnapshot(
                location_id=str(row["id"]),
                library_id=str(row["library_id"]),
                relative_path=str(row["relative_path"]),
                availability=str(row["availability"]),
                observed_size_bytes=_optional_int(row["observed_size_bytes"]),
                observed_mtime_ns=_optional_int(row["observed_mtime_ns"]),
            )
            for row in location_rows
        ),
        upload_publications=tuple(
            CatalogRemovalUploadSnapshot(
                upload_id=str(row["upload_id"]),
                destination_id=str(row["destination_id"]),
                relative_target=str(row["relative_target"]),
                byte_identity_id=str(row["byte_identity_id"]),
                state=str(row["state"]),
            )
            for row in upload_rows
        ),
        youtube_claims=tuple(
            CatalogRemovalYouTubeSnapshot(
                claim_id=str(row["id"]),
                state=str(row["state"]),
                media_id=None if row["media_id"] is None else str(row["media_id"]),
                media_location_id=None
                if row["media_location_id"] is None
                else str(row["media_location_id"]),
            )
            for row in youtube_rows
        ),
        cover=None
        if cover_row is None
        else CatalogRemovalCoverSnapshot(
            revision=int(cover_row["revision"]),
            artifact_digest=str(cover_row["artifact_digest"]),
        ),
        analysis_runs=tuple(
            CatalogRemovalAnalysisSnapshot(
                run_id=str(row["id"]),
                version=int(row["version"]),
                provider_submission_occurred=bool(
                    int(row["provider_submission_occurred"] or 0)
                ),
            )
            for row in analysis_rows
        ),
        storage_class=storage_class,
    )


def _storage_class(
    acquisition_source: str, upload_rows: list[Mapping[str, object]]
) -> StorageClass:
    if upload_rows:
        return "server_managed_upload"
    if acquisition_source in {"manual_upload", "youtube_manual_claim"}:
        return "server_managed_upload"
    if acquisition_source == "library_scan":
        return "operator_managed"
    return "unknown"


def _build_receipt(
    *,
    snapshot: CatalogRemovalSnapshot,
    fingerprint: str,
    request_id: str,
    actor_key: str,
    now_ms: int,
) -> CatalogRemovalReceipt:
    cover_state: CleanupState = "none" if snapshot.cover is None else "pending"
    preview_state: CleanupState = "none" if not snapshot.locations else "pending"
    location_ids = [location.location_id for location in snapshot.locations]
    return CatalogRemovalReceipt(
        id=str(uuid.uuid4()),
        occurred_at_ms=now_ms,
        request_id=request_id,
        actor_key=actor_key,
        media_id=snapshot.media_id,
        display_title_snapshot=snapshot.display_title,
        acquisition_source=snapshot.acquisition_source,
        storage_class=snapshot.storage_class,
        was_published=snapshot.publication_state == "published",
        published_at_ms=snapshot.published_at_ms,
        consequence_fingerprint=fingerprint,
        catalog_outcome=CATALOG_OUTCOME_REMOVED,
        original_bytes_policy=ORIGINAL_BYTES_POLICY,
        original_bytes_outcome=snapshot.original_bytes_outcome,
        youtube_claims_transitioned=len(snapshot.youtube_claims),
        upload_publications_detached=len(snapshot.upload_publications),
        analysis_run_count=snapshot.analysis_run_count,
        provider_submission_count=snapshot.provider_submission_count,
        cover_artifact_digest=None
        if snapshot.cover is None
        else snapshot.cover.artifact_digest,
        preview_location_ids_json=None
        if not location_ids
        else json.dumps(location_ids, separators=(",", ":")),
        cover_cleanup_state=cover_state,
        preview_cleanup_state=preview_state,
        cleanup_updated_at_ms=None,
    )


def _transition_youtube_claims(
    connection: Connection, media_id: MediaId, now_ms: int
) -> None:
    rows = (
        connection.execute(
            select(youtube_acquisition_claims).where(
                youtube_acquisition_claims.c.media_id == media_id.to_string()
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        claim = _claim_from_row(row)
        if claim.state not in LIVE_CATALOG_YOUTUBE_ACQUISITION_STATES:
            raise CatalogRemovalError(_REPOSITORY_FAILURE_MESSAGE)
        removed = claim.mark_catalog_removed(now_ms=now_ms)
        result = connection.execute(
            update(youtube_acquisition_claims)
            .where(
                and_(
                    youtube_acquisition_claims.c.id == claim.id.to_string(),
                    youtube_acquisition_claims.c.version == claim.version,
                    youtube_acquisition_claims.c.state == claim.state.value,
                )
            )
            .values(_values_from_claim(removed))
        )
        if result.rowcount != 1:
            raise CatalogRemovalStateConflictError(
                "Catalog removal consequences changed."
            )


def _detach_upload_publications(connection: Connection, media_id: MediaId) -> None:
    connection.execute(
        update(upload_publications)
        .where(upload_publications.c.media_id == media_id.to_string())
        .values(media_id=None, media_location_id=None)
    )


def _delete_analysis_runs(connection: Connection, media_id: MediaId) -> None:
    media_id_text = media_id.to_string()
    connection.execute(
        update(media_analysis_runs)
        .where(media_analysis_runs.c.media_id == media_id_text)
        .values(supersedes_run_id=None)
    )
    connection.execute(
        delete(media_analysis_runs).where(
            media_analysis_runs.c.media_id == media_id_text
        )
    )


def _delete_metadata_graph(connection: Connection, media_id: MediaId) -> None:
    media_id_text = media_id.to_string()
    connection.execute(
        delete(media_user_alias_tags).where(
            media_user_alias_tags.c.media_id == media_id_text
        )
    )
    connection.execute(
        delete(media_user_aliases).where(
            media_user_aliases.c.media_id == media_id_text
        )
    )
    connection.execute(
        delete(media_genres).where(media_genres.c.media_id == media_id_text)
    )
    connection.execute(
        delete(media_canonical_tags).where(
            media_canonical_tags.c.media_id == media_id_text
        )
    )
    connection.execute(
        delete(media_metadata).where(media_metadata.c.media_id == media_id_text)
    )


def _receipt_values(receipt: CatalogRemovalReceipt) -> dict[str, object]:
    return {
        "id": receipt.id,
        "occurred_at_ms": receipt.occurred_at_ms,
        "request_id": receipt.request_id,
        "actor_key": receipt.actor_key,
        "media_id": receipt.media_id,
        "display_title_snapshot": receipt.display_title_snapshot,
        "acquisition_source": receipt.acquisition_source,
        "storage_class": receipt.storage_class,
        "was_published": 1 if receipt.was_published else 0,
        "published_at_ms": receipt.published_at_ms,
        "consequence_fingerprint": receipt.consequence_fingerprint,
        "catalog_outcome": receipt.catalog_outcome,
        "original_bytes_policy": receipt.original_bytes_policy,
        "original_bytes_outcome": receipt.original_bytes_outcome,
        "youtube_claims_transitioned": receipt.youtube_claims_transitioned,
        "upload_publications_detached": receipt.upload_publications_detached,
        "analysis_run_count": receipt.analysis_run_count,
        "provider_submission_count": receipt.provider_submission_count,
        "cover_artifact_digest": receipt.cover_artifact_digest,
        "preview_location_ids_json": receipt.preview_location_ids_json,
        "cover_cleanup_state": receipt.cover_cleanup_state,
        "preview_cleanup_state": receipt.preview_cleanup_state,
        "cleanup_updated_at_ms": receipt.cleanup_updated_at_ms,
    }


def _receipt_from_row(row: Mapping[str, object]) -> CatalogRemovalReceipt:
    return CatalogRemovalReceipt(
        id=str(row["id"]),
        occurred_at_ms=int(row["occurred_at_ms"]),
        request_id=str(row["request_id"]),
        actor_key=str(row["actor_key"]),
        media_id=str(row["media_id"]),
        display_title_snapshot=None
        if row["display_title_snapshot"] is None
        else str(row["display_title_snapshot"]),
        acquisition_source=str(row["acquisition_source"]),
        storage_class=str(row["storage_class"]),  # type: ignore[arg-type]
        was_published=bool(int(row["was_published"])),
        published_at_ms=_optional_int(row["published_at_ms"]),
        consequence_fingerprint=str(row["consequence_fingerprint"]),
        catalog_outcome=str(row["catalog_outcome"]),
        original_bytes_policy=str(row["original_bytes_policy"]),
        original_bytes_outcome=str(row["original_bytes_outcome"]),  # type: ignore[arg-type]
        youtube_claims_transitioned=int(row["youtube_claims_transitioned"]),
        upload_publications_detached=int(row["upload_publications_detached"]),
        analysis_run_count=int(row["analysis_run_count"]),
        provider_submission_count=int(row["provider_submission_count"]),
        cover_artifact_digest=None
        if row["cover_artifact_digest"] is None
        else str(row["cover_artifact_digest"]),
        preview_location_ids_json=None
        if row["preview_location_ids_json"] is None
        else str(row["preview_location_ids_json"]),
        cover_cleanup_state=str(row["cover_cleanup_state"]),  # type: ignore[arg-type]
        preview_cleanup_state=str(row["preview_cleanup_state"]),  # type: ignore[arg-type]
        cleanup_updated_at_ms=_optional_int(row["cleanup_updated_at_ms"]),
    )


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)
