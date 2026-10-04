"""Contributor-scoped workspace media list and upload audience extension."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY, find_route_policy
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_MEDIA_WORKSPACE_READ,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

ALICE = "alice@example.com"
BOB = "bob@example.com"
ADMIN = "admin@example.com"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100

ALICE_UPLOAD = "11111111-1111-4111-8111-111111111111"
ALICE_YT = "22222222-2222-4222-8222-222222222222"
ALICE_X = "33333333-3333-4333-8333-333333333333"
BOB_UPLOAD = "44444444-4444-4444-8444-444444444444"
MULTI = "55555555-5555-4555-8555-555555555555"
PUBLISHED = "66666666-6666-4666-8666-666666666666"
UNATTRIBUTED = "77777777-7777-4777-8777-777777777777"
UNKNOWN = "88888888-8888-4888-8888-888888888888"

ALICE_UPLOAD_LOC = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ALICE_YT_LOC = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ALICE_X_LOC = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BOB_UPLOAD_LOC = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
MULTI_LOC = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
PUBLISHED_LOC = "ffffffff-ffff-4fff-8fff-ffffffffffff"
UNATTRIBUTED_LOC = "99999999-9999-4999-8999-999999999999"


def _identity(login: str, role: str, *, capabilities: frozenset[str] | None = None) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login.split("@", 1)[0].title(),
        role=role,
        capabilities=capabilities if capabilities is not None else CAPABILITIES_BY_ROLE[role],
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
        device = Device(id=DeviceId.new(), display_name="Workspace Device")
        SqliteDeviceRepository(engine).add(device)
        library = Library(
            id=library_id,
            device_id=device.id,
            display_name="Workspace Library",
            root=LibraryRoot(
                flavor=_native_flavor(),
                path=os.path.normpath(str(library_root)),
            ),
        )
        SqliteLibraryRepository(engine).add(library)
        return library_id
    finally:
        dispose_engine(engine)


def _client(settings: KronikaSettings, login: str, role: str, **identity_kwargs: object) -> TestClient:
    app = create_app(settings=settings)

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(login, role, **identity_kwargs)
        return await call_next(request)

    return TestClient(app)


def _plain_client(settings: KronikaSettings) -> TestClient:
    return TestClient(create_app(settings=settings))


def _insert_media(
    connection,
    *,
    media_id: str,
    location_id: str,
    library_id: str,
    relative: str,
    title: str | None,
    created_at_ms: int,
    published: bool = False,
    description: str | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (:id, 'video', :created, :created)"
        ),
        {"id": media_id, "created": created_at_ms},
    )
    connection.execute(
        text(
            "INSERT INTO media_metadata "
            "(media_id, display_title, description, collection_key, "
            "processed_at_ms, created_at_ms, updated_at_ms, "
            "content_category, acquisition_source) "
            "VALUES (:media_id, :title, :description, NULL, NULL, :created, :created, "
            "'general', 'unknown')"
        ),
        {
            "media_id": media_id,
            "title": title,
            "description": description if description is not None else (
                None if title is None else "Workspace description"
            ),
            "created": created_at_ms,
        },
    )
    connection.execute(
        text(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (:id, :media_id, :library_id, :relative, 'available', "
            ":size, 1, :created, :created)"
        ),
        {
            "id": location_id,
            "media_id": media_id,
            "library_id": library_id,
            "relative": relative,
            "size": len(MP4_BYTES),
            "created": created_at_ms,
        },
    )
    if published:
        connection.execute(
            text(
                "INSERT INTO media_content_publications "
                "(media_id, published_at_ms, publication_origin) "
                "VALUES (:id, :created, 'admin_explicit')"
            ),
            {"id": media_id, "created": created_at_ms},
        )


def _insert_upload(
    connection,
    *,
    upload_id: str,
    publication_id: str,
    byte_id: str,
    media_id: str,
    location_id: str,
    library_id: str,
    owner: str,
    storage_key: str,
    digest: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO media_byte_identities "
            "(id, checksum_algorithm, size_bytes, checksum_hex, created_at_ms) "
            "VALUES (:id, 'sha256', :size, :digest, 10)"
        ),
        {"id": byte_id, "size": len(MP4_BYTES), "digest": digest},
    )
    connection.execute(
        text(
            "INSERT INTO upload_sessions "
            "(id, state, storage_key, display_filename, declared_size_bytes, "
            "received_size_bytes, checksum_algorithm, checksum_hex, "
            "validated_media_kind, validated_format, byte_identity_id, "
            "duplicate_disposition, created_by_login_key, created_at_ms, "
            "updated_at_ms, expires_at_ms, failure_code, version) VALUES "
            "(:id, 'cataloged', :storage_key, 'clip.mp4', :size, :size, "
            "'sha256', :digest, 'video', 'mp4', :byte_id, NULL, :owner, "
            "10, 20, 100, NULL, 5)"
        ),
        {
            "id": upload_id,
            "storage_key": storage_key,
            "size": len(MP4_BYTES),
            "digest": digest,
            "byte_id": byte_id,
            "owner": owner,
        },
    )
    relative = f"{publication_id.replace('-', '')}.mp4"
    connection.execute(
        text(
            "INSERT INTO upload_publications "
            "(upload_id, publication_id, destination_id, relative_target, "
            "byte_identity_id, expected_size_bytes, checksum_algorithm, "
            "checksum_hex, validated_media_kind, validated_format, state, "
            "cleanup_state, created_at_ms, updated_at_ms, verified_at_ms, "
            "cleanup_completed_at_ms, version, media_id, media_location_id) "
            "VALUES (:upload_id, :publication_id, :destination_id, :relative, "
            ":byte_id, :size, 'sha256', :digest, 'video', 'mp4', 'verified', "
            "'complete', 20, 20, 20, 20, 1, :media_id, :location_id)"
        ),
        {
            "upload_id": upload_id,
            "publication_id": publication_id,
            "destination_id": library_id,
            "relative": relative,
            "byte_id": byte_id,
            "size": len(MP4_BYTES),
            "digest": digest,
            "media_id": media_id,
            "location_id": location_id,
        },
    )


def _insert_youtube(
    connection,
    *,
    claim_id: str,
    media_id: str,
    location_id: str,
    owner: str,
    video_id: str,
    staging_key: str,
) -> None:
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
            "'Upstream', 'Channel', 'channel', '2026-01-02', 'yt-dlp', "
            "'2026.07.23', '2026.07.23', '18', NULL, 'remote.mp4', "
            ":generated, :staging, :size, 10, 20, NULL, 20, NULL, NULL, NULL, "
            "'complete', 20, 1, :owner)"
        ),
        {
            "id": claim_id,
            "submitted": f"https://youtu.be/{video_id}",
            "canonical": f"https://www.youtube.com/watch?v={video_id}",
            "video_id": video_id,
            "media_id": media_id,
            "location_id": location_id,
            "generated": f"youtube-{video_id}.mp4",
            "staging": staging_key,
            "size": len(MP4_BYTES),
            "owner": owner,
        },
    )


def _insert_x(
    connection,
    *,
    claim_id: str,
    asset_id: str,
    media_id: str,
    location_id: str,
    owner: str,
    post_id: str,
    stage_key: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO x_post_claims ("
            "id, state, acquisition_source, submitted_url, canonical_url, "
            "x_post_id, extractor_key, created_by_login_key, "
            "discovered_asset_count, success_count, failure_count, "
            "created_at_ms, updated_at_ms, completed_at_ms, cleanup_state, "
            "cleanup_completed_at_ms, version"
            ") VALUES ("
            ":id, 'completed', 'x_manual_claim', :url, :url, :post_id, 'X', "
            ":owner, 1, 1, 0, 10, 20, 20, 'complete', 20, 1)"
        ),
        {
            "id": claim_id,
            "url": f"https://x.com/a/status/{post_id}",
            "post_id": post_id,
            "owner": owner,
        },
    )
    connection.execute(
        text(
            "INSERT INTO x_assets ("
            "id, claim_id, ordinal, media_type, expected_mime, state, stage_key, "
            "media_id, media_location_id, created_at_ms, updated_at_ms, "
            "completed_at_ms, cleanup_state, cleanup_completed_at_ms, version"
            ") VALUES ("
            ":id, :claim_id, 0, 'video', 'video/mp4', 'cataloged', :stage, "
            ":media_id, :location_id, 10, 20, 20, 'complete', 20, 1)"
        ),
        {
            "id": asset_id,
            "claim_id": claim_id,
            "stage": stage_key,
            "media_id": media_id,
            "location_id": location_id,
        },
    )


def _seed(database_path: Path, library_id: LibraryId) -> None:
    engine = create_sqlite_engine(database_path)
    library = library_id.to_string()
    try:
        with engine.begin() as connection:
            _insert_media(
                connection,
                media_id=ALICE_UPLOAD,
                location_id=ALICE_UPLOAD_LOC,
                library_id=library,
                relative="alice-upload.mp4",
                title="Alice Upload",
                created_at_ms=50,
            )
            _insert_upload(
                connection,
                upload_id="a1111111-1111-4111-8111-111111111111",
                publication_id="a2222222-2222-4222-8222-222222222222",
                byte_id="a3333333-3333-4333-8333-333333333333",
                media_id=ALICE_UPLOAD,
                location_id=ALICE_UPLOAD_LOC,
                library_id=library,
                owner=ALICE,
                storage_key="synthetic-upload-alice",
                digest="aa" * 32,
            )
            _insert_media(
                connection,
                media_id=ALICE_YT,
                location_id=ALICE_YT_LOC,
                library_id=library,
                relative="alice-yt.mp4",
                title="Alice YouTube",
                created_at_ms=40,
            )
            _insert_youtube(
                connection,
                claim_id="b1111111-1111-4111-8111-111111111111",
                media_id=ALICE_YT,
                location_id=ALICE_YT_LOC,
                owner=ALICE,
                video_id="AliceYt0001",
                staging_key="a" * 32,
            )
            _insert_media(
                connection,
                media_id=ALICE_X,
                location_id=ALICE_X_LOC,
                library_id=library,
                relative="alice-x.mp4",
                title="Alice X",
                created_at_ms=30,
            )
            _insert_x(
                connection,
                claim_id="c1111111-1111-4111-8111-111111111111",
                asset_id="c2222222-2222-4222-8222-222222222222",
                media_id=ALICE_X,
                location_id=ALICE_X_LOC,
                owner=ALICE,
                post_id="12345678901",
                stage_key="b" * 32,
            )
            _insert_media(
                connection,
                media_id=BOB_UPLOAD,
                location_id=BOB_UPLOAD_LOC,
                library_id=library,
                relative="bob-upload.mp4",
                title="Bob Upload",
                created_at_ms=20,
            )
            _insert_upload(
                connection,
                upload_id="d1111111-1111-4111-8111-111111111111",
                publication_id="d2222222-2222-4222-8222-222222222222",
                byte_id="d3333333-3333-4333-8333-333333333333",
                media_id=BOB_UPLOAD,
                location_id=BOB_UPLOAD_LOC,
                library_id=library,
                owner=BOB,
                storage_key="synthetic-upload-bob",
                digest="bb" * 32,
            )
            _insert_media(
                connection,
                media_id=MULTI,
                location_id=MULTI_LOC,
                library_id=library,
                relative="multi.mp4",
                title="Shared Contribution",
                created_at_ms=60,
            )
            _insert_upload(
                connection,
                upload_id="e1111111-1111-4111-8111-111111111111",
                publication_id="e2222222-2222-4222-8222-222222222222",
                byte_id="e3333333-3333-4333-8333-333333333333",
                media_id=MULTI,
                location_id=MULTI_LOC,
                library_id=library,
                owner=ALICE,
                storage_key="synthetic-upload-multi",
                digest="cc" * 32,
            )
            _insert_youtube(
                connection,
                claim_id="e4444444-4444-4444-8444-444444444444",
                media_id=MULTI,
                location_id=MULTI_LOC,
                owner=BOB,
                video_id="BobTube0001",
                staging_key="c" * 32,
            )
            _insert_media(
                connection,
                media_id=PUBLISHED,
                location_id=PUBLISHED_LOC,
                library_id=library,
                relative="published.mp4",
                title="Published Item",
                created_at_ms=10,
                published=True,
            )
            _insert_media(
                connection,
                media_id=UNATTRIBUTED,
                location_id=UNATTRIBUTED_LOC,
                library_id=library,
                relative="unattributed.mp4",
                title="Unattributed",
                created_at_ms=5,
            )
    finally:
        dispose_engine(engine)


def _prepare(tmp_path: Path) -> KronikaSettings:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    library_root = tmp_path / "library"
    library_root.mkdir()
    for name in (
        "alice-upload.mp4",
        "alice-yt.mp4",
        "alice-x.mp4",
        "bob-upload.mp4",
        "multi.mp4",
        "published.mp4",
        "unattributed.mp4",
    ):
        (library_root / name).write_bytes(MP4_BYTES)
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)
    _seed(database_path, library_id)
    return settings


def _denied_matches_unknown(client: TestClient, media_id: str, location_id: str) -> None:
    routes = (
        f"/api/media/{media_id}",
        f"/api/media/{media_id}/metadata",
        f"/api/media/{media_id}/locations/{location_id}/content",
        f"/api/media/{media_id}/locations/{location_id}/gallery-preview",
        f"/api/media/{media_id}/cover-thumbnail",
    )
    unknown_routes = (
        f"/api/media/{UNKNOWN}",
        f"/api/media/{UNKNOWN}/metadata",
        f"/api/media/{UNKNOWN}/locations/{location_id}/content",
        f"/api/media/{UNKNOWN}/locations/{location_id}/gallery-preview",
        f"/api/media/{UNKNOWN}/cover-thumbnail",
    )
    for denied_path, unknown_path in zip(routes, unknown_routes, strict=True):
        denied = client.get(denied_path)
        unknown = client.get(unknown_path)
        assert denied.status_code == unknown.status_code == 404, denied_path
        assert denied.json() == unknown.json()
        assert "Bob Upload" not in denied.text
        assert BOB not in denied.text


def test_workspace_list_requires_workspace_read_capability_on_trusted_ingress() -> None:
    policy, match = find_route_policy("GET", "/api/workspace/media")
    assert match is not None
    assert policy.capability == CAPABILITY_MEDIA_WORKSPACE_READ


def test_ordinary_user_lists_own_attributed_media_and_reads_own_bytes(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    with _client(settings, ALICE, ROLE_USER) as alice:
        gallery = alice.get("/api/media")
        assert gallery.status_code == 200
        assert [item["media_id"] for item in gallery.json()["items"]] == [PUBLISHED]

        workspace = alice.get("/api/workspace/media")
        assert workspace.status_code == 200
        ids = [item["media_id"] for item in workspace.json()["items"]]
        assert ids == [MULTI, ALICE_UPLOAD, ALICE_YT, ALICE_X]
        assert BOB_UPLOAD not in ids
        assert UNATTRIBUTED not in ids
        by_id = {item["media_id"]: item for item in workspace.json()["items"]}
        assert by_id[ALICE_UPLOAD]["content_publication_state"] == "unpublished"
        assert by_id[ALICE_UPLOAD]["contribution_sources"] == ["upload"]
        assert by_id[ALICE_YT]["contribution_sources"] == ["youtube"]
        assert by_id[ALICE_X]["contribution_sources"] == ["x"]
        assert by_id[MULTI]["contribution_sources"] == ["upload"]
        assert by_id[ALICE_UPLOAD]["missing_fields"] == ["tags"]
        assert by_id[ALICE_UPLOAD]["publication_ready"] is False

        content = alice.get(
            f"/api/media/{ALICE_UPLOAD}/locations/{ALICE_UPLOAD_LOC}/content"
        )
        detail = alice.get(f"/api/media/{ALICE_UPLOAD}")
        assert content.status_code == 200
        assert content.content == MP4_BYTES
        assert detail.status_code == 200
        assert detail.json()["display_title"] == "Alice Upload"

        published = alice.get(f"/api/media/{PUBLISHED}")
        assert published.status_code == 200
        assert published.json()["display_title"] == "Published Item"

    with _client(settings, BOB, ROLE_USER) as bob:
        workspace = bob.get("/api/workspace/media")
        ids = [item["media_id"] for item in workspace.json()["items"]]
        assert MULTI in ids
        assert BOB_UPLOAD in ids
        assert ALICE_UPLOAD not in ids
        by_id = {item["media_id"]: item for item in workspace.json()["items"]}
        assert by_id[MULTI]["contribution_sources"] == ["youtube"]
        gallery = bob.get("/api/media")
        assert [item["media_id"] for item in gallery.json()["items"]] == [PUBLISHED]
        _denied_matches_unknown(bob, ALICE_UPLOAD, ALICE_UPLOAD_LOC)
        published = bob.get(f"/api/media/{PUBLISHED}")
        assert published.status_code == 200

    with _client(settings, ADMIN, ROLE_ADMIN) as admin:
        gallery = admin.get("/api/media")
        assert [item["media_id"] for item in gallery.json()["items"]] == [PUBLISHED]
        default_page = admin.get("/api/admin/media")
        assert default_page.status_code == 200
        default_ids = [item["media_id"] for item in default_page.json()["items"]]
        assert UNATTRIBUTED in default_ids
        assert PUBLISHED not in default_ids
        assert default_page.json()["contributor"] is None
        all_page = admin.get("/api/admin/media", params={"publication": "all"})
        all_ids = [item["media_id"] for item in all_page.json()["items"]]
        assert UNATTRIBUTED in all_ids
        assert PUBLISHED in all_ids
        alice_filter = admin.get(
            "/api/admin/media",
            params={"publication": "all", "contributor": " Alice@Example.COM "},
        )
        assert alice_filter.status_code == 200
        assert alice_filter.json()["contributor"] == ALICE
        alice_ids = [item["media_id"] for item in alice_filter.json()["items"]]
        assert set(alice_ids) == {ALICE_UPLOAD, ALICE_YT, ALICE_X, MULTI}
        assert UNATTRIBUTED not in alice_ids
        contributors = {
            item["media_id"]: item["contributors"]
            for item in all_page.json()["items"]
            if item["media_id"] == MULTI
        }
        multi = contributors[MULTI]
        logins = {entry["login_key"]: entry["sources"] for entry in multi}
        assert logins[ALICE] == ["upload"]
        assert logins[BOB] == ["youtube"]
        invalid = admin.get("/api/admin/media", params={"contributor": "bad login"})
        assert invalid.status_code == 422

    denied_caps = CAPABILITIES_BY_ROLE[ROLE_USER] - {CAPABILITY_MEDIA_WORKSPACE_READ}
    with _client(
        settings,
        ALICE,
        ROLE_USER,
        capabilities=denied_caps,
    ) as stripped:
        denied = stripped.get("/api/workspace/media")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "CAPABILITY_DENIED"

    with _plain_client(settings) as anonymous:
        missing = anonymous.get("/api/workspace/media")
        assert missing.status_code == 401


def test_workspace_attribution_modules_are_read_only() -> None:
    root = Path(__file__).resolve().parents[2] / "src/framenest"
    sources = [
        (root / "infrastructure/persistence/media_attribution_repository.py").read_text(
            encoding="utf-8"
        ),
        (root / "application/workspace_media.py").read_text(encoding="utf-8"),
        (root / "adapters/api/workspace_media_api.py").read_text(encoding="utf-8"),
    ]
    combined = "\n".join(sources)
    for needle in (
        "insert(upload_sessions)",
        "insert(youtube_acquisition_claims)",
        "insert(x_post_claims)",
        "update(upload_sessions)",
        "update(youtube_acquisition_claims)",
        "update(x_post_claims)",
        "created_by_login_key=",
    ):
        assert needle not in combined
