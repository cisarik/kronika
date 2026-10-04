"""Ingress-level security and publication-visibility contract for covers."""

from __future__ import annotations

import io
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kronika.adapters.api.application import create_app
from kronika.application.media_analysis import build_representative_frame
from kronika.configuration import KronikaSettings
from kronika.infrastructure.filesystem.cover_storage import (
    FilesystemCoverThumbnailCache,
    FilesystemDurableCoverStorage,
    PillowCoverEncoder,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_LOGIN = "user@example.com"

PUBLISHED_ID = "11111111-1111-4111-8111-111111111111"
UNPUBLISHED_ID = "22222222-2222-4222-8222-222222222222"
LOCATION_ID = "33333333-3333-4333-8333-333333333333"
LIBRARY_ID = "44444444-4444-4444-8444-444444444444"
DEVICE_ID = "55555555-5555-4555-8555-555555555555"


def _serve_headers(login: str = ADMIN_LOGIN) -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": "Cover User",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


def _mutation_headers(login: str = ADMIN_LOGIN) -> dict[str, str]:
    return {
        **_serve_headers(login),
        "Origin": EXTERNAL_ORIGIN,
        "X-FrameNest-Request": "1",
    }


def _frame_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (128, 72), (5, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _seed(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executemany(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) VALUES (?, 'video', ?, ?)",
            (
                (PUBLISHED_ID, 1, 1),
                (UNPUBLISHED_ID, 2, 2),
            ),
        )
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'device')",
            (DEVICE_ID,),
        )
        connection.execute(
            "INSERT INTO libraries "
            "(id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, 'l', 'posix', '/media/movies')",
            (LIBRARY_ID, DEVICE_ID),
        )
        connection.execute(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            " observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, 'clip.mp4', 'available', 100, 1, 1, 1)",
            (LOCATION_ID, PUBLISHED_ID, LIBRARY_ID),
        )
        connection.execute(
            "INSERT INTO media_content_publications "
            "(media_id, published_at_ms, publication_origin) "
            "VALUES (?, 10, 'admin_explicit')",
            (PUBLISHED_ID,),
        )
        connection.commit()
    finally:
        connection.close()


def _publish_cover_for_media(
    settings: KronikaSettings,
    media_id: str,
    *,
    source_location_id: str | None,
) -> str:
    storage = FilesystemDurableCoverStorage(settings.cover_storage_root)
    thumbnail_cache = FilesystemCoverThumbnailCache(settings.cover_thumbnail_cache_path)
    encoder = PillowCoverEncoder()
    from kronika.domain.identities import MediaId

    frame = build_representative_frame(timestamp_ms=500, payload=_frame_png())
    artifact = encoder.encode_artifact_frame(frame)
    media_id_obj = MediaId.from_string(media_id)
    storage.publish(media_id=media_id_obj, artifact=artifact)
    thumbnail = encoder.encode_thumbnail(artifact.payload)
    key = thumbnail_cache.key_for(media_id=media_id_obj, artifact_digest=artifact.digest)
    thumbnail_cache.publish(key, thumbnail)

    connection = sqlite3.connect(settings.database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "INSERT INTO media_covers ("
            " media_id, source_location_id, source_reference, source_kind, "
            " source_timestamp_ms, source_size_bytes, source_mtime_ns, "
            " source_duration_ms, source_observation_version, source_observation_digest, "
            " artifact_profile, artifact_media_type, artifact_digest, artifact_width, "
            " artifact_height, artifact_byte_size, revision, accepted_at_ms) "
            "VALUES (?, ?, ?, 'mp4', 500, 100, 1, 1000, "
            " 'cover-source-observation-v1', ?, 'durable-cover-jpeg-v1', "
            " 'image/jpeg', ?, ?, ?, ?, 1, 100)",
            (
                media_id,
                source_location_id,
                f"location:{source_location_id}"
                if source_location_id
                else f"location:{uuid.uuid4()}",
                "a" * 64,
                artifact.digest,
                artifact.width,
                artifact.height,
                artifact.byte_size,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return artifact.digest


@pytest.fixture
def cover_client(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={ADMIN_LOGIN: "admin", USER_LOGIN: "user"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed(settings.database_path)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client, settings


def _audit_rows(database_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT capability, action, target_type, target_id, outcome, http_status"
            " FROM security_audit_events ORDER BY occurred_at_ms, id"
        ).fetchall()
    finally:
        connection.close()


def test_cover_route_capabilities_are_enforced_server_side(cover_client) -> None:
    client, settings = cover_client
    timeline = f"/api/media/{PUBLISHED_ID}/locations/{LOCATION_ID}/cover-timeline"
    state = f"/api/admin/media/{PUBLISHED_ID}/cover"
    thumb = f"/api/media/{PUBLISHED_ID}/cover-thumbnail"

    assert client.get(timeline, headers=_serve_headers(USER_LOGIN)).status_code == 403
    assert client.get(state, headers=_serve_headers(USER_LOGIN)).status_code == 403
    # An administrator passes capability enforcement; without a real source file
    # behind the registered root the server still fails closed with a sanitized
    # source-unavailable result instead of leaking internals.
    admin_timeline = client.get(timeline, headers=_serve_headers())
    assert admin_timeline.status_code == 409
    assert admin_timeline.json()["error"]["code"] == "COVER_SOURCE_UNAVAILABLE"
    assert client.get(state, headers=_serve_headers()).status_code == 200

    # Ordinary read of a cover thumbnail is allowed by the gallery.read
    # capability and must also pass publication visibility for the published item.
    _publish_cover_for_media(settings, PUBLISHED_ID, source_location_id=LOCATION_ID)
    ordinary_thumb = client.get(thumb, headers=_serve_headers(USER_LOGIN))
    assert ordinary_thumb.status_code == 200
    assert ordinary_thumb.headers["content-type"] == "image/jpeg"
    assert "path" not in ordinary_thumb.text.lower()


def test_accept_is_audited_before_mutation_even_when_extraction_fails(cover_client) -> None:
    client, settings = cover_client
    path = f"/api/media/{PUBLISHED_ID}/locations/{LOCATION_ID}/cover"
    headers = _mutation_headers(ADMIN_LOGIN)
    response = client.put(
        path,
        headers=headers,
        json={
            "timestamp_ms": 100,
            "expected_revision": 0,
            "expected_source_version": "0" * 64,
        },
    )
    # Source version cannot match because no real media file exists behind the
    # registered root; the server fails closed before any mutation.
    assert response.status_code == 409
    rows = _audit_rows(settings.database_path)
    cover_events = [row for row in rows if row["action"] == "media.cover_set"]
    assert len(cover_events) == 1
    assert cover_events[0]["capability"] == "metadata.canonical.write"
    assert cover_events[0]["target_type"] == "media"
    assert cover_events[0]["target_id"] == PUBLISHED_ID
    assert cover_events[0]["outcome"] == "allowed"
    assert cover_events[0]["http_status"] == 409


def test_unpublished_cover_bytes_are_concealed_from_ordinary_users(cover_client) -> None:
    client, settings = cover_client
    _publish_cover_for_media(settings, UNPUBLISHED_ID, source_location_id=None)
    thumb = f"/api/media/{UNPUBLISHED_ID}/cover-thumbnail"

    ordinary = client.get(thumb, headers=_serve_headers(USER_LOGIN))
    assert ordinary.status_code == 404
    assert ordinary.json()["error"]["code"] == "COVER_MEDIA_NOT_FOUND"

    # An administrator with workflow visibility may inspect the same media.
    admin = client.get(thumb, headers=_serve_headers(ADMIN_LOGIN))
    assert admin.status_code == 200
    assert admin.headers["etag"].startswith('"')
    assert admin.headers["content-type"] == "image/jpeg"


def test_cover_route_policies_are_bound_to_capabilities() -> None:
    from kronika.adapters.api.tailscale_ingress import find_route_policy

    for method, path in (
        ("GET", f"/api/media/{PUBLISHED_ID}/locations/{LOCATION_ID}/cover-timeline"),
        ("GET", f"/api/media/{PUBLISHED_ID}/locations/{LOCATION_ID}/cover-frame"),
        ("PUT", f"/api/media/{PUBLISHED_ID}/locations/{LOCATION_ID}/cover"),
        ("GET", f"/api/media/{PUBLISHED_ID}/cover-thumbnail"),
        ("GET", f"/api/admin/media/{PUBLISHED_ID}/cover"),
    ):
        policy, match = find_route_policy(method, path)
        assert match is not None, (method, path)
        if method == "GET" and "/cover-thumbnail" in path:
            assert policy.capability == "gallery.read"
        elif method == "PUT":
            assert policy.capability == "metadata.canonical.write"
            assert policy.audit_action == "media.cover_set"
        else:
            assert policy.capability == "metadata.canonical.write"
