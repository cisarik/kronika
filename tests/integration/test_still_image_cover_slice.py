"""End-to-end still-image upload, publication, and durable manual cover roundtrip.

Proves the complete still-image journey: browser-restricted upload acquisition,
quarantine/validation/publication/catalog lifecycle, timeless cover authoring
with no fabricated temporal contract, durable JPEG artifact and thumbnail
delivery, idempotency, optimistic-concurrency fencing, source-change detection,
replacement under the correct revision, ordinary-user denial, and original-media
integrity. The still-image path is Pillow-only and never invokes ffmpeg/ffprobe
or any AI provider.
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import insert

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import LibraryId
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.infrastructure.persistence.catalog_schema import devices, libraries
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DESTINATION_ID = LibraryId(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))


def _identity(role: str) -> IdentityContext:
    return IdentityContext(
        login=f"{role}@example.com",
        login_key=f"{role}@example.com",
        display_name=role.title(),
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="test",
    )


def _app(settings: KronikaSettings, role: str):
    app = create_app(settings=settings)

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(role)
        request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-event"
        return await call_next(request)

    return app


def _jpeg_payload(
    *,
    size: tuple[int, int] = (320, 200),
    color: tuple[int, int, int] = (11, 22, 33),
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


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


def _upload_and_catalog(client: TestClient, payload: bytes, filename: str) -> str:
    created = client.post(
        "/api/uploads",
        json={
            "display_filename": filename,
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
    for _ in range(400):
        status_response = client.get(f"/api/uploads/{upload_id}")
        assert status_response.status_code == 200
        status = status_response.json()
        if status["state"] == "cataloged":
            break
        time.sleep(0.01)
    assert status["state"] == "cataloged"
    return status["media_id"]


def test_still_image_cover_roundtrip_admin_and_ordinary_user(tmp_path: Path) -> None:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    quarantine_root = tmp_path / "quarantine"
    published_root = tmp_path / "published"
    cache_root = tmp_path / "cache"
    cover_root = tmp_path / "covers"
    thumbnail_root = tmp_path / "thumbnails"
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
        cover_storage_root=cover_root,
        cover_thumbnail_cache_path=thumbnail_root,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed_publication_library(database_path, published_root)
    payload = _jpeg_payload()

    with TestClient(_app(settings, ROLE_ADMIN)) as client:
        media_id = _upload_and_catalog(client, payload, "still.jpg")

        tag = client.post("/api/canonical-tags", json={"key": "still", "display_name": "Still"})
        assert tag.status_code == 201
        prepared = client.put(
            f"/api/media/{media_id}/metadata",
            json={
                "display_title": "Still Cover",
                "description": "Saved canonical description.",
                "tag_keys": ["still"],
            },
        )
        assert prepared.status_code == 200
        published = client.put(f"/api/admin/media/{media_id}/content-publication")
        assert published.status_code == 201
        assert published.json()["status"] == "published"

        catalog = client.get("/api/media")
        assert catalog.status_code == 200
        items = catalog.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["media_kind"] == "image"
        assert item["cover_ready"] is False
        location_id = item["locations"][0]["location_id"]

        timeline = client.get(
            f"/api/media/{media_id}/locations/{location_id}/cover-timeline"
        )
        assert timeline.status_code == 200
        timeline_payload = timeline.json()
        assert timeline_payload["media_kind"] == "image"
        assert timeline_payload["duration_ms"] == 0
        source_version = timeline_payload["source_version"]
        assert len(source_version) == 64

        preview = client.get(
            f"/api/media/{media_id}/locations/{location_id}/cover-frame",
            params={"timestamp_ms": 0, "source_version": source_version},
        )
        assert preview.status_code == 200
        assert preview.content.startswith(b"\x89PNG")
        assert preview.headers["cache-control"] == "no-store"

        put = client.put(
            f"/api/media/{media_id}/locations/{location_id}/cover",
            json={
                "timestamp_ms": 0,
                "expected_revision": 0,
                "expected_source_version": source_version,
            },
        )
        assert put.status_code == 201
        created = put.json()
        assert created["status"] == "created"
        assert created["revision"] == 1
        assert created["timestamp_ms"] == 0
        assert created["thumbnail_state"] == "ready"

        state = client.get(f"/api/admin/media/{media_id}/cover")
        assert state.status_code == 200
        state_payload = state.json()
        assert state_payload["has_cover"] is True
        assert state_payload["source_kind"] == "image"
        assert state_payload["timestamp_ms"] == 0
        assert state_payload["artifact_state"] == "available"
        assert state_payload["thumbnail_state"] == "ready"

        thumb = client.get(f"/api/media/{media_id}/cover-thumbnail")
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(thumb.content)) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.size[0] > 0 and decoded.size[1] > 0

        catalog_after = client.get("/api/media").json()["items"][0]
        assert catalog_after["cover_ready"] is True

        content = client.get(f"/api/media/{media_id}/locations/{location_id}/content")
        assert content.status_code == 200
        assert content.content == payload
        download = client.get(f"/api/media/{media_id}/locations/{location_id}/download")
        assert download.status_code == 200
        assert download.content == payload

        unchanged = client.put(
            f"/api/media/{media_id}/locations/{location_id}/cover",
            json={
                "timestamp_ms": 0,
                "expected_revision": 1,
                "expected_source_version": source_version,
            },
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["status"] == "unchanged"
        assert unchanged.json()["revision"] == 1

        stale = client.put(
            f"/api/media/{media_id}/locations/{location_id}/cover",
            json={
                "timestamp_ms": 0,
                "expected_revision": 7,
                "expected_source_version": source_version,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "COVER_CONFLICT"

    # Mutate the published source to invalidate the observed source identity.
    with sqlite3.connect(database_path) as connection:
        relative_target = connection.execute(
            "SELECT relative_target FROM upload_publications WHERE media_id = ?",
            (media_id,),
        ).fetchone()[0]
    published_path = published_root / relative_target
    original_digest = hashlib.sha256(published_path.read_bytes()).hexdigest()
    assert published_path.read_bytes() == payload
    changed_payload = _jpeg_payload(size=(320, 200), color=(200, 50, 90))
    published_path.write_bytes(changed_payload)
    os.utime(published_path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    with TestClient(_app(settings, ROLE_ADMIN)) as client:
        location = client.get("/api/media").json()["items"][0]["locations"][0]
        location_id = location["location_id"]
        changed_timeline = client.get(
            f"/api/media/{media_id}/locations/{location_id}/cover-timeline"
        ).json()
        assert changed_timeline["source_version"] != source_version

        stale_source = client.put(
            f"/api/media/{media_id}/locations/{location_id}/cover",
            json={
                "timestamp_ms": 0,
                "expected_revision": 1,
                "expected_source_version": source_version,
            },
        )
        assert stale_source.status_code == 409
        assert stale_source.json()["error"]["code"] == "COVER_SOURCE_CHANGED"

        replaced = client.put(
            f"/api/media/{media_id}/locations/{location_id}/cover",
            json={
                "timestamp_ms": 0,
                "expected_revision": 1,
                "expected_source_version": changed_timeline["source_version"],
            },
        )
        assert replaced.status_code == 200
        assert replaced.json()["status"] == "replaced"
        assert replaced.json()["revision"] == 2

    # Ordinary users may read the published cover/original media but not mutate.
    with TestClient(_app(settings, ROLE_USER)) as ordinary:
        location = ordinary.get("/api/media").json()["items"][0]["locations"][0]
        denied = ordinary.put(
            f"/api/media/{media_id}/locations/{location_id}/cover",
            json={
                "timestamp_ms": 0,
                "expected_revision": 2,
                "expected_source_version": changed_timeline["source_version"],
            },
        )
        assert denied.status_code == 403
        assert ordinary.get(f"/api/media/{media_id}/cover-thumbnail").status_code == 200
        assert (
            ordinary.get(
                f"/api/media/{media_id}/locations/{location_id}/content"
            ).status_code
            == 200
        )
        assert (
            ordinary.get(
                f"/api/media/{media_id}/locations/{location_id}/download"
            ).status_code
            == 200
        )

    # Original-media integrity: the source now holds the replacement bytes, and
    # the durable normalized artifact never touched the original location.
    assert hashlib.sha256(published_path.read_bytes()).hexdigest() != original_digest
    assert published_path.read_bytes() == changed_payload
    assert list(cover_root.rglob("*.jpg"))  # durable artifact published
