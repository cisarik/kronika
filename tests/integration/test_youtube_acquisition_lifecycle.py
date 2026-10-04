"""Deterministic integration evidence for YouTube acquisition lifecycle reuse."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
from pathlib import Path
import sqlite3

from sqlalchemy import insert, select, text

from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationEvidence,
)
from kronika.application.ports.youtube_downloader import (
    YouTubeDownloadError,
    YouTubeDownloadPlan,
    YouTubeDownloadResult,
    YouTubeInspection,
)
from kronika.application.upload_catalog import CatalogPublishedUpload
from kronika.application.upload_catalog_coordinator import UploadCatalogCoordinator
from kronika.application.upload_publication import PublishPendingUpload
from kronika.application.upload_publication_coordinator import (
    UploadPublicationCoordinator,
)
from kronika.application.upload_transport import (
    UploadSessionLockRegistry,
    UploadTransportLimits,
    UploadTransportService,
)
from kronika.application.upload_validation import ValidateReceivedUpload
from kronika.application.upload_validation_coordinator import (
    UploadValidationCoordinator,
)
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionCoordinator,
    YouTubeAcquisitionService,
    automatic_analysis_allowed_for_upload,
    youtube_classification_for_upload,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import LibraryId, YouTubeAcquisitionClaimId
from kronika.domain.uploads import (
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.domain.youtube_acquisition import (
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
    YouTubeConfirmationMethod,
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
    media_metadata,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.library_repository import (
    SqliteLibraryRepository,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_publication_repository import (
    SqliteUploadPublicationRepository,
)
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)
from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
    SqliteYouTubeAcquisitionClaimRepository,
)
from kronika.infrastructure.youtube.staging import (
    ARTIFACT_FILENAME,
    FilesystemYouTubeStaging,
)

DESTINATION_ID = LibraryId.from_string(
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)
VIDEO_A = "AbCdEf123_-"
VIDEO_B = "ZyXwVu987_-"
PAYLOAD = b"\x00\x00\x00\x18ftypmp42deterministic-youtube-media"


class _SyntheticValidator:
    def validate(self, _reader: object) -> UploadMediaValidationEvidence:
        return UploadMediaValidationEvidence(
            UploadValidatedMediaKind.VIDEO,
            UploadValidatedFormat.MP4,
        )


class _FakeDownloader:
    def __init__(
        self,
        staging: FilesystemYouTubeStaging,
        *,
        version: str = "2026.7.4",
        payload: bytes = PAYLOAD,
    ) -> None:
        self._staging = staging
        self.version = version
        self.payload = payload
        self.attest_calls = 0
        self.inspect_calls: list[str] = []
        self.download_calls: list[str] = []
        self.resume_seen = False
        self.fail_download = False

    async def attest_version(self) -> str:
        self.attest_calls += 1
        return self.version

    async def inspect(self, identity) -> YouTubeInspection:
        self.inspect_calls.append(identity.video_id)
        return YouTubeInspection(
            video_id=identity.video_id,
            extractor_key="Youtube",
            title="Synthetic title",
            channel="Synthetic channel",
            channel_id="synthetic-channel",
            source_date="2026-07-01",
            remote_filename="Synthetic remote.mp4",
            duration_seconds=3.0,
            downloader_version=self.version,
            extractor_version=self.version,
            plan=YouTubeDownloadPlan(
                video_format_id="18",
                audio_format_id=None,
                expected_size_bytes=len(self.payload),
                has_source_audio=True,
                split_streams=False,
            ),
        )

    async def download(
        self,
        identity,
        inspection,
        *,
        staging_key: str,
    ) -> YouTubeDownloadResult:
        assert inspection.video_id == identity.video_id
        self.download_calls.append(identity.video_id)
        claim_directory = self._staging.prepare(staging_key)
        if self.fail_download:
            (claim_directory / "artifact.mp4.part").write_bytes(b"failed-partial")
            raise YouTubeDownloadError("SYNTHETIC_DOWNLOAD_FAILED")
        partial = claim_directory / "artifact.mp4.part"
        if partial.exists():
            self.resume_seen = True
            partial.unlink()
        (claim_directory / ARTIFACT_FILENAME).write_bytes(self.payload)
        return YouTubeDownloadResult(size_bytes=len(self.payload))


class _AnalysisProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify_cataloged(self, media_id, media_location_id) -> None:
        self.calls.append((media_id.to_string(), media_location_id.to_string()))


@dataclass
class _Fixture:
    engine: object
    database_path: Path
    quarantine_root: Path
    published_root: Path
    staging_root: Path
    uploads: SqliteUploadSessionRepository
    publications: SqliteUploadPublicationRepository
    claims: SqliteYouTubeAcquisitionClaimRepository
    transport: UploadTransportService
    validation: UploadValidationCoordinator
    publication: UploadPublicationCoordinator
    catalog: UploadCatalogCoordinator
    acquisition: YouTubeAcquisitionCoordinator
    service: YouTubeAcquisitionService
    downloader: _FakeDownloader
    analysis: _AnalysisProbe

    async def close(self) -> None:
        await self.acquisition.shutdown()
        await self.validation.shutdown()
        await self.publication.shutdown()
        await self.catalog.shutdown()
        dispose_engine(self.engine)  # type: ignore[arg-type]


def _fixture(
    tmp_path: Path,
    *,
    downloader_version: str = "2026.7.4",
    acquisition_batch_size: int = 32,
) -> _Fixture:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    quarantine_root = tmp_path / "quarantine"
    published_root = tmp_path / "published"
    staging_root = tmp_path / "youtube"
    preview_root = tmp_path / "preview"
    for root in (quarantine_root, published_root, staging_root):
        root.mkdir(parents=True)
    staging_root.chmod(0o700)
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
    upload_repository = SqliteUploadSessionRepository(engine)
    publication_repository = SqliteUploadPublicationRepository(engine)
    claim_repository = SqliteYouTubeAcquisitionClaimRepository(engine)
    library_repository = SqliteLibraryRepository(engine)
    quarantine = FilesystemQuarantineStorage(quarantine_root)
    staging = FilesystemYouTubeStaging(
        staging_root,
        forbidden_roots=(
            quarantine_root,
            published_root,
            preview_root,
            database_path.parent,
        ),
    )
    published = FilesystemPublishedMediaStorage(
        DESTINATION_ID,
        published_root,
        forbidden_roots=(
            quarantine_root,
            staging_root,
            preview_root,
            database_path.parent,
        ),
    )
    ticks = itertools.count(100)
    now_ms = lambda: next(ticks)
    locks = UploadSessionLockRegistry()
    transport = UploadTransportService(
        upload_repository,
        quarantine,
        library_repository,
        UploadTransportLimits(
            max_total_bytes=1_073_741_824,
            max_patch_bytes=8,
            session_ttl_seconds=86_400,
            min_free_space_reserve_bytes=0,
        ),
        quarantine_root=quarantine_root,
        preview_cache_root=preview_root,
        now_ms=now_ms,
        locks=locks,
    )
    validator = ValidateReceivedUpload(
        upload_repository,
        quarantine,
        _SyntheticValidator(),
        now_ms=now_ms,
        locks=locks,
    )
    analysis = _AnalysisProbe()
    cataloger = CatalogPublishedUpload(
        publication_repository,
        classification_for_upload=lambda upload_id: (
            youtube_classification_for_upload(claim_repository, upload_id)
        ),
        now_ms=now_ms,
    )
    catalog = UploadCatalogCoordinator(
        publication_repository,
        cataloger,
        locks,
        analysis_notifier=analysis,
        analysis_allowed_for_upload=lambda upload_id: (
            automatic_analysis_allowed_for_upload(claim_repository, upload_id)
        ),
        retry_initial_delay_seconds=0,
        retry_max_delay_seconds=0,
    )
    publisher = PublishPendingUpload(
        publication_repository,
        published,
        quarantine,
        now_ms=now_ms,
    )
    publication = UploadPublicationCoordinator(
        publication_repository,
        publisher,
        locks,
        catalog_coordinator=catalog,
        retry_initial_delay_seconds=0,
        retry_max_delay_seconds=0,
    )
    validation = UploadValidationCoordinator(
        upload_repository,
        validator,
        locks,
        publication_coordinator=publication,
        discovery_retry_initial_delay_seconds=0,
        discovery_retry_max_delay_seconds=0,
    )
    downloader = _FakeDownloader(
        staging,
        version=downloader_version,
    )
    acquisition = YouTubeAcquisitionCoordinator(
        claim_repository,
        downloader,
        staging,
        transport,
        upload_repository,
        publication_repository,
        validation_coordinator=validation,
        publication_coordinator=publication,
        chunk_size_bytes=8,
        now_ms=now_ms,
        poll_interval_seconds=0,
        batch_size=acquisition_batch_size,
    )
    service = YouTubeAcquisitionService(
        claim_repository,
        upload_repository,
        staging,
        now_ms=now_ms,
        notifier=acquisition,
    )
    return _Fixture(
        engine=engine,
        database_path=database_path,
        quarantine_root=quarantine_root,
        published_root=published_root,
        staging_root=staging_root,
        uploads=upload_repository,
        publications=publication_repository,
        claims=claim_repository,
        transport=transport,
        validation=validation,
        publication=publication,
        catalog=catalog,
        acquisition=acquisition,
        service=service,
        downloader=downloader,
        analysis=analysis,
    )


async def _send_one(transport: UploadTransportService, payload: bytes):
    session = transport.create_session(
        display_filename="manual.mp4",
        declared_size_bytes=len(payload),
    )
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + 8]

        async def body():
            yield chunk

        session = await transport.receive_chunk(
            UploadSessionId.from_string(session.id),
            upload_offset=offset,
            content_length=len(chunk),
            body=body(),
        )
        offset = session.received_size_bytes
    return await transport.complete(UploadSessionId.from_string(session.id))


def _counts(database_path: Path) -> tuple[int, int, int]:
    connection = sqlite3.connect(database_path)
    try:
        return (
            int(connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()[0]),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM physical_media_locations"
                ).fetchone()[0]
            ),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM youtube_acquisition_claims"
                ).fetchone()[0]
            ),
        )
    finally:
        connection.close()


def test_new_video_reuses_existing_pipeline_and_suppresses_automatic_analysis(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        submitted = fixture.service.submit(
            submitted_url=f"https://youtu.be/{VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        )
        claim_id = submitted.snapshot.id
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        await fixture.validation.drain()
        await fixture.publication.drain()
        await fixture.catalog.drain()
        await fixture.acquisition.drain()
        current = fixture.claims.get(
            fixture.claims.find_by_upload_id(
                UploadSessionId.from_string(claim_id)
            ).id  # type: ignore[union-attr]
        )

        assert current is not None
        assert current.state is YouTubeAcquisitionState.CATALOGED
        assert current.media_id is not None
        assert current.media_location_id is not None
        assert current.cleanup_state.value == "complete"
        assert not (fixture.staging_root / current.staging_key).exists()
        assert fixture.downloader.inspect_calls == [VIDEO_A]
        assert fixture.downloader.download_calls == [VIDEO_A]
        assert fixture.analysis.calls == []
        assert _counts(fixture.database_path) == (1, 1, 1)
        with fixture.engine.connect() as connection:  # type: ignore[union-attr]
            metadata = connection.execute(
                select(
                    media_metadata.c.content_category,
                    media_metadata.c.acquisition_source,
                    media_metadata.c.display_title,
                ).where(
                    media_metadata.c.media_id == current.media_id.to_string()
                )
            ).one()
        assert metadata == ("youtube", "youtube_manual_claim", "Synthetic title")
        await fixture.close()

    asyncio.run(scenario())


def test_source_identity_and_manual_byte_duplicates_reuse_one_logical_medium(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        manual = await _send_one(fixture.transport, PAYLOAD)
        await fixture.validation.drain()
        await fixture.publication.drain()
        await fixture.catalog.drain()
        manual_candidate = fixture.publications.get_candidate(
            UploadSessionId.from_string(manual.id)
        )
        assert manual_candidate is not None
        assert manual_candidate.publication is not None
        assert manual_candidate.publication.media_id is not None
        with fixture.engine.begin() as connection:  # type: ignore[union-attr]
            connection.execute(
                insert(media_metadata).values(
                    media_id=manual_candidate.publication.media_id.to_string(),
                    display_title="Owner title",
                    description=None,
                    content_category="meme",
                    acquisition_source="manual_upload",
                    collection_key=None,
                    processed_at_ms=None,
                    created_at_ms=500,
                    updated_at_ms=500,
                )
            )

        submitted = fixture.service.submit(
            submitted_url=f"https://www.youtube.com/watch?v={VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        )
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        await fixture.validation.drain()
        await fixture.acquisition.drain()
        duplicate = fixture.claims.get(
            fixture.claims.find_by_upload_id(
                UploadSessionId.from_string(submitted.snapshot.id)
            ).id  # type: ignore[union-attr]
        )
        assert duplicate is not None
        assert duplicate.state is YouTubeAcquisitionState.DUPLICATE_RESOLVED
        assert duplicate.media_id == manual_candidate.publication.media_id
        assert fixture.uploads.get(
            UploadSessionId.from_string(submitted.snapshot.id)
        ).state is UploadSessionState.CANCELLED  # type: ignore[union-attr]
        assert _counts(fixture.database_path) == (1, 1, 1)
        with fixture.engine.connect() as connection:  # type: ignore[union-attr]
            metadata = connection.execute(
                select(
                    media_metadata.c.display_title,
                    media_metadata.c.content_category,
                    media_metadata.c.acquisition_source,
                )
            ).one()
        assert metadata == ("Owner title", "meme", "manual_upload")

        repeated = fixture.service.submit(
            submitted_url=f"https://m.youtube.com/shorts/{VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        )
        assert repeated.snapshot.state == "duplicate_resolved"
        assert repeated.snapshot.media_id == duplicate.media_id.to_string()
        assert fixture.downloader.inspect_calls == [VIDEO_A]
        assert fixture.downloader.download_calls == [VIDEO_A]
        assert _counts(fixture.database_path) == (1, 1, 2)
        await fixture.close()

    asyncio.run(scenario())


def test_two_youtube_ids_with_identical_bytes_preserve_two_claims(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        first = fixture.service.submit(
            submitted_url=f"https://youtu.be/{VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        )
        for _ in range(4):
            await fixture.acquisition.drain()
        await fixture.validation.drain()
        await fixture.publication.drain()
        await fixture.catalog.drain()
        await fixture.acquisition.drain()

        second = fixture.service.submit(
            submitted_url=f"https://youtu.be/{VIDEO_B}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        )
        for _ in range(4):
            await fixture.acquisition.drain()
        await fixture.validation.drain()
        await fixture.acquisition.drain()

        first_claim = fixture.claims.find_by_upload_id(
            UploadSessionId.from_string(first.snapshot.id)
        )
        second_claim = fixture.claims.find_by_upload_id(
            UploadSessionId.from_string(second.snapshot.id)
        )
        assert first_claim is not None
        assert second_claim is not None
        assert first_claim.state is YouTubeAcquisitionState.CATALOGED
        assert second_claim.state is YouTubeAcquisitionState.DUPLICATE_RESOLVED
        assert first_claim.media_id == second_claim.media_id
        assert _counts(fixture.database_path) == (1, 1, 2)
        assert fixture.downloader.download_calls == [VIDEO_A, VIDEO_B]
        await fixture.close()

    asyncio.run(scenario())


def test_same_version_partial_download_and_handoff_offset_recover(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        claim = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_B}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
            now_ms=10,
        )
        inspecting = claim.advance(
            YouTubeAcquisitionState.INSPECTING,
            updated_at_ms=11,
        )
        pending = inspecting.advance(
            YouTubeAcquisitionState.DOWNLOAD_PENDING,
            updated_at_ms=12,
            downloader_name="yt-dlp",
            downloader_version=fixture.downloader.version,
            extractor_version=fixture.downloader.version,
            selected_video_format_id="18",
        )
        downloading = pending.advance(
            YouTubeAcquisitionState.DOWNLOADING,
            updated_at_ms=13,
        )
        fixture.claims.create(downloading)
        claim_directory = (
            FilesystemYouTubeStaging(fixture.staging_root).prepare(
                downloading.staging_key
            )
        )
        (claim_directory / "artifact.mp4.part").write_bytes(b"partial")

        await fixture.acquisition.drain()
        current = fixture.claims.get(claim.id)
        assert current is not None
        assert current.state is YouTubeAcquisitionState.DOWNLOADED
        assert fixture.downloader.resume_seen is True
        await fixture.acquisition.drain()
        handoff = fixture.claims.get(claim.id)
        assert handoff is not None
        assert handoff.state is YouTubeAcquisitionState.HANDOFF

        upload_id = UploadSessionId.from_string(claim.id.to_string())
        snapshot = fixture.transport.create_session(
            display_filename=handoff.generated_filename,
            declared_size_bytes=len(PAYLOAD),
            session_id=upload_id,
            storage_key=UploadStorageKey(handoff.staging_key),
        )
        first = PAYLOAD[:8]

        async def first_body():
            yield first

        snapshot = await fixture.transport.receive_chunk(
            upload_id,
            upload_offset=0,
            content_length=len(first),
            body=first_body(),
        )
        linked = handoff.evolve(
            updated_at_ms=20,
            upload_id=upload_id,
        )
        fixture.claims.save(
            linked,
            expected_state=handoff.state,
            expected_version=handoff.version,
        )
        assert snapshot.received_size_bytes == 8

        await fixture.acquisition.drain()
        recovered = fixture.claims.get(claim.id)
        assert recovered is not None
        assert recovered.state is YouTubeAcquisitionState.HANDED_OFF
        upload = fixture.uploads.get(upload_id)
        assert upload is not None
        assert upload.received_size_bytes == len(PAYLOAD)
        assert upload.state is UploadSessionState.RECEIVED
        await fixture.close()

    asyncio.run(scenario())


def test_handoff_recovers_after_linked_upload_already_reached_catalog(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        submitted = fixture.service.submit(
            submitted_url=f"https://youtu.be/{VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        )
        for _ in range(3):
            await fixture.acquisition.drain()
        claim_id = YouTubeAcquisitionClaimId.from_string(submitted.snapshot.id)
        handoff = fixture.claims.get(claim_id)
        assert handoff is not None
        assert handoff.state is YouTubeAcquisitionState.HANDOFF
        assert handoff.downloaded_size_bytes == len(PAYLOAD)

        upload_id = UploadSessionId.from_string(claim_id.to_string())
        snapshot = fixture.transport.create_session(
            display_filename=handoff.generated_filename,
            declared_size_bytes=len(PAYLOAD),
            session_id=upload_id,
            storage_key=UploadStorageKey(handoff.staging_key),
        )
        offset = 0
        while offset < len(PAYLOAD):
            chunk = PAYLOAD[offset : offset + 8]

            async def body():
                yield chunk

            snapshot = await fixture.transport.receive_chunk(
                upload_id,
                upload_offset=offset,
                content_length=len(chunk),
                body=body(),
            )
            offset = snapshot.received_size_bytes
        await fixture.transport.complete(upload_id)
        linked = handoff.evolve(
            updated_at_ms=handoff.updated_at_ms + 1,
            upload_id=upload_id,
        )
        fixture.claims.save(
            linked,
            expected_state=handoff.state,
            expected_version=handoff.version,
        )

        await fixture.validation.drain()
        await fixture.publication.drain()
        await fixture.catalog.drain()
        upload = fixture.uploads.get(upload_id)
        assert upload is not None
        assert upload.state is UploadSessionState.CATALOGED

        await fixture.acquisition.drain()
        recovered = fixture.claims.get(claim_id)
        assert recovered is not None
        assert recovered.state is YouTubeAcquisitionState.HANDED_OFF
        assert recovered.cleanup_state.value == "complete"
        await fixture.acquisition.drain()
        completed = fixture.claims.get(claim_id)
        assert completed is not None
        assert completed.state is YouTubeAcquisitionState.CATALOGED
        assert _counts(fixture.database_path) == (1, 1, 1)
        await fixture.close()

    asyncio.run(scenario())


def test_changed_downloader_version_cleans_partial_and_reinspects(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, downloader_version="2026.7.4")

    async def scenario() -> None:
        claim = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_B}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
            now_ms=10,
        )
        inspecting = claim.advance(
            YouTubeAcquisitionState.INSPECTING,
            updated_at_ms=11,
        )
        pending = inspecting.advance(
            YouTubeAcquisitionState.DOWNLOAD_PENDING,
            updated_at_ms=12,
            downloader_name="yt-dlp",
            downloader_version="2026.6.1",
            extractor_version="2026.6.1",
            selected_video_format_id="18",
        )
        fixture.claims.create(pending)
        claim_directory = FilesystemYouTubeStaging(
            fixture.staging_root
        ).prepare(pending.staging_key)
        partial = claim_directory / "artifact.mp4.part"
        partial.write_bytes(b"old-version-partial")

        await fixture.acquisition.drain()

        current = fixture.claims.get(claim.id)
        assert current is not None
        assert current.state is YouTubeAcquisitionState.DOWNLOAD_PENDING
        assert current.downloader_version == "2026.7.4"
        assert fixture.downloader.inspect_calls == [VIDEO_B]
        assert not partial.exists()
        await fixture.close()

    asyncio.run(scenario())


def test_startup_reconciliation_pages_through_all_active_claims(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        downloader_version="2026.7.4",
        acquisition_batch_size=1,
    )

    async def scenario() -> None:
        pending_claims: list[YouTubeAcquisitionClaim] = []
        partial_paths: list[Path] = []
        for index, video_id in enumerate(
            ("PageTest00_", "PageTest01_", "PageTest02_")
        ):
            claim = YouTubeAcquisitionClaim.new(
                submitted_url=f"https://youtu.be/{video_id}",
                confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
                now_ms=10 + index,
            )
            inspecting = claim.advance(
                YouTubeAcquisitionState.INSPECTING,
                updated_at_ms=20 + index,
            )
            pending = inspecting.advance(
                YouTubeAcquisitionState.DOWNLOAD_PENDING,
                updated_at_ms=30 + index,
                downloader_name="yt-dlp",
                downloader_version="2026.6.1",
                extractor_version="2026.6.1",
                selected_video_format_id="18",
            )
            fixture.claims.create(pending)
            claim_directory = FilesystemYouTubeStaging(
                fixture.staging_root
            ).prepare(pending.staging_key)
            partial = claim_directory / "artifact.mp4.part"
            partial.write_bytes(b"old-version-partial")
            pending_claims.append(pending)
            partial_paths.append(partial)

        await fixture.acquisition.drain()

        assert fixture.downloader.attest_calls == 1
        assert all(not partial.exists() for partial in partial_paths)
        for pending in pending_claims:
            current = fixture.claims.get(pending.id)
            assert current is not None
            assert current.downloader_version in {None, "2026.7.4"}
            assert current.downloader_version != "2026.6.1"
        await fixture.close()

    asyncio.run(scenario())


def test_terminal_downloader_failure_cleans_staging_and_retry_uses_new_lineage(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        fixture.downloader.fail_download = True
        first = fixture.service.submit(
            submitted_url=f"https://youtu.be/{VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        )
        await fixture.acquisition.drain()
        await fixture.acquisition.drain()
        failed = fixture.claims.get(
            YouTubeAcquisitionClaimId.from_string(first.snapshot.id)
        )
        # The failure is terminal, so source-active lookup no longer resolves it.
        assert fixture.claims.find_active_by_source_identity(
            extractor_key="Youtube",
            youtube_video_id=VIDEO_A,
        ) is None
        assert failed is not None
        assert failed.state is YouTubeAcquisitionState.FAILED
        assert failed.failure_code == "SYNTHETIC_DOWNLOAD_FAILED"
        assert failed.cleanup_state.value == "complete"
        assert not (fixture.staging_root / failed.staging_key).exists()

        fixture.downloader.fail_download = False
        retry = fixture.service.retry(
            failed.id,
            confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        )
        retried = fixture.claims.get(
            YouTubeAcquisitionClaimId.from_string(retry.snapshot.id)
        )
        assert retried is not None
        assert retried.id != failed.id
        assert retried.retry_of_claim_id == failed.id
        assert retried.state is YouTubeAcquisitionState.CLAIMED
        await fixture.close()

    asyncio.run(scenario())


def test_unresolved_byte_duplicate_fails_youtube_claim_and_cleans_staging(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    async def scenario() -> None:
        manual = await _send_one(fixture.transport, PAYLOAD)
        manual_id = UploadSessionId.from_string(manual.id)
        await fixture.validation.drain()
        await fixture.publication.drain()

        submitted = fixture.service.submit(
            submitted_url=f"https://youtu.be/{VIDEO_A}",
            confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        )
        for _ in range(4):
            await fixture.acquisition.drain()
        await fixture.validation.drain()
        duplicate_id = UploadSessionId.from_string(submitted.snapshot.id)
        duplicate = fixture.uploads.get(duplicate_id)
        assert duplicate is not None
        assert duplicate.state is UploadSessionState.DUPLICATE_PENDING

        # Simulate a downstream loss of the canonical publication before the
        # YouTube reconciliation pass. No cataloged canonical remains.
        with fixture.engine.begin() as connection:  # type: ignore[union-attr]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET state = 'failed', "
                    "failure_code = 'SYNTHETIC_CANONICAL_LOST' "
                    "WHERE id = :upload_id"
                ),
                {"upload_id": manual.id},
            )

        await fixture.acquisition.drain()
        failed = fixture.claims.get(
            YouTubeAcquisitionClaimId.from_string(submitted.snapshot.id)
        )
        assert failed is not None
        assert failed.state is YouTubeAcquisitionState.FAILED
        assert failed.failure_stage.value == "downstream"
        assert failed.failure_code == "BYTE_DUPLICATE_UNRESOLVED"
        assert failed.cleanup_state.value == "complete"
        assert not (fixture.staging_root / failed.staging_key).exists()
        assert fixture.uploads.get(manual_id).state is UploadSessionState.FAILED  # type: ignore[union-attr]
        await fixture.close()

    asyncio.run(scenario())
