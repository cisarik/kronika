"""Integration evidence for administrator catalog-removal persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text

from kronika.application.catalog_removal import (
    CatalogMediaRemovalService,
    CatalogRemovalNotFoundError,
    CatalogRemovalStateConflictError,
    CleanupState,
)
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.catalog_removal_repository import (
    SqliteCatalogRemovalRepository,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
OTHER_MEDIA_ID = "22222222-2222-4222-8222-222222222222"
LOCATION_ID = "33333333-3333-4333-8333-333333333333"
OTHER_LOCATION_ID = "44444444-4444-4444-8444-444444444444"
DEVICE_ID = "55555555-5555-4555-8555-555555555555"
LIBRARY_ID = "66666666-6666-4666-8666-666666666666"
BYTE_ID = "77777777-7777-4777-8777-777777777777"
UPLOAD_ID = "88888888-8888-4888-8888-888888888888"
CLAIM_ID = "99999999-9999-4999-8999-999999999999"
RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SUPERSEDED_RUN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PUBLICATION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
DIGEST = "a" * 64
VIDEO_ID = "AbCdEf123_-"


class _RecordingCleanup:
    def __init__(self) -> None:
        self.cover_calls: list[dict[str, object]] = []
        self.preview_calls: list[dict[str, object]] = []
        self.cover_result: CleanupState = "complete"
        self.preview_result: CleanupState = "complete"
        self.fail_next_cover = False

    def cleanup_cover(
        self, *, media_id: str, artifact_digest: str | None
    ) -> CleanupState:
        self.cover_calls.append(
            {"media_id": media_id, "artifact_digest": artifact_digest}
        )
        if self.fail_next_cover:
            self.fail_next_cover = False
            return "failed"
        return self.cover_result if artifact_digest is not None else "none"

    def cleanup_previews(self, *, location_ids_json: str | None) -> CleanupState:
        self.preview_calls.append({"location_ids_json": location_ids_json})
        return self.preview_result if location_ids_json is not None else "none"


def _engine(tmp_path: Path) -> sa.Engine:
    database_path = tmp_path / "catalog-removal.sqlite3"
    upgrade_database_to_head(
        KronikaSettings(database_path=database_path, _env_file=None)
    )
    return create_sqlite_engine(database_path)


def _seed_base(connection: sa.Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO devices (id, display_name) VALUES (:id, 'Synthetic device')"
        ),
        {"id": DEVICE_ID},
    )
    connection.execute(
        text(
            "INSERT INTO libraries "
            "(id, device_id, display_name, path_flavor, root_path) "
            "VALUES (:id, :device_id, 'Synthetic library', 'posix', '/synthetic/media')"
        ),
        {"id": LIBRARY_ID, "device_id": DEVICE_ID},
    )
    connection.execute(
        text(
            "INSERT INTO media_byte_identities "
            "(id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms) "
            "VALUES (:id, 'sha256', 8, :digest, 10)"
        ),
        {"id": BYTE_ID, "digest": DIGEST},
    )


def _seed_media(
    connection: sa.Connection,
    *,
    media_id: str,
    location_id: str,
    title: str,
    published: bool = True,
    with_cover: bool = True,
    with_upload: bool = True,
    with_youtube: bool = True,
    with_analysis: bool = True,
) -> None:
    connection.execute(
        text(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (:id, 'video', 20, 20)"
        ),
        {"id": media_id},
    )
    connection.execute(
        text(
            "INSERT INTO media_metadata "
            "(media_id, display_title, description, content_category, "
            "acquisition_source, collection_key, processed_at_ms, "
            "created_at_ms, updated_at_ms) VALUES "
            "(:id, :title, 'Synthetic description', 'general', "
            "'manual_upload', NULL, NULL, 20, 20)"
        ),
        {"id": media_id, "title": title},
    )
    connection.execute(
        text(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (:id, :media_id, :library_id, :path, 'available', 8, 30, 20, 20)"
        ),
        {
            "id": location_id,
            "media_id": media_id,
            "library_id": LIBRARY_ID,
            "path": f"safe/{media_id}.mp4",
        },
    )
    if published:
        connection.execute(
            text(
                "INSERT INTO media_content_publications "
                "(media_id, published_at_ms, publication_origin) "
                "VALUES (:id, 40, 'admin_explicit')"
            ),
            {"id": media_id},
        )
    if with_cover:
        connection.execute(
            text(
                "INSERT INTO media_covers "
                "(media_id, source_location_id, source_reference, source_kind, "
                "source_timestamp_ms, source_size_bytes, source_mtime_ns, "
                "source_duration_ms, source_observation_version, "
                "source_observation_digest, artifact_profile, artifact_media_type, "
                "artifact_digest, artifact_width, artifact_height, "
                "artifact_byte_size, revision, accepted_at_ms) VALUES "
                "(:media_id, :location_id, :source_reference, 'mp4', 0, 8, 30, "
                "1000, 'cover-source-observation-v1', :digest, "
                "'durable-cover-jpeg-v1', 'image/jpeg', :digest, 64, 64, 12, 1, 50)"
            ),
            {
                "media_id": media_id,
                "location_id": location_id,
                "source_reference": f"location:{location_id}",
                "digest": DIGEST,
            },
        )
    if with_upload and media_id == MEDIA_ID:
        connection.execute(
            text(
                "INSERT INTO upload_sessions "
                "(id, state, storage_key, display_filename, declared_size_bytes, "
                "received_size_bytes, checksum_algorithm, checksum_hex, "
                "validated_media_kind, validated_format, byte_identity_id, "
                "duplicate_disposition, created_at_ms, updated_at_ms, expires_at_ms, "
                "failure_code, version) VALUES "
                "(:id, 'cataloged', 'synthetic-upload-0001', 'synthetic.mp4', 8, 8, "
                "'sha256', :digest, 'video', 'mp4', :byte_id, NULL, 10, 20, 100, "
                "NULL, 5)"
            ),
            {"id": UPLOAD_ID, "digest": DIGEST, "byte_id": BYTE_ID},
        )
        relative = f"{PUBLICATION_ID.replace('-', '')}.mp4"
        connection.execute(
            text(
                "INSERT INTO upload_publications "
                "(upload_id, publication_id, destination_id, relative_target, "
                "byte_identity_id, expected_size_bytes, checksum_algorithm, "
                "checksum_hex, validated_media_kind, validated_format, state, "
                "cleanup_state, created_at_ms, updated_at_ms, verified_at_ms, "
                "cleanup_completed_at_ms, version, media_id, media_location_id) "
                "VALUES (:upload_id, :publication_id, :destination_id, :relative, "
                ":byte_id, 8, 'sha256', :digest, 'video', 'mp4', 'verified', "
                "'complete', 20, 20, 20, 20, 1, :media_id, :location_id)"
            ),
            {
                "upload_id": UPLOAD_ID,
                "publication_id": PUBLICATION_ID,
                "destination_id": LIBRARY_ID,
                "relative": relative,
                "byte_id": BYTE_ID,
                "digest": DIGEST,
                "media_id": media_id,
                "location_id": location_id,
            },
        )
    if with_youtube and media_id == MEDIA_ID:
        connection.execute(
            text(
                "INSERT INTO youtube_acquisition_claims "
                "(id, state, acquisition_source, submitted_url, canonical_url, "
                "youtube_video_id, extractor_key, retry_of_claim_id, "
                "resolved_claim_id, upload_id, media_id, media_location_id, "
                "confirmation_method, confirmed_at_ms, upstream_title, "
                "upstream_channel, upstream_channel_id, upstream_source_date, "
                "downloader_name, downloader_version, extractor_version, "
                "selected_video_format_id, selected_audio_format_id, "
                "remote_filename, generated_filename, staging_key, "
                "downloaded_size_bytes, created_at_ms, updated_at_ms, "
                "downloaded_at_ms, completed_at_ms, catalog_removed_at_ms, "
                "failure_stage, failure_code, cleanup_state, "
                "cleanup_completed_at_ms, version) VALUES "
                "(:id, 'cataloged', 'youtube_manual_claim', "
                ":submitted, :canonical, :video_id, 'Youtube', NULL, NULL, "
                ":upload_id, :media_id, :location_id, 'yes_flag', 10, "
                "'Upstream', 'Channel', 'channel', '2026-01-02', 'yt-dlp', "
                "'2026.07.23', '2026.07.23', '137', '140', 'remote.mp4', "
                ":generated, :staging, 8, 10, 20, 15, 20, NULL, NULL, NULL, "
                "'complete', 20, 7)"
            ),
            {
                "id": CLAIM_ID,
                "submitted": f"https://youtu.be/{VIDEO_ID}",
                "canonical": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                "video_id": VIDEO_ID,
                "upload_id": UPLOAD_ID if with_upload else None,
                "media_id": media_id,
                "location_id": location_id,
                "generated": f"youtube-{VIDEO_ID}.mp4",
                "staging": "1" * 32,
            },
        )
    if with_analysis and media_id == MEDIA_ID:
        connection.execute(
            text(
                "INSERT INTO media_analysis_runs "
                "(id, media_id, media_location_id, analysis_definition, state, "
                "attempt_count, provider_id, model_id, prompt_version, "
                "result_schema_version, result_json, error_code, error_message, "
                "provider_submission_occurred, supersedes_run_id, created_at_ms, "
                "started_at_ms, completed_at_ms, version) VALUES "
                "(:superseded, :media_id, :location_id, 'automatic_post_catalog', "
                "'failed', 1, NULL, NULL, NULL, NULL, NULL, 'ANALYSIS_FAILED', "
                "'local', 0, NULL, 20, 20, 21, 1)"
            ),
            {
                "superseded": SUPERSEDED_RUN_ID,
                "media_id": media_id,
                "location_id": location_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO media_analysis_runs "
                "(id, media_id, media_location_id, analysis_definition, state, "
                "attempt_count, provider_id, model_id, prompt_version, "
                "result_schema_version, result_json, error_code, error_message, "
                "provider_submission_occurred, supersedes_run_id, created_at_ms, "
                "started_at_ms, completed_at_ms, version) VALUES "
                "(:run, :media_id, :location_id, 'automatic_post_catalog', "
                "'failed', 1, NULL, NULL, NULL, NULL, NULL, 'ANALYSIS_FAILED', "
                "'local', 1, :superseded, 22, 22, 23, 1)"
            ),
            {
                "run": RUN_ID,
                "superseded": SUPERSEDED_RUN_ID,
                "media_id": media_id,
                "location_id": location_id,
            },
        )


def test_removal_transaction_detaches_provenance_and_keeps_receipt(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    cleanup = _RecordingCleanup()
    repository = SqliteCatalogRemovalRepository(engine)
    service = CatalogMediaRemovalService(
        repository=repository,
        cleanup=cleanup,
        now_ms=lambda: 1000,
    )
    with engine.begin() as connection:
        _seed_base(connection)
        _seed_media(connection, media_id=MEDIA_ID, location_id=LOCATION_ID, title="Keep")
        _seed_media(
            connection,
            media_id=OTHER_MEDIA_ID,
            location_id=OTHER_LOCATION_ID,
            title="Untouched",
            with_upload=False,
            with_youtube=False,
            with_analysis=False,
            with_cover=False,
        )
    try:
        preview = service.preview(MEDIA_ID)
        result = service.execute(
            media_id=MEDIA_ID,
            acknowledge_consequences=True,
            consequence_fingerprint=preview.consequence_fingerprint,
            request_id="req-1",
            actor_key="admin@example.com",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM logical_media WHERE id = :id"),
                {"id": MEDIA_ID},
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT COUNT(*) FROM logical_media WHERE id = :id"),
                {"id": OTHER_MEDIA_ID},
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM media_content_publications")
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    "SELECT state, media_id, media_location_id, "
                    "catalog_removed_at_ms FROM youtube_acquisition_claims "
                    "WHERE id = :id"
                ),
                {"id": CLAIM_ID},
            ).one() == ("catalog_removed", None, None, 1000)
            upload_row = connection.execute(
                text(
                    "SELECT media_id, media_location_id, state FROM "
                    "upload_publications WHERE upload_id = :id"
                ),
                {"id": UPLOAD_ID},
            ).one()
            assert upload_row == (None, None, "verified")
            assert connection.execute(
                text("SELECT COUNT(*) FROM media_analysis_runs")
            ).scalar_one() == 0
            receipt_row = connection.execute(
                text(
                    "SELECT media_id, catalog_outcome, original_bytes_policy, "
                    "youtube_claims_transitioned, upload_publications_detached, "
                    "analysis_run_count, provider_submission_count "
                    "FROM media_catalog_removal_receipts WHERE id = :id"
                ),
                {"id": result.receipt.id},
            ).one()
            assert receipt_row == (
                MEDIA_ID,
                "removed",
                "retain_all",
                1,
                1,
                2,
                1,
            )
            assert connection.execute(
                text("SELECT COUNT(*) FROM media_byte_identities")
            ).scalar_one() == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
            assert (
                connection.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
            )
        assert result.catalog_state == "removed"
        assert result.receipt.original_bytes_outcome == "retained_server_managed"
        assert cleanup.cover_calls
        assert cleanup.preview_calls
    finally:
        engine.dispose()


def test_stale_fingerprint_and_repeat_removal_are_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = SqliteCatalogRemovalRepository(engine)
    service = CatalogMediaRemovalService(
        repository=repository,
        cleanup=_RecordingCleanup(),
        now_ms=lambda: 2000,
    )
    with engine.begin() as connection:
        _seed_base(connection)
        _seed_media(
            connection,
            media_id=MEDIA_ID,
            location_id=LOCATION_ID,
            title="Conflict",
            with_upload=False,
            with_youtube=False,
            with_analysis=False,
            with_cover=False,
        )
    try:
        preview = service.preview(MEDIA_ID)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE logical_media SET updated_at_ms = 99 WHERE id = :id"
                ),
                {"id": MEDIA_ID},
            )
        try:
            service.execute(
                media_id=MEDIA_ID,
                acknowledge_consequences=True,
                consequence_fingerprint=preview.consequence_fingerprint,
                request_id="req-stale",
                actor_key="admin@example.com",
            )
            raise AssertionError("expected stale fingerprint conflict")
        except CatalogRemovalStateConflictError:
            pass
        fresh = service.preview(MEDIA_ID)
        first = service.execute(
            media_id=MEDIA_ID,
            acknowledge_consequences=True,
            consequence_fingerprint=fresh.consequence_fingerprint,
            request_id="req-ok",
            actor_key="admin@example.com",
        )
        assert first.catalog_state == "removed"
        try:
            service.execute(
                media_id=MEDIA_ID,
                acknowledge_consequences=True,
                consequence_fingerprint=fresh.consequence_fingerprint,
                request_id="req-again",
                actor_key="admin@example.com",
            )
            raise AssertionError("expected missing media")
        except CatalogRemovalNotFoundError:
            pass
    finally:
        engine.dispose()


def test_cleanup_retry_is_idempotent_after_failure(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cleanup = _RecordingCleanup()
    cleanup.fail_next_cover = True
    repository = SqliteCatalogRemovalRepository(engine)
    service = CatalogMediaRemovalService(
        repository=repository,
        cleanup=cleanup,
        now_ms=lambda: 3000,
    )
    with engine.begin() as connection:
        _seed_base(connection)
        _seed_media(
            connection,
            media_id=MEDIA_ID,
            location_id=LOCATION_ID,
            title="Cleanup",
            with_upload=False,
            with_youtube=False,
            with_analysis=False,
            with_cover=True,
            published=False,
        )
    try:
        preview = service.preview(MEDIA_ID)
        result = service.execute(
            media_id=MEDIA_ID,
            acknowledge_consequences=True,
            consequence_fingerprint=preview.consequence_fingerprint,
            request_id="req-clean",
            actor_key="admin@example.com",
        )
        assert result.cleanup_retry_available is True
        assert result.receipt.cover_cleanup_state == "failed"
        retried = service.retry_cleanup(result.receipt.id)
        assert retried.receipt.cover_cleanup_state == "complete"
        assert retried.cleanup_retry_available is False
        again = service.retry_cleanup(result.receipt.id)
        assert again.receipt.cover_cleanup_state == "complete"
        assert again.cleanup_retry_available is False
    finally:
        engine.dispose()


def test_concurrent_removals_serialize_to_one_success(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = SqliteCatalogRemovalRepository(engine)
    service = CatalogMediaRemovalService(
        repository=repository,
        cleanup=_RecordingCleanup(),
        now_ms=lambda: 4000,
    )
    with engine.begin() as connection:
        _seed_base(connection)
        _seed_media(
            connection,
            media_id=MEDIA_ID,
            location_id=LOCATION_ID,
            title="Race",
            with_upload=False,
            with_youtube=False,
            with_analysis=False,
            with_cover=False,
            published=False,
        )
    preview = service.preview(MEDIA_ID)

    def attempt(label: str):
        try:
            return (
                "ok",
                service.execute(
                    media_id=MEDIA_ID,
                    acknowledge_consequences=True,
                    consequence_fingerprint=preview.consequence_fingerprint,
                    request_id=label,
                    actor_key="admin@example.com",
                ),
            )
        except CatalogRemovalNotFoundError as exc:
            return ("missing", exc)
        except CatalogRemovalStateConflictError as exc:
            return ("conflict", exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ("a", "b")))
        statuses = sorted(status for status, _payload in outcomes)
        assert statuses.count("ok") == 1
        assert statuses.count("missing") + statuses.count("conflict") == 1
    finally:
        engine.dispose()


def test_detached_upload_no_longer_blocks_live_duplicate_qualification(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    repository = SqliteCatalogRemovalRepository(engine)
    service = CatalogMediaRemovalService(
        repository=repository,
        cleanup=_RecordingCleanup(),
        now_ms=lambda: 5000,
    )
    with engine.begin() as connection:
        _seed_base(connection)
        _seed_media(
            connection,
            media_id=MEDIA_ID,
            location_id=LOCATION_ID,
            title="Upload",
            with_youtube=False,
            with_analysis=False,
            with_cover=False,
            published=False,
        )
    try:
        preview = service.preview(MEDIA_ID)
        service.execute(
            media_id=MEDIA_ID,
            acknowledge_consequences=True,
            consequence_fingerprint=preview.consequence_fingerprint,
            request_id="req-upload",
            actor_key="admin@example.com",
        )
        with engine.connect() as connection:
            live = connection.execute(
                text(
                    "SELECT upload_sessions.id FROM upload_sessions "
                    "LEFT JOIN upload_publications "
                    "ON upload_publications.upload_id = upload_sessions.id "
                    "WHERE upload_sessions.state = 'cataloged' "
                    "AND upload_publications.media_id IS NOT NULL"
                )
            ).fetchall()
            assert live == []
            detached = connection.execute(
                text(
                    "SELECT media_id FROM upload_publications WHERE upload_id = :id"
                ),
                {"id": UPLOAD_ID},
            ).scalar_one()
            assert detached is None
            retained = connection.execute(
                text("SELECT checksum_hex FROM media_byte_identities WHERE id = :id"),
                {"id": BYTE_ID},
            ).scalar_one()
            assert retained == DIGEST
    finally:
        engine.dispose()


def test_migration_0024_preserves_claims_and_adds_receipt_table(
    tmp_path: Path,
) -> None:
    from alembic import command

    from kronika.infrastructure.persistence.migrations import _alembic_config

    database_path = tmp_path / "from-0023.sqlite3"
    settings = KronikaSettings(database_path=database_path, _env_file=None)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "0023")
            connection.commit()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO devices (id, display_name) "
                    "VALUES (:id, 'Synthetic device')"
                ),
                {"id": DEVICE_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO libraries "
                    "(id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device_id, 'Synthetic library', 'posix', "
                    "'/synthetic/media')"
                ),
                {"id": LIBRARY_ID, "device_id": DEVICE_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 20, 20)"
                ),
                {"id": MEDIA_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations "
                    "(id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, "
                    "updated_at_ms) VALUES "
                    "(:id, :media_id, :library_id, 'safe/item.mp4', 'available', "
                    "8, 30, 20, 20)"
                ),
                {
                    "id": LOCATION_ID,
                    "media_id": MEDIA_ID,
                    "library_id": LIBRARY_ID,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO youtube_acquisition_claims "
                    "(id, state, acquisition_source, submitted_url, canonical_url, "
                    "youtube_video_id, extractor_key, confirmation_method, "
                    "confirmed_at_ms, generated_filename, staging_key, "
                    "downloaded_size_bytes, created_at_ms, updated_at_ms, "
                    "downloaded_at_ms, completed_at_ms, media_id, "
                    "media_location_id, cleanup_state, cleanup_completed_at_ms, "
                    "version) VALUES "
                    "(:id, 'duplicate_resolved', 'youtube_manual_claim', "
                    ":submitted, :canonical, :video_id, 'Youtube', 'yes_flag', "
                    "10, :generated, :staging, NULL, 10, 20, NULL, 20, "
                    ":media_id, :location_id, 'complete', 20, 1)"
                ),
                {
                    "id": CLAIM_ID,
                    "submitted": f"https://youtu.be/{VIDEO_ID}",
                    "canonical": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                    "video_id": VIDEO_ID,
                    "generated": f"youtube-{VIDEO_ID}.mp4",
                    "staging": "2" * 32,
                    "media_id": MEDIA_ID,
                    "location_id": LOCATION_ID,
                },
            )
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "0024")
            connection.commit()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0024"
            columns = {
                row[1]
                for row in connection.execute(
                    text("PRAGMA table_info(youtube_acquisition_claims)")
                )
            }
            assert "catalog_removed_at_ms" in columns
            claim = connection.execute(
                text(
                    "SELECT state, media_id, catalog_removed_at_ms "
                    "FROM youtube_acquisition_claims WHERE id = :id"
                ),
                {"id": CLAIM_ID},
            ).one()
            assert claim == ("duplicate_resolved", MEDIA_ID, None)
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
            assert "media_catalog_removal_receipts" in tables
            assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
            assert (
                connection.execute(text("PRAGMA integrity_check")).scalar_one()
                == "ok"
            )
    finally:
        engine.dispose()
