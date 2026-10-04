"""Persistence tests for specialized published-to-cataloged transactions."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, insert, select, text, update

from kronika.application.ports.upload_publications import (
    UploadCatalogInconsistencyError,
    UploadCatalogStateConflictError,
)
from kronika.application.upload_catalog import (
    CatalogPublishedUpload,
    CatalogUploadClassification,
    UploadCatalogInfrastructureError,
)
from kronika.domain.identities import LibraryId, MediaByteIdentityId, MediaId, MediaLocationId
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    MovieGenre,
)
from kronika.domain.media_metadata import (
    CanonicalTagKey,
    MediaDescription,
    MediaDisplayTitle,
    MediaMetadata,
)
from kronika.domain.upload_publications import (
    UploadPublicationCleanupState,
    UploadPublicationState,
    new_upload_publication_reservation,
)
from kronika.domain.uploads import (
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.infrastructure.persistence.catalog_schema import (
    canonical_tags,
    devices,
    libraries,
    logical_media,
    media_byte_identities,
    media_canonical_tags,
    media_content_publications,
    media_metadata,
    metadata,
    physical_media_locations,
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
        connection.execute(
            insert(media_byte_identities).values(
                id=IDENTITY_ID.to_string(),
                checksum_algorithm="sha256",
                size_bytes=8,
                checksum_hex="a" * 64,
                created_at_ms=10,
            )
        )
    return engine


def _upload(
    upload_id: str = "11111111-1111-4111-8111-111111111111",
    *,
    state: UploadSessionState = UploadSessionState.PUBLISH_PENDING,
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
        duplicate_disposition=None,
        created_at_ms=10,
        updated_at_ms=20,
        expires_at_ms=100,
        failure_code=None,
        version=4,
    )


def _published_complete(engine, uploads, publications, *, upload_id=None):
    upload = _upload() if upload_id is None else _upload(upload_id)
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
    published = publications.commit_verified_publication(
        upload.id,
        publication_id=reserved.publication.publication_id,
        expected_upload_version=upload.version,
        expected_publication_version=reserved.publication.version,
        updated_at_ms=30,
    )
    assert published.publication is not None
    completed = publications.mark_cleanup_complete(
        upload.id,
        publication_id=published.publication.publication_id,
        expected_publication_version=published.publication.version,
        updated_at_ms=31,
    )
    return completed


def _counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        media_count = connection.execute(
            select(func.count()).select_from(logical_media)
        ).scalar_one()
        location_count = connection.execute(
            select(func.count()).select_from(physical_media_locations)
        ).scalar_one()
    return int(media_count), int(location_count)


def _seed_canonical_tag(engine, key: str = "meme", display_name: str = "Meme") -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(canonical_tags).values(
                key=key,
                display_name=display_name,
                created_at_ms=1,
                updated_at_ms=1,
            )
        )


def _x_seed_classification(
    *,
    title: str = "Seeded title",
    description: str | None = "Seeded description",
    tag_keys: tuple[str, ...] = ("meme",),
) -> CatalogUploadClassification:
    return CatalogUploadClassification(
        content_category=ContentCategory.GENERAL,
        acquisition_source=AcquisitionSource.X_MANUAL_CLAIM,
        display_title=MediaDisplayTitle(title),
        description=None if description is None else MediaDescription(description),
        tag_keys=tuple(CanonicalTagKey(key) for key in tag_keys),
    )


def _metadata_row(engine, media_id: str):
    with engine.connect() as connection:
        return connection.execute(
            select(media_metadata).where(media_metadata.c.media_id == media_id)
        ).mappings().one()


def _ordered_tags(engine, media_id: str) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(media_canonical_tags.c.tag_key)
            .where(media_canonical_tags.c.media_id == media_id)
            .order_by(media_canonical_tags.c.position)
        ).all()
    return [str(row[0]) for row in rows]


def _publication_count(engine, media_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                select(func.count())
                .select_from(media_content_publications)
                .where(media_content_publications.c.media_id == media_id)
            ).scalar_one()
        )


def test_published_upload_creates_no_catalog_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    try:
        completed = _published_complete(engine, uploads, publications)
        assert completed.upload.state is UploadSessionState.PUBLISHED
        assert completed.publication is not None
        assert completed.publication.media_id is None
        assert completed.publication.media_location_id is None
        assert _counts(engine) == (0, 0)
        assert publications.list_catalog_candidates(limit=10)
    finally:
        dispose_engine(engine)


def test_successful_catalog_commit_creates_one_media_and_location(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    cataloger = CatalogPublishedUpload(publications, now_ms=lambda: 40)
    published_root = tmp_path / "published"
    published_root.mkdir()
    try:
        completed = _published_complete(engine, uploads, publications)
        assert completed.publication is not None
        target = published_root / completed.publication.relative_path.value
        target.write_bytes(b"12345678")
        result = cataloger.catalog_owned_blocking(completed.upload.id)
        assert result.state == "cataloged"
        assert result.media_id is not None
        current = publications.get_candidate(completed.upload.id)
        assert current is not None
        assert current.upload.state is UploadSessionState.CATALOGED
        assert current.publication is not None
        assert current.publication.media_id is not None
        assert current.publication.media_location_id is not None
        assert current.publication.media_id.to_string() == result.media_id
        assert _counts(engine) == (1, 1)
        assert target.is_file()
        assert target.read_bytes() == b"12345678"
    finally:
        dispose_engine(engine)


def test_catalog_commit_is_idempotent_without_duplicates(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    cataloger = CatalogPublishedUpload(publications, now_ms=lambda: 40)
    try:
        completed = _published_complete(engine, uploads, publications)
        first = cataloger.catalog_owned_blocking(completed.upload.id)
        second = cataloger.catalog_owned_blocking(completed.upload.id)
        assert first.media_id == second.media_id
        assert first.state == second.state == "cataloged"
        assert _counts(engine) == (1, 1)
    finally:
        dispose_engine(engine)


def test_partial_and_contradictory_linkage_fail_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    try:
        completed = _published_complete(engine, uploads, publications)
        assert completed.publication is not None
        with engine.begin() as connection:
            with pytest.raises(Exception):
                connection.execute(
                    text(
                        """
                        UPDATE upload_publications
                        SET media_id = ?
                        WHERE upload_id = ?
                        """
                    ),
                    (
                        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                        completed.upload.id.to_string(),
                    ),
                )
        cataloged = CatalogPublishedUpload(publications, now_ms=lambda: 40)
        cataloged.catalog_owned_blocking(completed.upload.id)
        linked = publications.get_candidate(completed.upload.id)
        assert linked is not None and linked.publication is not None
        with engine.begin() as connection:
            connection.execute(
                update(upload_sessions)
                .where(upload_sessions.c.id == completed.upload.id.to_string())
                .values(state=UploadSessionState.PUBLISHED.value)
            )
        media = LogicalMedia(
            id=MediaId.new(),
            kind=MediaKind.VIDEO,
            created_at_ms=50,
            updated_at_ms=50,
        )
        with pytest.raises(UploadCatalogInconsistencyError):
            publications.commit_cataloged_publication(
                completed.upload.id,
                media=media,
                location=MediaLocation(
                    id=MediaLocationId.new(),
                    media_id=media.id,
                    library_id=DESTINATION_ID,
                    relative_path=MediaRelativePath(
                        linked.publication.relative_path.value
                    ),
                    availability=MediaLocationAvailability.AVAILABLE,
                    observed_size_bytes=8,
                    observed_mtime_ns=None,
                    created_at_ms=50,
                    updated_at_ms=50,
                ),
                expected_upload_version=linked.upload.version,
                expected_publication_version=linked.publication.version,
                updated_at_ms=50,
            )
    finally:
        dispose_engine(engine)


def test_catalog_db_failure_rolls_back_and_preserves_published_file(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    published_root = tmp_path / "published"
    published_root.mkdir()
    try:
        completed = _published_complete(engine, uploads, publications)
        assert completed.publication is not None
        target = published_root / completed.publication.relative_path.value
        target.write_bytes(b"preserved")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TRIGGER reject_synthetic_catalog_media
                    BEFORE INSERT ON logical_media
                    BEGIN
                        SELECT RAISE(ABORT, 'synthetic catalog rollback');
                    END
                    """
                )
            )
        with pytest.raises(UploadCatalogInfrastructureError):
            CatalogPublishedUpload(publications, now_ms=lambda: 40).catalog_owned_blocking(
                completed.upload.id
            )
        current = publications.get_candidate(completed.upload.id)
        assert current is not None
        assert current.upload.state is UploadSessionState.PUBLISHED
        assert current.publication is not None
        assert current.publication.media_id is None
        assert current.publication.media_location_id is None
        assert _counts(engine) == (0, 0)
        assert target.is_file()
        assert target.read_bytes() == b"preserved"
    finally:
        dispose_engine(engine)


def test_catalog_commit_persists_title_description_and_ordered_tags(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    _seed_canonical_tag(engine, "alpha", "Alpha")
    _seed_canonical_tag(engine, "beta", "Beta")
    classification = _x_seed_classification(
        title="Alt title",
        description="Full tweet body",
        tag_keys=("alpha", "beta"),
    )
    cataloger = CatalogPublishedUpload(
        publications,
        now_ms=lambda: 40,
        classification_for_upload=lambda _upload_id: classification,
    )
    try:
        completed = _published_complete(engine, uploads, publications)
        result = cataloger.catalog_owned_blocking(completed.upload.id)
        assert result.media_id is not None
        row = _metadata_row(engine, result.media_id)
        assert row["display_title"] == "Alt title"
        assert row["description"] == "Full tweet body"
        assert row["collection_key"] == "processed"
        assert row["processed_at_ms"] == 40
        assert _ordered_tags(engine, result.media_id) == ["alpha", "beta"]
        assert _publication_count(engine, result.media_id) == 0
    finally:
        dispose_engine(engine)


def test_catalog_commit_empty_tags_leave_collection_null(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    classification = _x_seed_classification(
        title="Fallback title",
        description=None,
        tag_keys=(),
    )
    cataloger = CatalogPublishedUpload(
        publications,
        now_ms=lambda: 40,
        classification_for_upload=lambda _upload_id: classification,
    )
    try:
        completed = _published_complete(engine, uploads, publications)
        result = cataloger.catalog_owned_blocking(completed.upload.id)
        assert result.media_id is not None
        row = _metadata_row(engine, result.media_id)
        assert row["display_title"] == "Fallback title"
        assert row["description"] is None
        assert row["collection_key"] is None
        assert row["processed_at_ms"] is None
        assert _ordered_tags(engine, result.media_id) == []
    finally:
        dispose_engine(engine)


def test_catalog_commit_with_tags_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    _seed_canonical_tag(engine)
    classification = _x_seed_classification()
    cataloger = CatalogPublishedUpload(
        publications,
        now_ms=lambda: 40,
        classification_for_upload=lambda _upload_id: classification,
    )
    try:
        completed = _published_complete(engine, uploads, publications)
        first = cataloger.catalog_owned_blocking(completed.upload.id)
        second = cataloger.catalog_owned_blocking(completed.upload.id)
        assert first.media_id == second.media_id
        assert _counts(engine) == (1, 1)
        assert _ordered_tags(engine, first.media_id) == ["meme"]
    finally:
        dispose_engine(engine)


def test_unknown_tag_rolls_back_catalog_without_partial_linkage(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    classification = _x_seed_classification(tag_keys=("missing-tag",))
    cataloger = CatalogPublishedUpload(
        publications,
        now_ms=lambda: 40,
        classification_for_upload=lambda _upload_id: classification,
    )
    try:
        completed = _published_complete(engine, uploads, publications)
        with pytest.raises(UploadCatalogInfrastructureError):
            cataloger.catalog_owned_blocking(completed.upload.id)
        current = publications.get_candidate(completed.upload.id)
        assert current is not None
        assert current.upload.state is UploadSessionState.PUBLISHED
        assert current.publication is not None
        assert current.publication.media_id is None
        assert current.publication.media_location_id is None
        assert _counts(engine) == (0, 0)
        with engine.connect() as connection:
            metadata_count = connection.execute(
                select(func.count()).select_from(media_metadata)
            ).scalar_one()
            tag_count = connection.execute(
                select(func.count()).select_from(media_canonical_tags)
            ).scalar_one()
        assert int(metadata_count) == 0
        assert int(tag_count) == 0
    finally:
        dispose_engine(engine)


def test_catalog_commit_still_rejects_genres(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    try:
        completed = _published_complete(engine, uploads, publications)
        media = LogicalMedia(
            id=MediaId.new(),
            kind=MediaKind.VIDEO,
            created_at_ms=50,
            updated_at_ms=50,
        )
        with pytest.raises(UploadCatalogStateConflictError):
            publications.commit_cataloged_publication(
                completed.upload.id,
                media=media,
                location=MediaLocation(
                    id=MediaLocationId.new(),
                    media_id=media.id,
                    library_id=DESTINATION_ID,
                    relative_path=MediaRelativePath(
                        completed.publication.relative_path.value
                    ),
                    availability=MediaLocationAvailability.AVAILABLE,
                    observed_size_bytes=8,
                    observed_mtime_ns=None,
                    created_at_ms=50,
                    updated_at_ms=50,
                ),
                expected_upload_version=completed.upload.version,
                expected_publication_version=completed.publication.version,
                updated_at_ms=50,
                metadata=MediaMetadata(
                    media_id=media.id,
                    display_title=MediaDisplayTitle("Genre seed"),
                    description=None,
                    tag_keys=(),
                    created_at_ms=50,
                    updated_at_ms=50,
                    content_category=ContentCategory.MOVIE,
                    acquisition_source=AcquisitionSource.X_MANUAL_CLAIM,
                    genre_keys=(MovieGenre.ACTION,),
                ),
            )
        assert _counts(engine) == (0, 0)
    finally:
        dispose_engine(engine)
