"""Full-application unpublish contract on the sole content-publication route."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_LOGIN = "owner@example.com"
DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
READY = "11111111-1111-4111-8111-111111111111"
READY_LOC = "21111111-1111-4111-8111-111111111111"


def _serve_headers(login: str, name: str = "User") -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": name,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


def _mutation_headers(login: str = ADMIN_LOGIN) -> dict[str, str]:
    return {
        **_serve_headers(login, "Admin"),
        "Origin": EXTERNAL_ORIGIN,
        "X-FrameNest-Request": "1",
    }


def _client(tmp_path: Path) -> TestClient:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={
            ADMIN_LOGIN: "admin",
            USER_LOGIN: "user",
        },
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed(settings.database_path)
    return TestClient(create_app(settings=settings))


def _seed(database_path: Path) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO devices (id, display_name) VALUES (:id, 'Dev')"),
                {"id": DEVICE_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO libraries "
                    "(id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device, 'Lib', 'posix', '/tmp/synthetic')"
                ),
                {"id": LIBRARY_ID, "device": DEVICE_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_tags "
                    "(key, display_name, created_at_ms, updated_at_ms) "
                    "VALUES ('manual', 'Manual', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 10, 10)"
                ),
                {"id": READY},
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations ("
                    "id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                    ") VALUES (:id, :media, :library, 'ready.mp4', 'available', 8, NULL, 10, 10)"
                ),
                {"id": READY_LOC, "media": READY, "library": LIBRARY_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO media_metadata ("
                    "media_id, display_title, description, created_at_ms, updated_at_ms, "
                    "content_category, acquisition_source"
                    ") VALUES (:id, 'Ready title', 'Ready description', 10, 10, "
                    "'general', 'manual_upload')"
                ),
                {"id": READY},
            )
            connection.execute(
                text(
                    "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
                    "VALUES (:media, 'manual', 0)"
                ),
                {"media": READY},
            )
    finally:
        dispose_engine(engine)


def _gallery_ids(client: TestClient) -> set[str]:
    gallery = client.get("/api/media", headers=_serve_headers(USER_LOGIN, "Owner"))
    assert gallery.status_code == 200
    return {item["media_id"] for item in gallery.json()["items"]}


def _publication_count(database_path: Path, media_id: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM media_content_publications WHERE media_id = ?",
                (media_id,),
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _audit_actions(database_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT action, capability, outcome, http_status, target_id "
            "FROM security_audit_events ORDER BY occurred_at_ms, id"
        ).fetchall()
    finally:
        connection.close()


def test_publish_then_unpublish_removes_gallery_visibility(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        published = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
        )
        assert published.status_code == 201
        assert published.json()["status"] == "published"
        assert READY in _gallery_ids(client)
        unpublished = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
            json={"published": False},
        )
        assert unpublished.status_code == 200
        assert unpublished.json()["status"] == "unpublished"
        assert unpublished.json()["publication"] is None
        assert unpublished.headers.get("cache-control") == "no-store"
        assert READY not in _gallery_ids(client)
        assert _publication_count(tmp_path / "catalog.sqlite3", READY) == 0
        already = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
            json={"published": False},
        )
        assert already.status_code == 200
        assert already.json()["status"] == "already_unpublished"
        republished = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
        )
        assert republished.status_code == 201
        assert republished.json()["publication"]["publication_origin"] == "admin_explicit"
        assert READY in _gallery_ids(client)


def test_unpublish_of_unpublished_item_is_truthful(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
            json={"published": False},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "already_unpublished"
        assert response.json()["publication"] is None
        assert READY not in _gallery_ids(client)
        assert _publication_count(tmp_path / "catalog.sqlite3", READY) == 0


def test_unpublish_requires_admin_capability(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
        )
        ordinary = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(USER_LOGIN),
            json={"published": False},
        )
        missing_headers = _mutation_headers()
        del missing_headers["Tailscale-User-Login"]
        missing = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=missing_headers,
            json={"published": False},
        )
        assert ordinary.status_code == 403
        assert ordinary.json()["error"]["code"] == "CAPABILITY_DENIED"
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "IDENTITY_REQUIRED"
        assert READY in _gallery_ids(client)
        assert _publication_count(tmp_path / "catalog.sqlite3", READY) == 1


def test_unpublish_is_audited_on_the_existing_publication_action(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
        )
        unpublished = client.put(
            f"/api/admin/media/{READY}/content-publication",
            headers=_mutation_headers(),
            json={"published": False},
        )
        assert unpublished.status_code == 200
        rows = [
            row
            for row in _audit_actions(tmp_path / "catalog.sqlite3")
            if row["action"] == "media.content_publish"
        ]
        assert [row["http_status"] for row in rows] == [201, 200]
        assert rows[-1]["capability"] == "media.content.publish"
        assert rows[-1]["outcome"] == "allowed"
        assert rows[-1]["target_id"] == READY
