"""Complete direct-media audience enforcement contract."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

PUBLISHED_ID = "11111111-1111-4111-8111-111111111111"
UNPUBLISHED_ID = "22222222-2222-4222-8222-222222222222"
UNKNOWN_ID = "33333333-3333-4333-8333-333333333333"
LOCATION_ID = "44444444-4444-4444-8444-444444444444"


def _settings(tmp_path: Path) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "audience.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )


def _seed(settings: KronikaSettings) -> None:
    upgrade_database_to_head(settings)
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (?, 'video', ?, ?)",
            (
                (PUBLISHED_ID, 10, 10),
                (UNPUBLISHED_ID, 20, 20),
            ),
        )
        connection.executemany(
            "INSERT INTO media_metadata "
            "(media_id, display_title, description, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, 1, 1)",
            (
                (PUBLISHED_ID, "Published", "Published description"),
                (UNPUBLISHED_ID, "Unpublished", "Unpublished description"),
            ),
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


def _admin_identity() -> IdentityContext:
    return IdentityContext(
        login="admin@example.com",
        login_key="admin@example.com",
        display_name="Admin",
        role=ROLE_ADMIN,
        capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN],
        provenance="tailscale-serve",
    )


def _ordinary_identity() -> IdentityContext:
    from kronika.domain.identity_access import ROLE_USER

    return IdentityContext(
        login="owner@example.com",
        login_key="owner@example.com",
        display_name="Owner",
        role=ROLE_USER,
        capabilities=CAPABILITIES_BY_ROLE[ROLE_USER],
        provenance="tailscale-serve",
    )


def _client(
    settings: KronikaSettings,
    *,
    admin: bool = False,
) -> TestClient:
    app = create_app(settings=settings)
    identity = _admin_identity() if admin else _ordinary_identity()

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = identity
        return await call_next(request)

    return TestClient(app)


def test_ordinary_catalog_is_published_only_and_cannot_request_unpublished() -> None:
    from kronika.application.media_catalog import ListMediaCatalog

    class CapturingRepository:
        query = None

        def list_media(self, query):
            self.query = query
            from kronika.application.ports.media_catalog_repository import (
                MediaCatalogPage,
            )

            return MediaCatalogPage(
                items=(),
                total=0,
                limit=query.limit,
                offset=query.offset,
                q=query.q,
                tag_keys=query.tag_keys,
            )

    from kronika.domain.record_access import RecordAccessScope

    repository = CapturingRepository()
    ListMediaCatalog(repository).execute(access_scope=RecordAccessScope.legacy_public())

    assert repository.query.published_only is True
    assert repository.query.access_scope == RecordAccessScope.legacy_public()


def test_public_list_hides_unpublished_while_admin_list_can_inspect_it(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _seed(settings)

    with _client(settings) as ordinary:
        listing = ordinary.get("/api/media")
        denied = ordinary.get(f"/api/media/{UNPUBLISHED_ID}/metadata")
        unknown = ordinary.get(f"/api/media/{UNKNOWN_ID}/metadata")
        denied_detail = ordinary.get(f"/api/media/{UNPUBLISHED_ID}")
        unknown_detail = ordinary.get(f"/api/media/{UNKNOWN_ID}")
        published_detail = ordinary.get(f"/api/media/{PUBLISHED_ID}")

    with _client(settings, admin=True) as admin:
        workflow = admin.get("/api/admin/media")
        metadata = admin.get(f"/api/media/{UNPUBLISHED_ID}/metadata")
        admin_detail = admin.get(f"/api/media/{UNPUBLISHED_ID}")

    assert listing.status_code == 200
    assert [item["media_id"] for item in listing.json()["items"]] == [
        PUBLISHED_ID
    ]
    assert denied.status_code == unknown.status_code == 404
    assert denied.json() == unknown.json()
    assert denied_detail.status_code == unknown_detail.status_code == 404
    assert denied_detail.json() == unknown_detail.json()
    assert published_detail.status_code == 200
    assert published_detail.json()["display_title"] == "Published"
    assert published_detail.json()["media_kind"] == "video"
    assert workflow.status_code == 200
    assert [item["media_id"] for item in workflow.json()["items"]] == [
        UNPUBLISHED_ID
    ]
    assert metadata.status_code == 200
    assert metadata.json()["display_title"] == "Unpublished"
    assert admin_detail.status_code == 200
    assert admin_detail.json()["display_title"] == "Unpublished"


def test_every_direct_surface_denies_unpublished_like_unknown(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _seed(settings)
    direct_reads = [
        (
            "GET",
            f"/api/media/{{media_id}}",
            None,
        ),
        (
            "GET",
            f"/api/media/{{media_id}}/metadata",
            None,
        ),
        (
            "PUT",
            f"/api/media/{{media_id}}/metadata",
            {
                "display_title": "Attempt",
                "description": "Attempt",
                "tag_keys": [],
            },
        ),
        (
            "GET",
            f"/api/media/{{media_id}}/locations/{LOCATION_ID}/content",
            None,
        ),
        (
            "GET",
            f"/api/media/{{media_id}}/locations/{LOCATION_ID}/download",
            None,
        ),
        (
            "GET",
            f"/api/media/{{media_id}}/locations/{LOCATION_ID}/gallery-preview",
            None,
        ),
        (
            "GET",
            f"/api/media/{{media_id}}/automatic-analysis",
            None,
        ),
        (
            "GET",
            f"/api/media/{{media_id}}/movie-identification",
            None,
        ),
        (
            "POST",
            f"/api/media/{{media_id}}/locations/{LOCATION_ID}/durable-analysis",
            {"confirm_cloud_upload": False},
        ),
        (
            "POST",
            f"/api/media/{{media_id}}/locations/{LOCATION_ID}/movie-identification",
            {"confirm_cloud_upload": False},
        ),
        (
            "POST",
            f"/api/media/{{media_id}}/locations/{LOCATION_ID}/ai-suggestion-preview",
            {"confirm_cloud_upload": False},
        ),
    ]
    with _client(settings) as client:
        for method, template, body in direct_reads:
            unpublished_url = template.format(media_id=UNPUBLISHED_ID)
            unknown_url = template.format(media_id=UNKNOWN_ID)
            if method == "GET":
                unpublished = client.get(unpublished_url)
                unknown = client.get(unknown_url)
            elif method == "PUT":
                unpublished = client.put(unpublished_url, json=body)
                unknown = client.put(unknown_url, json=body)
            else:
                unpublished = client.post(unpublished_url, json=body)
                unknown = client.post(unknown_url, json=body)
            assert unpublished.status_code == unknown.status_code == 404, (
                method,
                template,
                unpublished.text,
                unknown.text,
            )
            assert unpublished.json() == unknown.json(), (method, template)
