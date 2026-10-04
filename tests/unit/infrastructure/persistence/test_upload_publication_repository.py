"""Persistence tests for specialized atomic publication transactions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, insert, select, text, update

from kronika.application.ports.upload_publications import (
    FrameNestUploadPublicationRepositoryError,
    UploadPublicationEvidenceConflictError,
)
from kronika.application.ports.upload_sessions import (
    InvalidUploadSessionTransitionError,
)
from kronika.domain.identities import LibraryId, MediaByteIdentityId
from kronika.domain.upload_publications import (
    UploadPublicationCleanupState,
    UploadPublicationState,
    new_upload_publication_reservation,
)
from kronika.domain.uploads import (
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
    devices,
    libraries,
    logical_media,
    metadata,
    physical_media_locations,
    upload_publications,
    upload_sessions,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.upload_publication_repository import (
    SqliteUploadPublicationRepository,
)
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)


DESTINATION_ID = LibraryId(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
IDENTITY_ID = MediaByteIdentityId(
    uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
)


def _engine(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(devices).values(
                id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                display_name="Synthetic device",
            )
        )
        connection.execute(
            insert(libraries).values(
                id=DESTINATION_ID.to_string(),
                device_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                display_name="Published originals",
                path_flavor="posix",
                root_path="/synthetic/published",
            )
        )
    return engine


def _upload(
    upload_id: str = "11111111-1111-4111-8111-111111111111",
    *,
    state: UploadSessionState = UploadSessionState.PUBLISH_PENDING,
    disposition: UploadDuplicateDisposition | None = None,
) -> UploadSession:
    return UploadSession(
        id=UploadSessionId(uuid.UUID(upload_id)),
        state=state,
        storage_key=UploadStorageKey(upload_id.replace("-", "")),
        display_filename=UploadDisplayFilename("synthetic.mp4"),
        declared_size_bytes=8,
        received_size_bytes=8,
        checksum_algorithm="sha256",
        checksum_hex="a" * 64,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=IDENTITY_ID,
        duplicate_disposition=disposition,
        created_at_ms=10,
        updated_at_ms=20,
        expires_at_ms=100,
        failure_code=None,
        version=4,
    )


def test_concurrent_reservation_attempts_converge_on_one_upload_and_target(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    upload = _upload()
    uploads.create(upload)
    reservations = tuple(
        new_upload_publication_reservation(
            upload,
            destination_id=DESTINATION_ID,
            now_ms=21 + index,
        )
        for index in range(2)
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    lambda reservation: publications.get_or_create_reservation(
                        reservation,
                        expected_upload_version=upload.version,
                    ),
                    reservations,
                )
            )

        assert results[0].publication == results[1].publication
        assert results[0].publication is not None
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(upload_publications)) == 1
    finally:
        dispose_engine(engine)


def test_specialized_commit_atomically_publishes_and_cleanup_preserves_provenance(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    upload = _upload(disposition=UploadDuplicateDisposition.KEEP_SEPARATE)
    uploads.create(upload)
    reservation = new_upload_publication_reservation(
        upload,
        destination_id=DESTINATION_ID,
        now_ms=21,
    )
    reserved = publications.get_or_create_reservation(
        reservation,
        expected_upload_version=upload.version,
    )
    assert reserved.publication is not None
    try:
        published = publications.commit_verified_publication(
            upload.id,
            publication_id=reserved.publication.publication_id,
            expected_upload_version=upload.version,
            expected_publication_version=reserved.publication.version,
            updated_at_ms=30,
        )

        assert published.upload.state is UploadSessionState.PUBLISHED
        assert published.upload.duplicate_disposition is UploadDuplicateDisposition.KEEP_SEPARATE
        assert published.publication is not None
        assert published.publication.state is UploadPublicationState.VERIFIED
        assert (
            published.publication.cleanup_state
            is UploadPublicationCleanupState.PENDING
        )
        same = publications.commit_verified_publication(
            upload.id,
            publication_id=published.publication.publication_id,
            expected_upload_version=upload.version,
            expected_publication_version=reserved.publication.version,
            updated_at_ms=31,
        )
        assert same == published

        completed = publications.mark_cleanup_complete(
            upload.id,
            publication_id=published.publication.publication_id,
            expected_publication_version=published.publication.version,
            updated_at_ms=32,
        )
        assert completed.publication is not None
        assert (
            completed.publication.cleanup_state
            is UploadPublicationCleanupState.COMPLETE
        )
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(logical_media)) == 0
            assert (
                connection.scalar(
                    select(func.count()).select_from(physical_media_locations)
                )
                == 0
            )
    finally:
        dispose_engine(engine)


def test_generic_repository_transition_cannot_claim_published_or_cataloged(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    upload = _upload()
    uploads.create(upload)
    try:
        with pytest.raises(InvalidUploadSessionTransitionError):
            uploads.transition_state(
                upload.id,
                expected_state=UploadSessionState.PUBLISH_PENDING,
                target_state=UploadSessionState.PUBLISHED,
                expected_version=upload.version,
                updated_at_ms=30,
            )
        reservation = new_upload_publication_reservation(
            upload,
            destination_id=DESTINATION_ID,
            now_ms=21,
        )
        reserved = publications.get_or_create_reservation(
            reservation,
            expected_upload_version=upload.version,
        )
        assert reserved.publication is not None
        published = publications.commit_verified_publication(
            upload.id,
            publication_id=reserved.publication.publication_id,
            expected_upload_version=upload.version,
            expected_publication_version=reserved.publication.version,
            updated_at_ms=30,
        )
        with pytest.raises(InvalidUploadSessionTransitionError):
            uploads.transition_state(
                upload.id,
                expected_state=UploadSessionState.PUBLISHED,
                target_state=UploadSessionState.CATALOGED,
                expected_version=published.upload.version,
                updated_at_ms=31,
            )
    finally:
        dispose_engine(engine)


def test_upload_state_failure_rolls_back_verified_publication_update(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    upload = _upload()
    uploads.create(upload)
    reservation = new_upload_publication_reservation(
        upload,
        destination_id=DESTINATION_ID,
        now_ms=21,
    )
    reserved = publications.get_or_create_reservation(
        reservation,
        expected_upload_version=upload.version,
    )
    assert reserved.publication is not None
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER reject_synthetic_published_state
                BEFORE UPDATE OF state ON upload_sessions
                WHEN NEW.state = 'published'
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic publication rollback');
                END
                """
            )
        )
    try:
        with pytest.raises(FrameNestUploadPublicationRepositoryError):
            publications.commit_verified_publication(
                upload.id,
                publication_id=reserved.publication.publication_id,
                expected_upload_version=upload.version,
                expected_publication_version=reserved.publication.version,
                updated_at_ms=30,
            )

        current = publications.get_candidate(upload.id)
        assert current is not None
        assert current.upload.state is UploadSessionState.PUBLISH_PENDING
        assert current.publication is not None
        assert current.publication.state is UploadPublicationState.RESERVED
        assert current.publication.verified_at_ms is None
    finally:
        dispose_engine(engine)


def test_existing_reservation_rejects_drifted_upload_evidence_and_legacy_is_visible(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    upload = _upload()
    legacy = _upload(
        "22222222-2222-4222-8222-222222222222",
        state=UploadSessionState.PUBLISHED,
    )
    uploads.create(upload)
    uploads.create(legacy)
    reservation = new_upload_publication_reservation(
        upload,
        destination_id=DESTINATION_ID,
        now_ms=21,
    )
    publications.get_or_create_reservation(
        reservation,
        expected_upload_version=upload.version,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                update(upload_sessions)
                .where(upload_sessions.c.id == upload.id.to_string())
                .values(checksum_hex="b" * 64)
            )
        with pytest.raises(UploadPublicationEvidenceConflictError):
            publications.get_or_create_reservation(
                replace(reservation, checksum_hex="b" * 64),
                expected_upload_version=upload.version,
            )
        legacy_candidate = publications.get_candidate(legacy.id)
        assert legacy_candidate is not None
        assert legacy_candidate.upload.state is UploadSessionState.PUBLISHED
        assert legacy_candidate.publication is None
    finally:
        dispose_engine(engine)


def test_candidate_listing_is_bounded_and_includes_only_pending_or_cleanup_work(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    first = _upload()
    second = _upload("22222222-2222-4222-8222-222222222222")
    legacy = _upload(
        "33333333-3333-4333-8333-333333333333",
        state=UploadSessionState.PUBLISHED,
    )
    for upload in (first, second, legacy):
        uploads.create(upload)
    try:
        page = publications.list_candidates(limit=1)
        assert [candidate.upload.id for candidate in page] == [first.id]
        next_page = publications.list_candidates(
            limit=2,
            after_updated_at_ms=page[0].upload.updated_at_ms,
            after_id=page[0].upload.id.to_string(),
        )
        assert [candidate.upload.id for candidate in next_page] == [second.id]
    finally:
        dispose_engine(engine)
