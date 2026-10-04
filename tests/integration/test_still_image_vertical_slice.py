"""End-to-end synthetic still-image upload, catalog, content, and analysis."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import insert

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    MediaRelativePath,
    PreparedAnalysisResult,
    TechnicalMetadata,
    build_representative_frame,
)
from kronika.application.media_analysis_lifecycle import (
    AutomaticImportedMediaSuggestionExecutor,
)
from kronika.application.media_suggestion import (
    MediaSuggestion,
    MediaSuggestionRequest,
    PROMPT_VERSION,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import LibraryId, MediaId, MediaLocationId
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
)
from kronika.domain.media import MediaKind
from kronika.infrastructure.persistence.catalog_schema import devices, libraries
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DESTINATION_ID = LibraryId(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))


def _admin_app(settings: KronikaSettings):
    app = create_app(settings=settings)

    @app.middleware("http")
    async def inject_admin(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = IdentityContext(
            login="admin@example.com",
            login_key="admin@example.com",
            display_name="Admin",
            role=ROLE_ADMIN,
            capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN],
            provenance="test",
        )
        request.scope[SCOPE_AUDIT_EVENT_ID] = "test-audit"
        return await call_next(request)

    return app


def _png_payload(*, size: tuple[int, int] = (16, 12)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (9, 18, 27)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_payload(*, size: tuple[int, int] = (16, 12)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (11, 22, 33)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class _CountingProvider:
    def __init__(self) -> None:
        self.calls: list[MediaSuggestionRequest] = []

    def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
        self.calls.append(request)
        extension = "." + request.basename.rsplit(".", 1)[-1]
        return MediaSuggestion(
            title="Synthetic Still",
            description="A synthetic still image for FrameNest tests.",
            collection="Stills",
            tags=("still", "synthetic"),
            suggested_filename=f"synthetic-still{extension}",
            confidence=0.91,
            evidence=("visible subject",),
            uncertainties=(),
            provider_id="fake-provider",
            model_id="fake-model",
            prompt_version=PROMPT_VERSION,
        )


class _StillPreparer:
    def __init__(self, relative_path: str, payload: bytes) -> None:
        self.relative_path = relative_path
        self.payload = payload
        self.calls = 0

    def prepare(self, root, relative_path: MediaRelativePath) -> PreparedAnalysisResult:
        del root
        self.calls += 1
        assert relative_path.value == self.relative_path
        frame = build_representative_frame(
            timestamp_ms=0,
            payload=_png_frame_payload(),
        )
        return PreparedAnalysisResult(
            relative_path=relative_path,
            candidate_kind=LibraryScanCandidateKind.IMAGE,
            technical_metadata=TechnicalMetadata(
                duration_ms=None,
                width=16,
                height=12,
                video_codec="still",
                container_formats=("png",),
                has_audio=False,
            ),
            representative_frames=(frame,),
            requested_frame_count=3,
            warnings=(),
            ffprobe_version="pillow-still-image",
            ffmpeg_version="unused",
        )


def _png_frame_payload() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


class _MoviePreparer:
    def __init__(self) -> None:
        self.calls = 0

    def prepare(self, root, relative_path: MediaRelativePath) -> PreparedAnalysisResult:
        del root
        self.calls += 1
        frame = build_representative_frame(timestamp_ms=100, payload=_png_frame_payload())
        return PreparedAnalysisResult(
            relative_path=relative_path,
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=TechnicalMetadata(
                duration_ms=1000,
                width=16,
                height=12,
                video_codec="h264",
                container_formats=("mp4",),
                has_audio=False,
            ),
            representative_frames=(frame,),
            requested_frame_count=3,
            warnings=(),
            ffprobe_version="n",
            ffmpeg_version="n",
        )


def _seed_publication_library(database_path: Path, published_root: Path) -> None:
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


def test_still_image_upload_catalog_content_and_single_provider_call(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    quarantine_root = tmp_path / "quarantine"
    published_root = tmp_path / "published"
    cache_root = tmp_path / "cache"
    quarantine_root.mkdir()
    published_root.mkdir()
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=cache_root,
        upload_quarantine_root=quarantine_root,
        upload_publication_library_id=DESTINATION_ID.to_string(),
        upload_max_patch_bytes=1_048_576,
        upload_min_free_space_reserve_bytes=0,
        automatic_media_analysis_enabled=False,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed_publication_library(database_path, published_root)
    payload = _jpeg_payload()

    with TestClient(_admin_app(settings)) as client:
        created = client.post(
            "/api/uploads",
            json={
                "display_filename": "synthetic.jpg",
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
        media_id = status["media_id"]

        tag = client.post(
            "/api/canonical-tags",
            json={"key": "still", "display_name": "Still"},
        )
        assert tag.status_code == 201
        prepared = client.put(
            f"/api/media/{media_id}/metadata",
            json={
                "display_title": "Canonical Still",
                "description": "Saved canonical description.",
                "tag_keys": ["still"],
            },
        )
        assert prepared.status_code == 200
        published = client.put(
            f"/api/admin/media/{media_id}/content-publication",
        )
        assert published.status_code == 201
        assert published.json()["status"] == "published"

        catalog = client.get("/api/media")
        assert catalog.status_code == 200
        items = catalog.json()["items"]
        assert len(items) == 1
        assert items[0]["media_kind"] == "image"
        location_id = items[0]["locations"][0]["location_id"]

        content = client.get(f"/api/media/{media_id}/locations/{location_id}/content")
        assert content.status_code == 200
        assert content.headers["content-type"] == "image/jpeg"
        assert content.content == payload
        assert str(published_root) not in content.text

        download = client.get(f"/api/media/{media_id}/locations/{location_id}/download")
        assert download.status_code == 200
        assert download.content == payload
        assert "attachment" in download.headers.get("content-disposition", "")

        saved = client.put(
            f"/api/media/{media_id}/metadata",
            json={
                "display_title": "Canonical Still",
                "description": "Saved canonical description.",
                "tag_keys": [],
            },
        )
        assert saved.status_code == 200

        before_restart = client.get(f"/api/media/{media_id}/metadata")
        assert before_restart.status_code == 200
        assert before_restart.json()["display_title"] == "Canonical Still"

    with TestClient(_admin_app(settings)) as restarted:
        after_restart = restarted.get(f"/api/media/{media_id}/metadata")
        assert after_restart.status_code == 200
        assert after_restart.json()["display_title"] == "Canonical Still"
        assert after_restart.json()["description"] == "Saved canonical description."

    with sqlite3.connect(database_path) as connection:
        kind = connection.execute(
            "SELECT media_kind FROM logical_media WHERE id = ?",
            (media_id,),
        ).fetchone()[0]
        relative_target = connection.execute(
            "SELECT relative_target FROM upload_publications WHERE media_id = ?",
            (media_id,),
        ).fetchone()[0]
    assert kind == MediaKind.IMAGE.value
    assert relative_target.endswith(".jpg")
    assert (published_root / relative_target).read_bytes() == payload

    provider = _CountingProvider()
    preparer = _StillPreparer(relative_target, payload)

    from kronika.infrastructure.persistence.library_repository import (
        SqliteLibraryRepository,
    )
    from kronika.infrastructure.persistence.media_repository import (
        SqliteMediaRepository,
    )

    engine = create_sqlite_engine(database_path)
    try:
        media_repo = SqliteMediaRepository(engine)
        library_repo = SqliteLibraryRepository(engine)
        executor = AutomaticImportedMediaSuggestionExecutor(
            media_repo,
            library_repo,
            preparer,
            provider,
        )
        suggestion = executor.execute(
            MediaId.from_string(media_id),
            MediaLocationId.from_string(location_id),
        )
        assert suggestion.title == "Synthetic Still"
        assert len(provider.calls) == 1
        assert len(provider.calls[0].representative_frames) == 1
        assert preparer.calls == 1
        assert str(published_root) not in str(provider.calls[0])
    finally:
        dispose_engine(engine)


def test_movie_analysis_remains_single_provider_call(tmp_path: Path) -> None:
    """Regression: movie preparation still yields exactly one provider submission."""
    from kronika.domain import Library, LibraryPathFlavor, LibraryRoot
    from kronika.domain.media import (
        LogicalMedia,
        MediaLocation,
        MediaLocationAvailability,
        MediaRelativePath as DomainRelativePath,
    )

    media_id = MediaId.new()
    location_id = MediaLocationId.new()
    library_id = LibraryId.new()

    class _Repo:
        def get_media(self, requested):
            assert requested == media_id
            return LogicalMedia(
                id=media_id,
                kind=MediaKind.VIDEO,
                created_at_ms=1,
                updated_at_ms=1,
            )

        def get_location(self, requested):
            assert requested == location_id
            return MediaLocation(
                id=location_id,
                media_id=media_id,
                library_id=library_id,
                relative_path=DomainRelativePath("clip.mp4"),
                availability=MediaLocationAvailability.AVAILABLE,
                observed_size_bytes=10,
                observed_mtime_ns=1,
                created_at_ms=1,
                updated_at_ms=1,
            )

    class _Libraries:
        def get(self, requested):
            assert requested == library_id
            return Library(
                id=library_id,
                device_id=__import__("kronika.domain", fromlist=["DeviceId"]).DeviceId.new(),
                display_name="Lib",
                root=LibraryRoot(LibraryPathFlavor.POSIX, str(tmp_path)),
            )

    provider = _CountingProvider()
    preparer = _MoviePreparer()
    executor = AutomaticImportedMediaSuggestionExecutor(
        _Repo(),  # type: ignore[arg-type]
        _Libraries(),  # type: ignore[arg-type]
        preparer,
        provider,
    )
    suggestion = executor.execute(media_id, location_id)
    assert suggestion.title == "Synthetic Still"
    assert len(provider.calls) == 1
    assert preparer.calls == 1
