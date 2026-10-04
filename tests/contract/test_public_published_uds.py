"""Contract tests for the local-only public_published_uds reader."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Route

from kronika.adapters.api import public_published_api as public_published_api_module
from kronika.adapters.api.application import create_app
from kronika.adapters.api.public_published_api import (
    PublicPublishedApiDependencies,
    create_public_published_api_router,
)
from kronika.adapters.api.public_published_application import (
    PublicPublishedStartupError,
    REQUIRED_PUBLIC_SCHEMA_REVISION,
    create_public_published_app,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import MediaId
from kronika.infrastructure.persistence.content_publication_repository import (
    SqliteContentPublicationRepository,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    create_sqlite_readonly_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.structured_logging import (
    FrameNestJsonFormatter,
    FrameNestRedactionFilter,
)

GIF_ID = "11111111-1111-4111-8111-111111111111"
IMAGE_ID = "22222222-2222-4222-8222-222222222222"
VIDEO_ID = "33333333-3333-4333-8333-333333333333"
MOVIE_ID = "44444444-4444-4444-8444-444444444444"
UNPUBLISHED_ID = "55555555-5555-4555-8555-555555555555"
UNKNOWN_ID = "66666666-6666-4666-8666-666666666666"
GIF_LOC = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
IMAGE_LOC = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
VIDEO_LOC = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
MOVIE_LOC = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
UNPUB_LOC = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
LIBRARY_ID = "77777777-7777-4777-8777-777777777777"
DEVICE_ID = "88888888-8888-4888-8888-888888888888"
UNUSED_TAG = "unused"
PUBLISHED_TAG = "published"
SECRET_STABLE_ID = "provider-stable-secret"
PRIVATE_RELATIVE_PATH = "private/secret-clip.mp4"

ALLOWED_GET_PATHS = {
    "/",
    "/assets/app.js",
    "/assets/styles.css",
    "/api/audience/me",
    "/api/media",
    "/api/media/{media_id}",
    "/api/canonical-tags",
    "/api/media/{media_id}/metadata",
    "/api/media/{media_id}/locations/{location_id}/content",
    "/api/media/{media_id}/locations/{location_id}/gallery-preview",
    "/api/media/{media_id}/cover-thumbnail",
}

UNLISTED_PATHS = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/status/cloud",
    "/api/identity/me",
    "/api/libraries",
    "/api/admin/media",
    "/api/workspace/media",
    "/api/workspace/media/11111111-1111-4111-8111-111111111111/analysis-proposals",
    "/api/admin/analysis-proposals",
    "/api/admin/media/11111111-1111-4111-8111-111111111111/aliases",
    "/api/uploads",
    "/api/ai/media-suggestion-capability",
    "/assets/companion_host.js",
    f"/api/media/{GIF_ID}/locations/{GIF_LOC}/download",
    f"/api/media/{GIF_ID}/alias",
)

FORBIDDEN_FIELDS = {
    "library_id",
    "relative_path",
    "created_at_ms",
    "updated_at_ms",
    "observed_size_bytes",
    "observed_mtime_ns",
    "processed_at_ms",
    "collection_key",
    "creator_stable_id",
    "persisted",
    "alias",
    "aliases",
}

WORKSPACE_ROUTER_MARKERS = (
    "create_upload_api_router",
    "create_companion_review_api_router",
    "create_x_admin_api_router",
    "create_youtube_operator_api_router",
    "create_media_alias_api_router",
    "create_library_api_router",
    "create_content_publication_api_router",
    "create_catalog_removal_api_router",
    "create_workspace_media_api_router",
    "create_analysis_proposal_api_router",
    "create_team_alias_api_router",
)


def _settings(tmp_path: Path) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        ingress_mode="public_published_uds",
        uds_path=tmp_path / "public.sock",
        _env_file=None,
    )


def _seed(settings: KronikaSettings, library_root: Path) -> None:
    library_root.mkdir()
    (library_root / "clip.gif").write_bytes(b"GIF89a" + b"\x00" * 32)
    (library_root / "still.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (library_root / "clip.mp4").write_bytes(bytes(range(256)) * 8)
    (library_root / "movie.mp4").write_bytes(bytes(range(256)) * 4)
    (library_root / "hidden.mp4").write_bytes(b"hidden-bytes")
    connection = sqlite3.connect(settings.database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'device')",
            (DEVICE_ID,),
        )
        connection.execute(
            "INSERT INTO libraries "
            "(id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, 'lib', 'posix', ?)",
            (LIBRARY_ID, DEVICE_ID, str(library_root)),
        )
        connection.executemany(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) VALUES (?, ?, 1, 1)",
            (
                (GIF_ID, "animated_image"),
                (IMAGE_ID, "image"),
                (VIDEO_ID, "video"),
                (MOVIE_ID, "video"),
                (UNPUBLISHED_ID, "video"),
            ),
        )
        connection.executemany(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            " observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, 'available', 99, 7, 1, 1)",
            (
                (GIF_LOC, GIF_ID, LIBRARY_ID, "clip.gif"),
                (IMAGE_LOC, IMAGE_ID, LIBRARY_ID, "still.png"),
                (VIDEO_LOC, VIDEO_ID, LIBRARY_ID, "clip.mp4"),
                (MOVIE_LOC, MOVIE_ID, LIBRARY_ID, "movie.mp4"),
                (UNPUB_LOC, UNPUBLISHED_ID, LIBRARY_ID, "hidden.mp4"),
            ),
        )
        connection.execute(
            "INSERT INTO canonical_tags "
            "(key, display_name, created_at_ms, updated_at_ms) VALUES "
            "('published', 'Published', 1, 1), ('unused', 'Unused', 1, 1)"
        )
        connection.executemany(
            "INSERT INTO media_metadata "
            "(media_id, display_title, description, content_category, "
            " acquisition_source, creator_attribution_kind, creator_stable_id, "
            " creator_handle, creator_display_name, collection_key, "
            " processed_at_ms, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, 'unknown', 'x_author', ?, 'public-handle', "
            " 'Public Creator', 'processed', 9, 1, 1)",
            (
                (GIF_ID, "Published GIF", "A published gif", "meme", SECRET_STABLE_ID),
                (IMAGE_ID, "Published image", "A published image", "general", SECRET_STABLE_ID),
                (VIDEO_ID, "Published video", "A published video", "general", SECRET_STABLE_ID),
                (MOVIE_ID, "Published movie", "A published movie", "movie", SECRET_STABLE_ID),
                (UNPUBLISHED_ID, "Hidden video", "Should not leak", "general", SECRET_STABLE_ID),
            ),
        )
        connection.executemany(
            "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
            "VALUES (?, 'published', 0)",
            ((GIF_ID,), (IMAGE_ID,), (VIDEO_ID,), (MOVIE_ID,), (UNPUBLISHED_ID,)),
        )
        connection.execute(
            "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
            "VALUES (?, 'unused', 1)",
            (UNPUBLISHED_ID,),
        )
        connection.executemany(
            "INSERT INTO media_content_publications "
            "(media_id, published_at_ms, publication_origin) "
            "VALUES (?, 10, 'admin_explicit')",
            ((GIF_ID,), (IMAGE_ID,), (VIDEO_ID,), (MOVIE_ID,)),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def public_client(tmp_path: Path):
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    _seed(settings, tmp_path / "library")
    with TestClient(create_app(settings=settings)) as client:
        yield client, settings


def _not_found(response) -> None:
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not found."}}
    assert "no-store" in response.headers.get("cache-control", "")
    assert "access-control-allow-origin" not in {
        name.lower() for name in response.headers
    }


def _route_method_name(method: object) -> str:
    if isinstance(method, str):
        return method.upper()
    name = getattr(method, "name", None) or getattr(method, "value", None)
    if isinstance(name, str):
        return name.upper()
    return str(method).rsplit(".", 1)[-1].upper()


def _collect_declared_routes(root: object) -> set[tuple[str, str]]:
    declared: set[tuple[str, str]] = set()
    stack: list[object] = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        routes = getattr(node, "routes", None)
        if isinstance(routes, (list, tuple)):
            stack.extend(routes)
        router = getattr(node, "router", None)
        if router is not None:
            stack.append(router)
        methods = getattr(node, "methods", None)
        path = getattr(node, "path", None) or getattr(node, "path_format", None)
        if methods and isinstance(path, str):
            for method in methods:
                declared.add((_route_method_name(method), path))
    return declared


def _dummy_public_dependencies() -> PublicPublishedApiDependencies:
    return PublicPublishedApiDependencies(
        catalog_available=lambda: False,
        is_published=lambda _media_id: False,
        list_media=object(),
        get_media=object(),
        list_published_tags=lambda: (),
        get_metadata=object(),
        resolve_content=object(),
        open_gallery_preview=object(),
        open_cover_thumbnail=object(),
        cover_thumbnail_etag=object(),
    )


def test_route_inventory_is_exact_get_allowlist(public_client) -> None:
    client, _settings = public_client
    router = create_public_published_api_router(_dummy_public_dependencies())
    declared = _collect_declared_routes(router) | _collect_declared_routes(client.app)
    mutating = {
        (method, path)
        for method, path in declared
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert mutating == set()
    named = {
        path
        for method, path in declared
        if method == "GET" and path != "/{full_path:path}"
    }
    assert named == ALLOWED_GET_PATHS


def test_unlisted_routes_and_methods_are_uniform_404(public_client) -> None:
    client, _settings = public_client
    for path in UNLISTED_PATHS:
        _not_found(client.get(path))
        _not_found(client.post(path, json={"probe": True}))
    _not_found(client.post("/api/media", json={"q": "x"}))
    _not_found(client.put(f"/api/media/{GIF_ID}/metadata", json={}))
    _not_found(client.delete("/api/media"))
    _not_found(client.patch("/api/audience/me"))
    _not_found(client.options("/api/media"))


def test_unpublished_and_unknown_are_indistinguishable(public_client) -> None:
    client, _settings = public_client
    unpublished_bodies = []
    unknown_bodies = []
    for media_id, location_id, bucket in (
        (UNPUBLISHED_ID, UNPUB_LOC, unpublished_bodies),
        (UNKNOWN_ID, UNPUB_LOC, unknown_bodies),
    ):
        for path in (
            f"/api/media/{media_id}",
            f"/api/media/{media_id}/metadata",
            f"/api/media/{media_id}/locations/{location_id}/content",
            f"/api/media/{media_id}/locations/{location_id}/gallery-preview",
            f"/api/media/{media_id}/cover-thumbnail",
        ):
            response = client.get(path)
            _not_found(response)
            bucket.append(response.json())
    assert unpublished_bodies == unknown_bodies


def test_spoofed_tailscale_and_mutation_headers_do_not_widen_access(
    public_client,
) -> None:
    client, _settings = public_client
    headers = {
        "Tailscale-User-Login": "admin@example.com",
        "Tailscale-User-Name": "Admin",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "example.ts.net",
        "X-FrameNest-Request": "1",
        "Origin": "https://example.ts.net",
    }
    _not_found(client.get(f"/api/media/{UNPUBLISHED_ID}", headers=headers))
    _not_found(client.get("/api/admin/media", headers=headers))
    _not_found(client.get(f"/api/admin/media/{GIF_ID}/aliases", headers=headers))
    _not_found(client.post("/api/canonical-tags", headers=headers, json={}))
    audience = client.get("/api/audience/me", headers=headers)
    assert audience.status_code == 200
    assert audience.json() == {
        "audience": "public_published",
        "identity": None,
        "capabilities": ["gallery.read", "media.original.read"],
    }


def test_redacted_catalog_and_metadata_omit_internal_fields(public_client) -> None:
    client, _settings = public_client
    listing = client.get("/api/media")
    assert listing.status_code == 200
    payload = listing.json()
    ids = {item["media_id"] for item in payload["items"]}
    assert GIF_ID in ids
    assert MOVIE_ID in ids
    assert UNPUBLISHED_ID not in ids
    for item in payload["items"]:
        assert FORBIDDEN_FIELDS.isdisjoint(item)
        assert SECRET_STABLE_ID not in str(item)
        assert PRIVATE_RELATIVE_PATH not in str(item)
        assert "library_id" not in str(item)
        for location in item["locations"]:
            assert set(location) == {"location_id", "availability"}
    detail = client.get(f"/api/media/{GIF_ID}")
    assert detail.status_code == 200
    body = detail.json()
    assert FORBIDDEN_FIELDS.isdisjoint(body)
    assert body["creator_display_name"] == "Public Creator"
    assert body["creator_handle"] == "public-handle"
    metadata = client.get(f"/api/media/{GIF_ID}/metadata")
    assert metadata.status_code == 200
    meta = metadata.json()
    assert FORBIDDEN_FIELDS.isdisjoint(meta)
    assert SECRET_STABLE_ID not in str(meta)
    tags = client.get("/api/canonical-tags")
    assert tags.status_code == 200
    keys = {tag["key"] for tag in tags.json()["tags"]}
    assert PUBLISHED_TAG in keys
    assert UNUSED_TAG not in keys


def test_published_gif_image_video_movie_and_range_reads(public_client) -> None:
    client, _settings = public_client
    gif = client.get(f"/api/media/{GIF_ID}/locations/{GIF_LOC}/content")
    assert gif.status_code == 200
    assert gif.content.startswith(b"GIF89a")
    image = client.get(f"/api/media/{IMAGE_ID}/locations/{IMAGE_LOC}/content")
    assert image.status_code == 200
    video = client.get(f"/api/media/{VIDEO_ID}/locations/{VIDEO_LOC}/content")
    assert video.status_code == 200
    assert "no-store" in video.headers.get("cache-control", "")
    movie = client.get(f"/api/media/{MOVIE_ID}/locations/{MOVIE_LOC}/content")
    assert movie.status_code == 200
    partial = client.get(
        f"/api/media/{VIDEO_ID}/locations/{VIDEO_LOC}/content",
        headers={"Range": "bytes=0-15"},
    )
    assert partial.status_code == 206
    assert partial.content == bytes(range(16))
    assert partial.headers["content-range"].startswith("bytes 0-15/")
    movie_partial = client.get(
        f"/api/media/{MOVIE_ID}/locations/{MOVIE_LOC}/content",
        headers={"Range": "bytes=0-7"},
    )
    assert movie_partial.status_code == 206
    listing = client.get("/api/media")
    kinds = {
        item["media_id"]: (item["media_kind"], item["content_category"])
        for item in listing.json()["items"]
    }
    assert kinds[MOVIE_ID] == ("video", "movie")


def test_unpublish_stops_visibility_immediately(public_client) -> None:
    client, settings = public_client
    assert client.get(f"/api/media/{GIF_ID}").status_code == 200
    engine = create_sqlite_engine(settings.database_path)
    try:
        SqliteContentPublicationRepository(engine).unpublish(MediaId.from_string(GIF_ID))
    finally:
        dispose_engine(engine)
    _not_found(client.get(f"/api/media/{GIF_ID}"))
    _not_found(client.get(f"/api/media/{GIF_ID}/locations/{GIF_LOC}/content"))
    listing = client.get("/api/media")
    assert GIF_ID not in {item["media_id"] for item in listing.json()["items"]}


def test_fail_closed_startup_on_missing_or_unmigrated_catalog(tmp_path: Path) -> None:
    missing = _settings(tmp_path)
    with pytest.raises(PublicPublishedStartupError):
        create_app(settings=missing)
    empty = _settings(tmp_path / "empty")
    empty.database_path.parent.mkdir(parents=True, exist_ok=True)
    empty.database_path.write_bytes(b"")
    with pytest.raises((PublicPublishedStartupError, FrameNestPersistenceError)):
        create_app(settings=empty)
    behind = _settings(tmp_path / "behind")
    behind.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(behind.database_path)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute("INSERT INTO alembic_version VALUES ('0001')")
    connection.commit()
    connection.close()
    with pytest.raises(PublicPublishedStartupError):
        create_app(settings=behind)


def test_readonly_engine_rejects_writes(tmp_path: Path) -> None:
    settings = KronikaSettings(database_path=tmp_path / "catalog.sqlite3", _env_file=None)
    upgrade_database_to_head(settings)
    engine = create_sqlite_readonly_engine(settings.database_path)
    try:
        with pytest.raises(Exception):
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version(version_num) VALUES ('nope')"
                )
                connection.commit()
        with engine.connect() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
        assert revision == REQUIRED_PUBLIC_SCHEMA_REVISION
    finally:
        dispose_engine(engine)


def test_public_modules_do_not_import_workspace_routers() -> None:
    root = Path(__file__).resolve().parents[2] / "src/kronika/adapters/api"
    sources = [
        (root / "public_published_application.py").read_text(encoding="utf-8"),
        (root / "public_published_api.py").read_text(encoding="utf-8"),
    ]
    combined = "\n".join(sources)
    for marker in WORKSPACE_ROUTER_MARKERS:
        assert marker not in combined
    assert "@router.post" not in combined
    assert "@router.put" not in combined
    assert "@router.delete" not in combined
    assert "@router.patch" not in combined
    assert "docs_url=None" in combined
    assert "alias.team" not in combined
    assert "/aliases" not in combined
    assert "create_team_alias_api_router" not in combined


def test_workspace_tcp_audience_bootstrap_is_trusted_loopback() -> None:
    settings = KronikaSettings(
        database_path=Path("/tmp/framenest-public-audience-tcp.sqlite3"),
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings))
    response = client.get("/api/audience/me")
    assert response.status_code == 200
    payload = response.json()
    assert payload["audience"] == "trusted_loopback"
    assert payload["identity"] is None
    assert "gallery.read" in payload["capabilities"]
    assert "upload.submit" in payload["capabilities"]


def test_non_enumerated_http_exception_catch_all_returns_uniform_404(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    _seed(settings, tmp_path / "library")
    app = create_app(settings=settings)

    def _raise_non_enumerated_status(request: object) -> object:
        del request
        raise StarletteHTTPException(status_code=418)

    app.router.routes.insert(
        len(app.router.routes) - 1,
        Route("/internal-fault-probe", _raise_non_enumerated_status, methods=["GET"]),
    )
    with TestClient(app) as client:
        response = client.get("/internal-fault-probe")
    _not_found(response)


def test_malformed_and_out_of_range_requests_match_uniform_404(public_client) -> None:
    client, _settings = public_client
    reference = client.get("/uniform-404-probe")
    assert reference.status_code == 404
    probes = (
        "/api/media/not-a-uuid",
        "/api/media/11111111-1111-4111-8111-11111111111/metadata",
        f"/api/media/{GIF_ID}/locations/not-a-uuid/content",
        f"/api/media/{GIF_ID}/locations/not-a-uuid/gallery-preview",
        "/api/media?limit=9999",
        "/api/media?limit=0",
        "/api/media?limit=abc",
        "/api/media?offset=-1",
        "/api/media?tag=%FF",
    )
    for path in probes:
        response = client.get(path)
        _not_found(response)
        assert response.content == reference.content
        assert response.headers.get("x-content-type-options") == "nosniff"


def test_public_index_serves_without_companion_script_reference(public_client) -> None:
    client, _settings = public_client
    page = client.get("/")
    assert page.status_code == 200
    assert "companion_host.js" not in page.text


def test_startup_fails_closed_when_companion_marker_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        public_published_api_module, "_read_web_resource", lambda name: b"<html></html>"
    )
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        ingress_mode="public_published_uds",
        uds_path=tmp_path / "public.sock",
        _env_file=None,
    )
    with pytest.raises(PublicPublishedStartupError):
        create_public_published_app(settings)


def test_public_root_fails_loud_when_marker_missing_at_serve_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        public_published_api_module, "_read_web_resource", lambda name: b"<html></html>"
    )
    app = FastAPI()
    app.include_router(create_public_published_api_router(_dummy_public_dependencies()))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/")
    assert response.status_code == 500


class _ExplodingListMedia:
    def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("leak attempt /srv/media/private clip.mp4")


def test_public_read_failure_logs_only_sanitized_error_class(caplog) -> None:
    dependencies = replace(
        _dummy_public_dependencies(),
        catalog_available=lambda: True,
        list_media=_ExplodingListMedia(),
    )
    app = FastAPI()
    app.include_router(create_public_published_api_router(dependencies))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/media")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PUBLIC_READ_FAILED"
    records = [
        record for record in caplog.records if record.name == "framenest.public_published_api"
    ]
    assert len(records) == 1
    record = records[0]
    assert FrameNestRedactionFilter().filter(record) is True
    rendered = FrameNestJsonFormatter().format(record)
    payload = json.loads(rendered)
    assert payload["event"] == "public_read_failed"
    assert payload["exception"] == {"type": "RuntimeError"}
    assert "clip.mp4" not in rendered
    assert "/srv/media" not in rendered
    assert "leak attempt" not in rendered


def test_validation_rejection_logs_no_request_details(public_client, caplog) -> None:
    client, _settings = public_client
    with caplog.at_level("WARNING", logger="framenest.public_published_application"):
        client.get("/api/media/not-a-uuid")
    records = [
        record
        for record in caplog.records
        if record.name == "framenest.public_published_application"
    ]
    assert len(records) == 1
    assert FrameNestRedactionFilter().filter(records[0]) is True
    rendered = FrameNestJsonFormatter().format(records[0])
    payload = json.loads(rendered)
    assert payload["event"] == "public_request_validation_rejected"
    assert payload["level"] == "WARNING"
    assert "not-a-uuid" not in rendered
    assert "media_id" not in rendered
