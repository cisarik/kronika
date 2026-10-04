"""Contract tests for the requester-private X companion meme picker API."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
OWNER_LOGIN = "owner@example.com"
FOREIGN_LOGIN = "foreign@example.com"

PUBLISHED_JPEG = "11111111-1111-4111-8111-111111111111"
PUBLISHED_JPEG_LOC = "21111111-1111-4111-8111-111111111111"
OWNER_PRIVATE = "33333333-3333-4333-8333-333333333333"
OWNER_PRIVATE_LOC = "43333333-3333-4333-8333-333333333333"
FOREIGN_PRIVATE = "55555555-5555-4555-8555-555555555555"
FOREIGN_PRIVATE_LOC = "65555555-5555-4555-8555-555555555555"
GENERAL_VIDEO = "77777777-7777-4777-8777-777777777777"
GENERAL_VIDEO_LOC = "87777777-7777-4777-8777-777777777777"
CLAIM_OWNER = "99999999-9999-4999-8999-999999999999"
CLAIM_FOREIGN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ASSET_OWNER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ASSET_FOREIGN = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _serve_headers(login: str, name: str = "User") -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": name,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


@pytest.fixture
def companion_api_client(tmp_path: Path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "published.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
    (library_root / "owner-private.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 32)
    (library_root / "foreign-private.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x02" * 32)
    (library_root / "movie.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x03" * 32)
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={
            ADMIN_LOGIN: "admin",
            OWNER_LOGIN: "user",
            FOREIGN_LOGIN: "user",
        },
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed(settings.database_path, library_root)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client


def _seed(database_path: Path, library_root: Path) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        device = Device(id=DeviceId.new(), display_name="Companion Device")
        SqliteDeviceRepository(engine).add(device)
        library_id = LibraryId.new()
        flavor = (
            LibraryPathFlavor.WINDOWS if os.name == "nt" else LibraryPathFlavor.POSIX
        )
        SqliteLibraryRepository(engine).add(
            Library(
                id=library_id,
                device_id=device.id,
                display_name="Companion Library",
                root=LibraryRoot(flavor=flavor, path=os.path.normpath(str(library_root))),
            )
        )
        library = library_id.to_string()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) VALUES "
                    "(:jpeg, 'image', 300, 300), "
                    "(:owner, 'video', 200, 200), "
                    "(:foreign, 'video', 100, 100), "
                    "(:general, 'video', 400, 400)"
                ),
                {
                    "jpeg": PUBLISHED_JPEG,
                    "owner": OWNER_PRIVATE,
                    "foreign": FOREIGN_PRIVATE,
                    "general": GENERAL_VIDEO,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_metadata "
                    "(media_id, display_title, description, created_at_ms, updated_at_ms, "
                    "content_category, acquisition_source) VALUES "
                    "(:jpeg, 'Published JPEG meme', 'A still', 300, 300, 'meme', 'manual_upload'), "
                    "(:owner, 'Owner private X meme', 'Private', 200, 200, 'meme', 'x_manual_claim'), "
                    "(:foreign, 'Foreign private X meme', 'Secret', 100, 100, 'meme', 'x_manual_claim'), "
                    "(:general, 'A movie', 'Not a meme', 400, 400, 'general', 'manual_upload')"
                ),
                {
                    "jpeg": PUBLISHED_JPEG,
                    "owner": OWNER_PRIVATE,
                    "foreign": FOREIGN_PRIVATE,
                    "general": GENERAL_VIDEO,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations "
                    "(id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
                    "VALUES "
                    "(:jpeg_loc, :jpeg, :library, 'published.jpg', 'available', 19, 1, 1, 1), "
                    "(:owner_loc, :owner, :library, 'owner-private.mp4', 'available', 50, 1, 1, 1), "
                    "(:foreign_loc, :foreign, :library, 'foreign-private.mp4', 'available', 50, 1, 1, 1), "
                    "(:general_loc, :general, :library, 'movie.mp4', 'available', 50, 1, 1, 1)"
                ),
                {
                    "jpeg_loc": PUBLISHED_JPEG_LOC,
                    "jpeg": PUBLISHED_JPEG,
                    "owner_loc": OWNER_PRIVATE_LOC,
                    "owner": OWNER_PRIVATE,
                    "foreign_loc": FOREIGN_PRIVATE_LOC,
                    "foreign": FOREIGN_PRIVATE,
                    "general_loc": GENERAL_VIDEO_LOC,
                    "general": GENERAL_VIDEO,
                    "library": library,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_content_publications "
                    "(media_id, published_at_ms, publication_origin) VALUES "
                    "(:jpeg, 1, 'legacy_backfill'), (:general, 1, 'legacy_backfill')"
                ),
                {"jpeg": PUBLISHED_JPEG, "general": GENERAL_VIDEO},
            )
            _insert_x_claim(
                connection,
                claim_id=CLAIM_OWNER,
                asset_id=ASSET_OWNER,
                media_id=OWNER_PRIVATE,
                location_id=OWNER_PRIVATE_LOC,
                owner=OWNER_LOGIN,
                post_id="1111111111111111111",
                stage_key="a" * 32,
            )
            _insert_x_claim(
                connection,
                claim_id=CLAIM_FOREIGN,
                asset_id=ASSET_FOREIGN,
                media_id=FOREIGN_PRIVATE,
                location_id=FOREIGN_PRIVATE_LOC,
                owner=FOREIGN_LOGIN,
                post_id="2222222222222222222",
                stage_key="b" * 32,
            )
    finally:
        dispose_engine(engine)


def _insert_x_claim(
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


def test_owner_sees_published_jpeg_and_own_private_x_meme(
    companion_api_client,
) -> None:
    client = companion_api_client
    response = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(OWNER_LOGIN, "Owner"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["companion_api_version"] == "framenest-companion.v1"
    ids = [item["media_id"] for item in payload["items"]]
    assert ids == [PUBLISHED_JPEG, OWNER_PRIVATE]
    assert payload["items"][0]["location"]["media_type"] == "image/jpeg"
    assert payload["items"][1]["location"]["location_id"] == OWNER_PRIVATE_LOC
    assert FOREIGN_PRIVATE not in ids
    assert GENERAL_VIDEO not in ids
    assert "relative_path" not in payload["items"][0]
    assert "owner-private.mp4" not in response.text
    assert FOREIGN_LOGIN not in response.text


def test_foreign_user_cannot_enumerate_another_requesters_private_meme(
    companion_api_client,
) -> None:
    client = companion_api_client
    response = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
    )
    assert response.status_code == 200
    ids = [item["media_id"] for item in response.json()["items"]]
    assert ids == [PUBLISHED_JPEG, FOREIGN_PRIVATE]
    assert OWNER_PRIVATE not in ids
    owner_detail = client.get(
        f"/api/media/{OWNER_PRIVATE}",
        headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
    )
    unknown = client.get(
        f"/api/media/{uuid.uuid4()}",
        headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
    )
    assert owner_detail.status_code == unknown.status_code == 404


def test_kind_filter_and_cursor_pagination_are_stable(companion_api_client) -> None:
    client = companion_api_client
    images = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(OWNER_LOGIN, "Owner"),
        params={"kind": "image", "limit": 1},
    )
    assert images.status_code == 200
    payload = images.json()
    assert [item["media_id"] for item in payload["items"]] == [PUBLISHED_JPEG]
    first_page = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(OWNER_LOGIN, "Owner"),
        params={"limit": 1},
    )
    assert first_page.status_code == 200
    assert first_page.json()["items"][0]["media_id"] == PUBLISHED_JPEG
    cursor = first_page.json()["next_cursor"]
    assert cursor
    second_page = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(OWNER_LOGIN, "Owner"),
        params={"limit": 1, "cursor": cursor},
    )
    assert second_page.status_code == 200
    assert [item["media_id"] for item in second_page.json()["items"]] == [OWNER_PRIVATE]


def test_owner_query_parameter_cannot_name_another_requester(
    companion_api_client,
) -> None:
    client = companion_api_client
    response = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
        params={"owner": OWNER_LOGIN, "created_by": OWNER_LOGIN},
    )
    assert response.status_code == 200
    ids = [item["media_id"] for item in response.json()["items"]]
    assert OWNER_PRIVATE not in ids
