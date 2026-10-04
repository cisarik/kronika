"""Requester-private YouTube Details hydration and privacy contract.

Proves FrameNest catalog-detail hydration for requester-owned private media.
YouTube bytes are already cataloged into FrameNest; this suite does not upload
to YouTube and does not exercise live yt-dlp acquisition.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.adapters.api.youtube_request_api import (
    YouTubeRequestApiDependencies,
    create_youtube_request_api_router,
)
from kronika.application.youtube_acquisition import (
    YouTubeRequestLimits,
    YouTubeRequestService,
)
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
    SqliteYouTubeAcquisitionClaimRepository,
)

MEDIA_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOCATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CLAIM_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
UNKNOWN_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
VIDEO_ID = "AbCdEf123_-"
OWNER_LOGIN = "owner@example.com"
FOREIGN_LOGIN = "foreign@example.com"
ADMIN_LOGIN = "admin@example.com"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100


def _identity(login: str, role: str) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login.split("@", 1)[0].title(),
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


def _native_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _register_library(database_path: Path, library_root: Path) -> LibraryId:
    engine = create_sqlite_engine(database_path)
    library_id = LibraryId.new()
    try:
        device = Device(id=DeviceId.new(), display_name="Private Details Device")
        SqliteDeviceRepository(engine).add(device)
        library = Library(
            id=library_id,
            device_id=device.id,
            display_name="Private Details Library",
            root=LibraryRoot(
                flavor=_native_flavor(),
                path=os.path.normpath(str(library_root)),
            ),
        )
        SqliteLibraryRepository(engine).add(library)
        return library_id
    finally:
        dispose_engine(engine)


def _seed_private_owned_media(
    database_path: Path,
    *,
    library_id: LibraryId,
    owner_login: str = OWNER_LOGIN,
    title: str | None = "Owner Private Title",
    upstream_title: str | None = "Upstream",
    relative_path: str = "owner-private.mp4",
) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 10, 10)"
                ),
                {"id": MEDIA_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO media_metadata "
                    "(media_id, display_title, description, collection_key, "
                    "processed_at_ms, created_at_ms, updated_at_ms, "
                    "content_category, acquisition_source) "
                    "VALUES (:media_id, :title, :description, NULL, NULL, 10, 10, "
                    "'general', 'youtube_manual_claim')"
                ),
                {
                    "media_id": MEDIA_ID,
                    "title": title,
                    "description": (
                        None if title is None else "Private requester description"
                    ),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations "
                    "(id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, "
                    "updated_at_ms) "
                    "VALUES (:id, :media_id, :library_id, :relative, 'available', "
                    ":size, 1, 10, 10)"
                ),
                {
                    "id": LOCATION_ID,
                    "media_id": MEDIA_ID,
                    "library_id": library_id.to_string(),
                    "relative": relative_path,
                    "size": len(MP4_BYTES),
                },
            )
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
                    "cleanup_completed_at_ms, version, created_by_login_key) VALUES "
                    "(:id, 'duplicate_resolved', 'youtube_manual_claim', "
                    ":submitted, :canonical, :video_id, 'Youtube', NULL, NULL, "
                    "NULL, :media_id, :location_id, 'interactive', 10, "
                    ":upstream_title, 'Channel', 'channel', '2026-01-02', 'yt-dlp', "
                    "'2026.07.23', '2026.07.23', '18', NULL, 'remote.mp4', "
                    ":generated, :staging, :size, 10, 20, NULL, 20, NULL, NULL, NULL, "
                    "'complete', 20, 1, :owner)"
                ),
                {
                    "id": CLAIM_ID,
                    "submitted": f"https://youtu.be/{VIDEO_ID}",
                    "canonical": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                    "video_id": VIDEO_ID,
                    "media_id": MEDIA_ID,
                    "location_id": LOCATION_ID,
                    "generated": f"youtube-{VIDEO_ID}.mp4",
                    "staging": "a" * 32,
                    "size": len(MP4_BYTES),
                    "owner": owner_login,
                    "upstream_title": upstream_title,
                },
            )
    finally:
        dispose_engine(engine)


class _StagingStub:
    def cleanup(self, staging_key: str) -> None:
        return None

    def available_bytes(self) -> int:
        return 10_000_000_000


def _request_client(database_path: Path, login: str) -> TestClient:
    engine = create_sqlite_engine(database_path)
    service = YouTubeRequestService(
        SqliteYouTubeAcquisitionClaimRepository(engine),
        type("Publication", (), {"is_published": staticmethod(lambda media_id: False)})(),
        _StagingStub(),
        limits=YouTubeRequestLimits(),
    )
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_youtube_request_api_router(
            YouTubeRequestApiDependencies(
                service=service,
                audit_recorder=None,
                enabled=True,
            )
        )
    )

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(login, ROLE_USER)
        return await call_next(request)

    client = TestClient(app)
    client._engine = engine  # type: ignore[attr-defined]
    return client


def _media_client(settings: KronikaSettings, login: str, role: str) -> TestClient:
    app = create_app(settings=settings)

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(login, role)
        return await call_next(request)

    return TestClient(app)


def test_owner_private_details_hydrate_while_gallery_stays_published_only(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "owner-private.mp4").write_bytes(MP4_BYTES)
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)
    _seed_private_owned_media(database_path, library_id=library_id)

    with _media_client(settings, OWNER_LOGIN, ROLE_USER) as owner:
        gallery = owner.get("/api/media")
        assert gallery.status_code == 200
        assert gallery.json()["items"] == []

        detail = owner.get(f"/api/media/{MEDIA_ID}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["media_id"] == MEDIA_ID
        assert payload["media_kind"] == "video"
        assert payload["display_title"] == "Owner Private Title"
        assert payload["description"] == "Private requester description"
        assert payload["locations"][0]["location_id"] == LOCATION_ID
        assert payload["locations"][0]["availability"] == "available"

        metadata = owner.get(f"/api/media/{MEDIA_ID}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["display_title"] == "Owner Private Title"

        content = owner.get(
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/content"
        )
        download = owner.get(
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/download"
        )
        ranged = owner.get(
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/content",
            headers={"Range": "bytes=0-3"},
        )
        assert content.status_code == 200
        assert content.content == MP4_BYTES
        assert download.status_code == 200
        assert download.content == MP4_BYTES
        assert ranged.status_code == 206

    request_client = _request_client(database_path, OWNER_LOGIN)
    try:
        requests = request_client.get("/api/youtube/requests")
        assert requests.status_code == 200
        item = requests.json()["items"][0]
        assert item["phase"] == "completed_private"
        assert item["media_id"] == MEDIA_ID
    finally:
        dispose_engine(request_client._engine)  # type: ignore[attr-defined]

    with _media_client(settings, FOREIGN_LOGIN, ROLE_USER) as foreign:
        gallery = foreign.get("/api/media")
        assert gallery.status_code == 200
        assert gallery.json()["items"] == []

        denied_detail = foreign.get(f"/api/media/{MEDIA_ID}")
        unknown_detail = foreign.get(f"/api/media/{UNKNOWN_ID}")
        assert denied_detail.status_code == unknown_detail.status_code == 404
        assert denied_detail.json() == unknown_detail.json()
        assert "Owner Private Title" not in denied_detail.text
        assert OWNER_LOGIN not in denied_detail.text
        assert VIDEO_ID not in denied_detail.text

        for path in (
            f"/api/media/{MEDIA_ID}/metadata",
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/content",
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/download",
        ):
            denied = foreign.get(path)
            unknown = foreign.get(path.replace(MEDIA_ID, UNKNOWN_ID))
            assert denied.status_code == unknown.status_code == 404
            assert denied.json() == unknown.json()
            assert "Owner Private Title" not in denied.text
            assert OWNER_LOGIN not in denied.text

    foreign_requests = _request_client(database_path, FOREIGN_LOGIN)
    try:
        requests = foreign_requests.get("/api/youtube/requests")
        assert requests.status_code == 200
        assert requests.json()["items"] == []
    finally:
        dispose_engine(foreign_requests._engine)  # type: ignore[attr-defined]

    with _media_client(settings, ADMIN_LOGIN, ROLE_ADMIN) as admin:
        workflow = admin.get("/api/admin/media")
        assert workflow.status_code == 200
        assert any(item["media_id"] == MEDIA_ID for item in workflow.json()["items"])
        save = admin.put(
            f"/api/media/{MEDIA_ID}/metadata",
            json={
                "display_title": "Admin Saved Title",
                "description": "Admin description",
                "tag_keys": [],
            },
        )
        assert save.status_code == 200

    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_content_publications "
                    "(media_id, published_at_ms, publication_origin) "
                    "VALUES (:media_id, 100, 'admin_explicit')"
                ),
                {"media_id": MEDIA_ID},
            )
    finally:
        dispose_engine(engine)

    with _media_client(settings, OWNER_LOGIN, ROLE_USER) as owner:
        gallery = owner.get("/api/media")
        assert gallery.status_code == 200
        assert any(item["media_id"] == MEDIA_ID for item in gallery.json()["items"])
        detail = owner.get(f"/api/media/{MEDIA_ID}")
        assert detail.status_code == 200
        assert detail.json()["display_title"] == "Admin Saved Title"

    engine = create_sqlite_engine(database_path)
    try:
        class _PublishedLookup:
            def is_published(self, media_id: object) -> bool:
                text_id = (
                    media_id.to_string()
                    if hasattr(media_id, "to_string")
                    else str(media_id)
                )
                return text_id == MEDIA_ID

        service = YouTubeRequestService(
            SqliteYouTubeAcquisitionClaimRepository(engine),
            _PublishedLookup(),
            _StagingStub(),
            limits=YouTubeRequestLimits(),
        )
        page = service.list_owned(created_by_login_key=OWNER_LOGIN)
        assert page.items[0].phase == "completed"
        assert page.items[0].media_id == MEDIA_ID
    finally:
        dispose_engine(engine)


def test_removed_requester_claim_clears_media_access(tmp_path: Path) -> None:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "owner-private.mp4").write_bytes(MP4_BYTES)
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)
    _seed_private_owned_media(database_path, library_id=library_id)

    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE youtube_acquisition_claims SET "
                    "state = 'catalog_removed', media_id = NULL, "
                    "media_location_id = NULL, catalog_removed_at_ms = 50, "
                    "updated_at_ms = 50 WHERE id = :id"
                ),
                {"id": CLAIM_ID},
            )
            connection.execute(
                text("DELETE FROM physical_media_locations WHERE id = :id"),
                {"id": LOCATION_ID},
            )
            connection.execute(
                text("DELETE FROM media_metadata WHERE media_id = :id"),
                {"id": MEDIA_ID},
            )
            connection.execute(
                text("DELETE FROM logical_media WHERE id = :id"),
                {"id": MEDIA_ID},
            )
    finally:
        dispose_engine(engine)

    request_client = _request_client(database_path, OWNER_LOGIN)
    try:
        requests = request_client.get("/api/youtube/requests")
        assert requests.status_code == 200
        item = requests.json()["items"][0]
        assert item["phase"] == "unavailable"
        assert item["media_id"] is None
    finally:
        dispose_engine(request_client._engine)  # type: ignore[attr-defined]

    with _media_client(settings, OWNER_LOGIN, ROLE_USER) as owner:
        detail = owner.get(f"/api/media/{MEDIA_ID}")
        unknown = owner.get(f"/api/media/{UNKNOWN_ID}")
        assert detail.status_code == unknown.status_code == 404
        assert detail.json() == unknown.json()


def test_imported_upstream_title_is_canonical_until_admin_save(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    library_root = tmp_path / "library"
    library_root.mkdir()
    hash_filename = "a1b2c3d4e5f6789012345678abcdef01.mp4"
    (library_root / hash_filename).write_bytes(MP4_BYTES)
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)
    imported_title = "Realistic Upstream YouTube Title"
    _seed_private_owned_media(
        database_path,
        library_id=library_id,
        title=imported_title,
        upstream_title=imported_title,
        relative_path=hash_filename,
    )

    with _media_client(settings, OWNER_LOGIN, ROLE_USER) as owner:
        gallery = owner.get("/api/media")
        assert gallery.status_code == 200
        assert gallery.json()["items"] == []

        detail = owner.get(f"/api/media/{MEDIA_ID}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["display_title"] == imported_title
        assert hash_filename not in payload["display_title"]
        assert payload["locations"][0]["relative_path"] == hash_filename

        content = owner.get(
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/content"
        )
        download = owner.get(
            f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/download"
        )
        assert content.status_code == 200
        assert content.content == MP4_BYTES
        assert download.status_code == 200
        assert download.content == MP4_BYTES

    with _media_client(settings, FOREIGN_LOGIN, ROLE_USER) as foreign:
        denied = foreign.get(f"/api/media/{MEDIA_ID}")
        unknown = foreign.get(f"/api/media/{UNKNOWN_ID}")
        assert denied.status_code == unknown.status_code == 404
        assert denied.json() == unknown.json()
        assert imported_title not in denied.text
        assert hash_filename not in denied.text
        assert OWNER_LOGIN not in denied.text

    with _media_client(settings, ADMIN_LOGIN, ROLE_ADMIN) as admin:
        detail = admin.get(f"/api/media/{MEDIA_ID}")
        assert detail.status_code == 200
        assert detail.json()["display_title"] == imported_title
        save = admin.put(
            f"/api/media/{MEDIA_ID}/metadata",
            json={
                "display_title": "Admin Canonical Title",
                "description": "Canonical description",
                "tag_keys": [],
            },
        )
        assert save.status_code == 200
        assert save.json()["status"] in {"created", "updated", "unchanged"}
        reloaded = admin.get(f"/api/media/{MEDIA_ID}")
        assert reloaded.status_code == 200
        assert reloaded.json()["display_title"] == "Admin Canonical Title"
        metadata = admin.get(f"/api/media/{MEDIA_ID}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["display_title"] == "Admin Canonical Title"

    with _media_client(settings, OWNER_LOGIN, ROLE_USER) as owner:
        detail = owner.get(f"/api/media/{MEDIA_ID}")
        assert detail.status_code == 200
        assert detail.json()["display_title"] == "Admin Canonical Title"
        assert detail.json()["display_title"] != imported_title


def test_missing_upstream_and_display_title_do_not_invent_product_title(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    library_root = tmp_path / "library"
    library_root.mkdir()
    hash_filename = "deadbeefcafebabe0123456789abcdef.mp4"
    (library_root / hash_filename).write_bytes(MP4_BYTES)
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)
    _seed_private_owned_media(
        database_path,
        library_id=library_id,
        title=None,
        upstream_title=None,
        relative_path=hash_filename,
    )

    with _media_client(settings, OWNER_LOGIN, ROLE_USER) as owner:
        detail = owner.get(f"/api/media/{MEDIA_ID}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["display_title"] is None
        assert "/" not in (payload["locations"][0]["relative_path"] or "")
        assert payload["locations"][0]["relative_path"] == hash_filename

    with _media_client(settings, FOREIGN_LOGIN, ROLE_USER) as foreign:
        denied = foreign.get(f"/api/media/{MEDIA_ID}")
        unknown = foreign.get(f"/api/media/{UNKNOWN_ID}")
        assert denied.status_code == unknown.status_code == 404
        assert denied.json() == unknown.json()
        assert hash_filename not in denied.text
        assert OWNER_LOGIN not in denied.text


def test_forbidden_bound_media_link_is_unavailable_without_media_id(
    tmp_path: Path,
) -> None:
    from kronika.domain.records import RecordId
    from kronika.infrastructure.persistence.content_publication_repository import (
        SqliteContentPublicationRepository,
    )
    from kronika.infrastructure.persistence.engine import run_in_immediate_transaction
    from kronika.infrastructure.persistence.record_repository import (
        SqliteRecordRepository,
    )

    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    library_root = tmp_path / "library"
    library_root.mkdir()
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)
    _seed_private_owned_media(
        database_path, library_id=library_id, owner_login=OWNER_LOGIN
    )
    engine = create_sqlite_engine(database_path)
    try:
        def bind(connection) -> None:
            SqliteRecordRepository(engine).bind_media_record(
                connection,
                record_id=RecordId.new(),
                media_id=MEDIA_ID,
                owner_login_key=FOREIGN_LOGIN,
                created_at_ms=3,
            )

        run_in_immediate_transaction(engine, bind)
        service = YouTubeRequestService(
            SqliteYouTubeAcquisitionClaimRepository(engine),
            SqliteContentPublicationRepository(engine),
            _StagingStub(),
            limits=YouTubeRequestLimits(),
        )
        page = service.list_owned(created_by_login_key=OWNER_LOGIN)
        assert len(page.items) == 1
        assert page.items[0].phase == "unavailable"
        assert page.items[0].media_id is None
    finally:
        dispose_engine(engine)
