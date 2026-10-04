"""Opt-in real-tool end-to-end cover workflow using synthetic GIF/MP4 fixtures."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.media_cover import (
    CoverSourceChangedError,
    CoverService,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import (
    DeviceId,
    LibraryId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
)
from kronika.infrastructure.filesystem.cover_storage import (
    FilesystemCoverThumbnailCache,
    FilesystemDurableCoverStorage,
    PillowCoverEncoder,
)
from kronika.infrastructure.media_analysis.cover_frame import LocalCoverSourceAdapter
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_cover_repository import (
    SqliteMediaCoverRepository,
)
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAMENEST_RUN_REAL_MEDIA_TOOLS") != "1",
    reason="Set FRAMENEST_RUN_REAL_MEDIA_TOOLS=1 to run real-tool cover workflow tests.",
)

MEDIA_MP4 = MediaId.from_string("11111111-1111-4111-8111-111111111111")
MEDIA_GIF = MediaId.from_string("22222222-2222-4222-8222-222222222222")
LOC_MP4 = MediaLocationId.from_string("33333333-3333-4333-8333-333333333333")
LOC_GIF = MediaLocationId.from_string("44444444-4444-4444-8444-444444444444")
LIBRARY_ID = LibraryId.from_string("55555555-5555-4555-8555-555555555555")
DEVICE_ID = DeviceId.from_string("66666666-6666-4666-8666-666666666666")


def _require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.fail("ffmpeg is required for real-tool cover workflow tests")
    return executable


def _generate_media(root: Path) -> None:
    ffmpeg = _require_ffmpeg()
    subprocess.run(
        [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2",
            "-pix_fmt", "yuv420p", str(root / "clip.mp4"),
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=160x120:d=1",
            str(root / "clip.gif"),
        ],
        check=True,
        timeout=30,
    )


def _seed_catalog(settings: KronikaSettings, root: Path) -> None:
    connection = sqlite3.connect(settings.database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executemany(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) VALUES (?, ?, 1, 1)",
            ((MEDIA_MP4.to_string(), "video"), (MEDIA_GIF.to_string(), "animated_image")),
        )
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'device')",
            (DEVICE_ID.to_string(),),
        )
        connection.execute(
            "INSERT INTO libraries "
            "(id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, 'l', 'posix', ?)",
            (LIBRARY_ID.to_string(), DEVICE_ID.to_string(), str(root)),
        )
        connection.executemany(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            " observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, 'available', 0, 0, 1, 1)",
            (
                (LOC_MP4.to_string(), MEDIA_MP4.to_string(), LIBRARY_ID.to_string(), "clip.mp4"),
                (LOC_GIF.to_string(), MEDIA_GIF.to_string(), LIBRARY_ID.to_string(), "clip.gif"),
            ),
        )
        connection.executemany(
            "INSERT INTO media_content_publications "
            "(media_id, published_at_ms, publication_origin) VALUES (?, 1, 'legacy_backfill')",
            ((MEDIA_MP4.to_string(),), (MEDIA_GIF.to_string(),)),
        )
        connection.commit()
    finally:
        connection.close()


def _service(settings: KronikaSettings) -> CoverService:
    engine = create_sqlite_engine(settings.database_path)
    service = CoverService(
        SqliteMediaRepository(engine),
        SqliteLibraryRepository(engine),
        LocalCoverSourceAdapter(),
        PillowCoverEncoder(),
        FilesystemDurableCoverStorage(settings.cover_storage_root),
        FilesystemCoverThumbnailCache(settings.cover_thumbnail_cache_path),
        SqliteMediaCoverRepository(engine),
    )
    service.__dict__["_engine"] = engine
    return service


@pytest.fixture
def workflow(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    _generate_media(root)
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed_catalog(settings, root)
    service = _service(settings)
    try:
        yield service, settings
    finally:
        engine = service.__dict__.get("_engine")
        if engine is not None:
            dispose_engine(engine)


def test_mp4_and_gif_manual_cover_workflow_roundtrip(workflow) -> None:
    service, settings = workflow
    for media_id, location_id in ((MEDIA_MP4, LOC_MP4), (MEDIA_GIF, LOC_GIF)):
        timeline = service.timeline(media_id, location_id)
        assert timeline.duration_ms > 0
        assert len(timeline.source_version) == 64

        preview = service.preview(
            media_id,
            location_id,
            250,
            expected_source_version=timeline.source_version,
        )
        assert preview.media_type == "image/png"
        assert preview.payload

        created = service.accept(
            media_id,
            location_id,
            timestamp_ms=250,
            expected_revision=0,
            expected_source_version=timeline.source_version,
        )
        assert created.status == "created"
        assert created.revision == 1
        assert created.thumbnail_state == "ready"

        state = service.admin_state(media_id)
        assert state.has_cover is True
        assert state.artifact_state == "available"
        assert state.thumbnail_state == "ready"

        opened = service.open_thumbnail(media_id)
        assert opened.byte_size > 0
        opened.close()

    assert service.cover_ready_map((MEDIA_MP4.to_string(), MEDIA_GIF.to_string())) == {
        MEDIA_MP4.to_string(): True,
        MEDIA_GIF.to_string(): True,
    }


def test_source_change_is_detected_and_rejects_stale_accept(workflow) -> None:
    service, settings = workflow
    timeline = service.timeline(MEDIA_MP4, LOC_MP4)
    # Change the source bytes and mtime to invalidate the source observation.
    from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository

    engine = service.__dict__["_engine"]
    library_repo = SqliteLibraryRepository(engine)
    media_repo = SqliteMediaRepository(engine)
    library = library_repo.get(LIBRARY_ID)
    location = media_repo.get_location(LOC_MP4)
    path = Path(library.root.path) / location.relative_path.value
    path.write_bytes(path.read_bytes() + b"\x00" * 8)

    with pytest.raises(CoverSourceChangedError):
        service.accept(
            MEDIA_MP4,
            LOC_MP4,
            timestamp_ms=250,
            expected_revision=0,
            expected_source_version=timeline.source_version,
        )
    assert service.admin_state(MEDIA_MP4).has_cover is False


def test_physical_location_deletion_keeps_cover_usable(workflow) -> None:
    service, settings = workflow
    timeline = service.timeline(MEDIA_MP4, LOC_MP4)
    service.accept(
        MEDIA_MP4,
        LOC_MP4,
        timestamp_ms=250,
        expected_revision=0,
        expected_source_version=timeline.source_version,
    )
    connection = sqlite3.connect(settings.database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "DELETE FROM physical_media_locations WHERE id = ?",
            (LOC_MP4.to_string(),),
        )
        connection.commit()
    finally:
        connection.close()

    state = service.admin_state(MEDIA_MP4)
    assert state.has_cover is True
    assert state.artifact_state == "available"
    assert state.thumbnail_state == "ready"
    opened = service.open_thumbnail(MEDIA_MP4)
    opened.close()


def test_http_accept_sets_cover_ready_and_thumbnail_etag_workflow(workflow) -> None:
    service, settings = workflow
    app = create_app(settings=settings)

    @app.middleware("http")
    async def inject_admin(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = IdentityContext(
            login="admin@example.com",
            login_key="admin@example.com",
            display_name="Admin",
            role=ROLE_ADMIN,
            capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN],
            provenance="tailscale-serve",
        )
        from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID
        import uuid

        request.scope[SCOPE_AUDIT_EVENT_ID] = str(uuid.uuid4())
        return await call_next(request)

    timeline = service.timeline(MEDIA_MP4, LOC_MP4)
    with TestClient(app) as client:
        put = client.put(
            f"/api/media/{MEDIA_MP4}/locations/{LOC_MP4}/cover",
            json={
                "timestamp_ms": 250,
                "expected_revision": 0,
                "expected_source_version": timeline.source_version,
            },
        )
        assert put.status_code == 201
        assert put.json()["thumbnail_state"] == "ready"

        listing = client.get("/api/media")
        items = {item["media_id"]: item for item in listing.json()["items"]}
        assert items[MEDIA_MP4.to_string()]["cover_ready"] is True
        assert items[MEDIA_GIF.to_string()]["cover_ready"] is False

        thumb = client.get(f"/api/media/{MEDIA_MP4}/cover-thumbnail")
        assert thumb.status_code == 200
        etag = thumb.headers["etag"]
        assert etag.startswith('"')
        not_modified = client.get(
            f"/api/media/{MEDIA_MP4}/cover-thumbnail",
            headers={"If-None-Match": etag},
        )
        assert not_modified.status_code == 304


def test_thumbnail_etag_is_bound_to_algorithm_identity(workflow) -> None:
    service, settings = workflow
    timeline = service.timeline(MEDIA_MP4, LOC_MP4)
    service.accept(
        MEDIA_MP4,
        LOC_MP4,
        timestamp_ms=250,
        expected_revision=0,
        expected_source_version=timeline.source_version,
    )
    etag = service.thumbnail_etag(MEDIA_MP4)
    assert etag is not None

    import hashlib

    for hypothetical_algorithm in ("cover-thumbnail-jpeg-v2", "cover-thumbnail-jpeg-v1"):
        from kronika.domain.media_cover import MediaCover

        cover = service.__dict__["_cover_repository"].get(MEDIA_MP4)
        expected = '"' + hashlib.sha256(
            f"{hypothetical_algorithm}|{cover.artifact_digest}".encode("utf-8")
        ).hexdigest() + '"'
        if hypothetical_algorithm == "cover-thumbnail-jpeg-v1":
            assert etag == expected
        else:
            assert etag != expected
