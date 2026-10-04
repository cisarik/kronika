"""End-to-end synthetic recovery evidence for atomic upload publication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, insert, select, text

from kronika.application.ports.quarantine_storage import QuarantineWriteFailedError
from kronika.application.upload_publication import (
    PublishPendingUpload,
    UploadPublicationCleanupPendingError,
    UploadPublicationInfrastructureError,
    UploadPublicationSourceError,
)
from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN
from tests.support.record_access import install_synthetic_caller
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
from kronika.infrastructure.filesystem.published_media_storage import (
    FilesystemPublishedMediaStorage,
)
from kronika.infrastructure.filesystem.quarantine_storage import (
    FilesystemQuarantineStorage,
)
from kronika.infrastructure.persistence.catalog_schema import (
    devices,
    libraries,
    logical_media,
    physical_media_locations,
    upload_publications,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_publication_repository import (
    SqliteUploadPublicationRepository,
)
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)

DESTINATION_ID = LibraryId(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))


@dataclass
class _Fixture:
    engine: object
    uploads: SqliteUploadSessionRepository
    publications: SqliteUploadPublicationRepository
    quarantine: FilesystemQuarantineStorage
    published: FilesystemPublishedMediaStorage
    publisher: PublishPendingUpload
    quarantine_root: Path
    published_root: Path


class _FailOnceCleanupQuarantine:
    def __init__(self, delegate: FilesystemQuarantineStorage) -> None:
        self._delegate = delegate
        self._failed = False

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def remove(self, storage_key: UploadStorageKey) -> None:
        if not self._failed:
            self._failed = True
            raise QuarantineWriteFailedError("quarantine cleanup failed")
        self._delegate.remove(storage_key)


def _fixture(tmp_path: Path, *, quarantine_override=None) -> _Fixture:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    quarantine_root = tmp_path / "quarantine"
    published_root = tmp_path / "published"
    quarantine_root.mkdir()
    published_root.mkdir()
    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(database_path)
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
                root_path=str(published_root),
            )
        )
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    quarantine = FilesystemQuarantineStorage(quarantine_root)
    published = FilesystemPublishedMediaStorage(
        DESTINATION_ID,
        published_root,
        forbidden_roots=(quarantine_root, database_path.parent),
    )
    publisher = PublishPendingUpload(
        publications,
        published,
        quarantine if quarantine_override is None else quarantine_override(quarantine),
        now_ms=iter(range(30, 1000)).__next__,
    )
    return _Fixture(
        engine,
        uploads,
        publications,
        quarantine,
        published,
        publisher,
        quarantine_root,
        published_root,
    )


def _pending_upload(
    data: bytes,
    upload_id: str = "11111111-1111-4111-8111-111111111111",
    *,
    disposition: UploadDuplicateDisposition | None = None,
) -> UploadSession:
    return UploadSession(
        id=UploadSessionId(uuid.UUID(upload_id)),
        state=UploadSessionState.PUBLISH_PENDING,
        storage_key=UploadStorageKey(upload_id.replace("-", "")),
        display_filename=UploadDisplayFilename("synthetic-client.mp4"),
        declared_size_bytes=len(data),
        received_size_bytes=len(data),
        checksum_algorithm="sha256",
        checksum_hex=hashlib.sha256(data).hexdigest(),
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=MediaByteIdentityId(
            uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        ),
        duplicate_disposition=disposition,
        created_at_ms=10,
        updated_at_ms=20,
        expires_at_ms=10_000,
        failure_code=None,
        version=4,
    )


def _stage(fixture: _Fixture, upload: UploadSession, data: bytes) -> None:
    fixture.uploads.create(upload)
    writer = fixture.quarantine.open_writer(upload.storage_key, offset=0, create=True)
    try:
        assert writer.write(data) == len(data)
        writer.flush_and_fsync()
    finally:
        writer.close()


def _counts(fixture: _Fixture) -> tuple[int, int, int]:
    with fixture.engine.connect() as connection:  # type: ignore[union-attr]
        return (
            int(connection.scalar(select(func.count()).select_from(upload_publications))),
            int(connection.scalar(select(func.count()).select_from(logical_media))),
            int(
                connection.scalar(
                    select(func.count()).select_from(physical_media_locations)
                )
            ),
        )


def test_unique_upload_reaches_published_with_exact_bytes_cleanup_and_zero_catalog(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"\x00\x00\x00\x18ftypmp42synthetic-publication"
    upload = _pending_upload(data)
    _stage(fixture, upload, data)
    try:
        result = fixture.publisher.publish_owned_blocking(upload.id)
        candidate = fixture.publications.get_candidate(upload.id)

        assert result.state == "published"
        assert result.cleanup_state == "complete"
        assert candidate is not None and candidate.publication is not None
        assert candidate.upload.state is UploadSessionState.PUBLISHED
        assert candidate.publication.state is UploadPublicationState.VERIFIED
        assert (
            candidate.publication.cleanup_state
            is UploadPublicationCleanupState.COMPLETE
        )
        assert (
            fixture.published_root / candidate.publication.relative_path.value
        ).read_bytes() == data
        assert fixture.quarantine.file_size(upload.storage_key) is None
        assert _counts(fixture) == (1, 0, 0)
        assert fixture.publisher.publish_owned_blocking(upload.id) == result
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_cleanup_failure_leaves_published_provenance_pending_and_retry_is_isolated(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, quarantine_override=_FailOnceCleanupQuarantine)
    data = b"cleanup-retry-synthetic"
    upload = _pending_upload(data)
    _stage(fixture, upload, data)
    try:
        with pytest.raises(UploadPublicationCleanupPendingError):
            fixture.publisher.publish_owned_blocking(upload.id)

        pending = fixture.publications.get_candidate(upload.id)
        assert pending is not None and pending.publication is not None
        assert pending.upload.state is UploadSessionState.PUBLISHED
        assert (
            pending.publication.cleanup_state
            is UploadPublicationCleanupState.PENDING
        )
        assert fixture.quarantine.file_size(upload.storage_key) == len(data)

        completed = fixture.publisher.publish_owned_blocking(upload.id)
        assert completed.cleanup_state == "complete"
        assert fixture.quarantine.file_size(upload.storage_key) is None
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_database_rollback_after_durable_target_preserves_source_and_retry_adopts_target(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"database-rollback-after-filesystem"
    upload = _pending_upload(data)
    _stage(fixture, upload, data)
    with fixture.engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                """
                CREATE TRIGGER reject_synthetic_publish
                BEFORE UPDATE OF state ON upload_sessions
                WHEN NEW.state = 'published'
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic rollback');
                END
                """
            )
        )
    try:
        with pytest.raises(UploadPublicationInfrastructureError):
            fixture.publisher.publish_owned_blocking(upload.id)

        pending = fixture.publications.get_candidate(upload.id)
        assert pending is not None and pending.publication is not None
        assert pending.upload.state is UploadSessionState.PUBLISH_PENDING
        assert pending.publication.state is UploadPublicationState.RESERVED
        final = fixture.published_root / pending.publication.relative_path.value
        assert final.read_bytes() == data
        assert fixture.quarantine.file_size(upload.storage_key) == len(data)

        with fixture.engine.begin() as connection:  # type: ignore[union-attr]
            connection.execute(text("DROP TRIGGER reject_synthetic_publish"))
        recovered = fixture.publisher.publish_owned_blocking(upload.id)
        assert recovered.state == "published"
        assert recovered.cleanup_state == "complete"
        assert final.read_bytes() == data
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_cleanup_marker_rollback_retries_after_source_is_already_absent(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"cleanup-marker-rollback-after-source-removal"
    upload = _pending_upload(data)
    _stage(fixture, upload, data)
    with fixture.engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                """
                CREATE TRIGGER reject_synthetic_cleanup_marker
                BEFORE UPDATE OF cleanup_state ON upload_publications
                WHEN NEW.cleanup_state = 'complete'
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic cleanup rollback');
                END
                """
            )
        )
    try:
        with pytest.raises(UploadPublicationInfrastructureError):
            fixture.publisher.publish_owned_blocking(upload.id)

        pending = fixture.publications.get_candidate(upload.id)
        assert pending is not None and pending.publication is not None
        assert pending.upload.state is UploadSessionState.PUBLISHED
        assert (
            pending.publication.cleanup_state
            is UploadPublicationCleanupState.PENDING
        )
        assert fixture.quarantine.file_size(upload.storage_key) is None

        with fixture.engine.begin() as connection:  # type: ignore[union-attr]
            connection.execute(text("DROP TRIGGER reject_synthetic_cleanup_marker"))
        recovered = fixture.publisher.publish_owned_blocking(upload.id)
        assert recovered.state == "published"
        assert recovered.cleanup_state == "complete"
        assert fixture.quarantine.file_size(upload.storage_key) is None
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_missing_source_before_verified_target_fails_closed_without_false_published(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"missing-source"
    upload = _pending_upload(data)
    fixture.uploads.create(upload)
    try:
        with pytest.raises(UploadPublicationSourceError):
            fixture.publisher.publish_owned_blocking(upload.id)

        current = fixture.publications.get_candidate(upload.id)
        assert current is not None and current.publication is not None
        assert current.upload.state is UploadSessionState.PUBLISH_PENDING
        assert current.publication.state is UploadPublicationState.RESERVED
        assert not (
            fixture.published_root / current.publication.relative_path.value
        ).exists()
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_concurrent_attempts_share_one_lock_reservation_target_and_completed_result(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"single-process-concurrent-publication"
    upload = _pending_upload(data)
    _stage(fixture, upload, data)
    locks = UploadSessionLockRegistry()

    async def attempt():
        async with locks.lease(upload.id):
            return await asyncio.to_thread(
                fixture.publisher.publish_owned_blocking,
                upload.id,
            )

    async def concurrent_attempts():
        return await asyncio.gather(attempt(), attempt())

    try:
        first, second = asyncio.run(concurrent_attempts())
        assert first == second
        assert first.state == "published"
        assert first.cleanup_state == "complete"
        assert _counts(fixture) == (1, 0, 0)
        assert len(list(fixture.published_root.glob("*.mp4"))) == 1
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_kept_exact_duplicate_gets_distinct_target_without_physical_deduplication(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"identical-kept-duplicate-bytes"
    first = _pending_upload(data)
    second = _pending_upload(
        data,
        "22222222-2222-4222-8222-222222222222",
        disposition=UploadDuplicateDisposition.KEEP_SEPARATE,
    )
    _stage(fixture, first, data)
    _stage(fixture, second, data)
    try:
        fixture.publisher.publish_owned_blocking(first.id)
        fixture.publisher.publish_owned_blocking(second.id)
        first_candidate = fixture.publications.get_candidate(first.id)
        second_candidate = fixture.publications.get_candidate(second.id)
        assert first_candidate is not None and first_candidate.publication is not None
        assert second_candidate is not None and second_candidate.publication is not None
        assert (
            first_candidate.publication.byte_identity_id
            == second_candidate.publication.byte_identity_id
        )
        assert (
            first_candidate.publication.relative_path
            != second_candidate.publication.relative_path
        )
        assert len(list(fixture.published_root.glob("*.mp4"))) == 2
        assert _counts(fixture) == (2, 0, 0)
    finally:
        dispose_engine(fixture.engine)  # type: ignore[arg-type]


def test_full_application_lifecycle_automatically_publishes_valid_synthetic_gif(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "full-app-database" / "catalog.sqlite3"
    quarantine_root = tmp_path / "full-app-quarantine"
    published_root = tmp_path / "full-app-published"
    cache_root = tmp_path / "full-app-cache"
    quarantine_root.mkdir()
    published_root.mkdir()
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=cache_root,
        upload_quarantine_root=quarantine_root,
        upload_publication_library_id=DESTINATION_ID.to_string(),
        upload_max_patch_bytes=1_048_576,
        upload_min_free_space_reserve_bytes=0,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(database_path)
    try:
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
                    root_path=str(published_root),
                )
            )
    finally:
        dispose_engine(engine)
    payload_buffer = BytesIO()
    Image.new("P", (2, 2), color=1).save(payload_buffer, format="GIF")
    payload = payload_buffer.getvalue()

    with TestClient(
        install_synthetic_caller(
            create_app(settings=settings),
            "admin@example.com",
            role=ROLE_ADMIN,
        )
    ) as client:
        created = client.post(
            "/api/uploads",
            json={
                "display_filename": "synthetic.gif",
                "declared_size_bytes": len(payload),
            },
        )
        assert created.status_code == 201
        upload_id = created.json()["id"]
        patched = client.patch(
            f"/api/uploads/{upload_id}",
            content=payload,
            headers={
                "content-type": "application/offset+octet-stream",
                "upload-offset": "0",
            },
        )
        assert patched.status_code == 200
        completed = client.post(f"/api/uploads/{upload_id}/complete")
        assert completed.status_code == 200
        status = completed.json()
        for _ in range(400):
            status_response = client.get(f"/api/uploads/{upload_id}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["state"] == "cataloged":
                break
            time.sleep(0.01)

    assert status["state"] == "cataloged"
    assert status["media_id"]
    assert set(status) == {
        "id",
        "state",
        "display_filename",
        "declared_size_bytes",
        "received_size_bytes",
        "expires_at",
        "failure_code",
        "media_id",
    }
    assert "checksum" not in status
    assert "publication" not in str(status).lower() or "publication" not in status
    with sqlite3.connect(database_path) as connection:
        publication = connection.execute(
            """
            SELECT relative_target, cleanup_state, media_id, media_location_id
            FROM upload_publications WHERE upload_id = ?
            """,
            (upload_id,),
        ).fetchone()
        logical_count = connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()
        location_count = connection.execute(
            "SELECT COUNT(*) FROM physical_media_locations"
        ).fetchone()
    assert publication is not None
    assert publication[1] == "complete"
    assert publication[2] == status["media_id"]
    assert publication[3] is not None
    assert (published_root / publication[0]).read_bytes() == payload
    assert list(quarantine_root.iterdir()) == []
    assert logical_count == (1,)
    assert location_count == (1,)


def test_cooperative_publication_interrupt_leaves_retryable_state_without_final_media(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    data = b"\x00\x00\x00\x18ftypmp42interrupt-publication"
    upload = _pending_upload(data)
    _stage(fixture, upload, data)
    try:
        fixture.publisher.request_stop()
        with pytest.raises(UploadPublicationInfrastructureError):
            fixture.publisher.publish_owned_blocking(upload.id)
        candidate = fixture.publications.get_candidate(upload.id)
        assert candidate is not None
        assert candidate.upload.state is UploadSessionState.PUBLISH_PENDING
        final_names = [
            path.name
            for path in fixture.published_root.iterdir()
            if not path.name.endswith(".publish.tmp") and not path.name.startswith(".")
        ]
        assert final_names == []
        fresh_storage = FilesystemPublishedMediaStorage(
            DESTINATION_ID,
            fixture.published_root,
            forbidden_roots=(fixture.quarantine_root,),
        )
        retried = PublishPendingUpload(
            fixture.publications,
            fresh_storage,
            fixture.quarantine,
            now_ms=iter(range(200, 1000)).__next__,
        )
        result = retried.publish_owned_blocking(upload.id)
        restored = fixture.publications.get_candidate(upload.id)
        assert result.state == "published"
        assert restored is not None
        assert restored.upload.state is UploadSessionState.PUBLISHED
        assert list(fixture.published_root.glob("*.mp4"))
    finally:
        dispose_engine(fixture.engine)
