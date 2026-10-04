"""Integration proof for the same-origin searchable media catalog."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
)
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _native_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _register_library(database_path: Path, library_root: Path) -> LibraryId:
    engine = create_sqlite_engine(database_path)
    library_id = LibraryId.new()
    try:
        device = Device(id=DeviceId.new(), display_name="Catalog Test Device")
        SqliteDeviceRepository(engine).add(device)
        library = Library(
            id=library_id,
            device_id=device.id,
            display_name="Catalog Test Library",
            root=LibraryRoot(flavor=_native_flavor(), path=os.path.normpath(str(library_root))),
        )
        SqliteLibraryRepository(engine).add(library)
        return library_id
    finally:
        dispose_engine(engine)


def _admin_client(settings: KronikaSettings) -> TestClient:
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
        return await call_next(request)

    return TestClient(app)


def test_local_web_catalog_search_tags_pagination_locations_and_read_only_get(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    (library_root / "entropy.mp4").write_bytes(b"mp4-data-a")
    (library_root / "reaction.gif").write_bytes(b"gif-data-b")
    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with _admin_client(settings) as client:
        imported_entropy = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "entropy.mp4"},
        )
        imported_reaction = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "reaction.gif"},
        )
        assert imported_entropy.status_code == 200
        assert imported_reaction.status_code == 200
        entropy_media_id = imported_entropy.json()["media"]["id"]
        reaction_media_id = imported_reaction.json()["media"]["id"]
        math = client.post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Math"})
        compression = client.post(
            "/api/canonical-tags",
            json={"key": "compression", "display_name": "Compression"},
        )
        meme = client.post("/api/canonical-tags", json={"key": "meme", "display_name": "Meme"})
        assert math.status_code == 201
        assert compression.status_code == 201
        assert meme.status_code == 201
        entropy_metadata = client.put(
            f"/api/media/{entropy_media_id}/metadata",
            json={"display_title": "Reinventing Entropy", "description": "A treatise on entropy", "tag_keys": ["mathematics", "compression"]},
        )
        reaction_metadata = client.put(
            f"/api/media/{reaction_media_id}/metadata",
            json={"display_title": "Reaction Entropy", "description": None, "tag_keys": ["meme"]},
        )
        assert entropy_metadata.status_code == 200
        assert reaction_metadata.status_code == 200

        engine = create_sqlite_engine(database_path)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO media_content_publications "
                        "(media_id, published_at_ms, publication_origin) "
                        "VALUES (:entropy, 1, 'admin_explicit'), "
                        "(:reaction, 1, 'admin_explicit')"
                    ),
                    {
                        "entropy": entropy_media_id,
                        "reaction": reaction_media_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO physical_media_locations "
                        "(id, media_id, library_id, relative_path, availability, observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
                        "VALUES (:id, :media_id, :library_id, 'backup/entropy-copy.mp4', 'offline', 10, 20, 1, 1)"
                    ),
                    {
                        "id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                        "media_id": entropy_media_id,
                        "library_id": library_id.to_string(),
                    },
                )
        finally:
            dispose_engine(engine)

        before_counts = _catalog_counts(database_path)
        title_search = client.get("/api/media", params={"q": "entropy"})
        and_filter = client.get(
            "/api/media",
            params=[("tag", "mathematics"), ("tag", "compression")],
        )
        paged = client.get("/api/media", params={"limit": "1", "offset": "1"})
        after_counts = _catalog_counts(database_path)

    assert title_search.status_code == 200
    assert title_search.json()["total"] == 2
    assert and_filter.status_code == 200
    assert [item["media_id"] for item in and_filter.json()["items"]] == [entropy_media_id]
    assert and_filter.json()["items"][0]["tags"] == [
        {"key": "mathematics", "display_name": "Math", "position": 0},
        {"key": "compression", "display_name": "Compression", "position": 1},
    ]
    assert and_filter.json()["items"][0]["description"] == "A treatise on entropy"
    assert len(and_filter.json()["items"][0]["locations"]) == 2
    assert [location["relative_path"] for location in and_filter.json()["items"][0]["locations"]] == [
        "backup/entropy-copy.mp4",
        "entropy.mp4",
    ]
    assert paged.status_code == 200
    assert paged.json()["total"] == 2
    assert paged.json()["limit"] == 1
    assert paged.json()["offset"] == 1
    assert before_counts == after_counts
    assert str(library_root) not in title_search.text + and_filter.text + paged.text
    assert str(database_path) not in title_search.text + and_filter.text + paged.text


def _catalog_counts(database_path: Path) -> tuple[int, int, int, int, int]:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            return (
                connection.execute(text("SELECT COUNT(*) FROM logical_media")).scalar_one(),
                connection.execute(text("SELECT COUNT(*) FROM physical_media_locations")).scalar_one(),
                connection.execute(text("SELECT COUNT(*) FROM canonical_tags")).scalar_one(),
                connection.execute(text("SELECT COUNT(*) FROM media_metadata")).scalar_one(),
                connection.execute(text("SELECT COUNT(*) FROM media_canonical_tags")).scalar_one(),
            )
    finally:
        dispose_engine(engine)
