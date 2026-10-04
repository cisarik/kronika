"""Integration proof for the manual metadata workspace API workflow."""

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


def _snapshot_files(root: Path) -> dict[str, tuple[int, int, bytes]]:
    snapshot: dict[str, tuple[int, int, bytes]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                path.read_bytes(),
            )
    return snapshot


def _register_library(database_path: Path, library_root: Path) -> LibraryId:
    engine = create_sqlite_engine(database_path)
    library_id = LibraryId.new()
    try:
        device = Device(id=DeviceId.new(), display_name="Workspace Test Device")
        SqliteDeviceRepository(engine).add(device)
        library = Library(
            id=library_id,
            device_id=device.id,
            display_name="Workspace Test Library",
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


def test_local_web_manual_metadata_workspace_api_roundtrip_and_file_safety(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    (library_root / "entropy.mp4").write_bytes(b"synthetic-mp4-bytes")
    before = _snapshot_files(library_root)

    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with _admin_client(settings) as client:
        imported = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "entropy.mp4"},
        )
        assert imported.status_code == 200
        media_id = imported.json()["media"]["id"]
        engine = create_sqlite_engine(database_path)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO media_content_publications "
                        "(media_id, published_at_ms, publication_origin) "
                        "VALUES (:media_id, 1, 'admin_explicit')"
                    ),
                    {"media_id": media_id},
                )
        finally:
            dispose_engine(engine)

        sparse = client.get(f"/api/media/{media_id}/metadata")
        math = client.post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Math"})
        compression = client.post(
            "/api/canonical-tags",
            json={"key": "compression", "display_name": "Compression"},
        )
        created = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Reinventing Entropy", "description": None, "tag_keys": ["mathematics", "compression"]},
        )
        read_back = client.get(f"/api/media/{media_id}/metadata")
        catalog = client.get("/api/media", params={"q": "entropy"})
        updated = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Entropy Revisited", "description": None, "tag_keys": ["compression", "mathematics"]},
        )
        updated_catalog = client.get("/api/media", params=[("tag", "compression"), ("tag", "mathematics")])
        cleared = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": None, "description": None, "tag_keys": []},
        )
        unchanged = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": None, "description": None, "tag_keys": []},
        )
        cleared_catalog = client.get("/api/media")
        retagged = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Entropy Revisited", "description": None, "tag_keys": ["mathematics"]},
        )
        retagged_read_back = client.get(f"/api/media/{media_id}/metadata")

    assert sparse.status_code == 200
    assert sparse.json()["persisted"] is False
    assert sparse.json()["display_title"] is None
    assert sparse.json()["tags"] == []
    assert math.status_code == 201
    assert compression.status_code == 201

    assert created.status_code == 200
    assert created.json()["status"] == "created"
    assert created.json()["metadata"]["collection_key"] == "processed"
    created_ts = created.json()["metadata"]["processed_at_ms"]
    assert created_ts is not None
    assert isinstance(created_ts, int)
    assert not isinstance(created_ts, bool)
    assert read_back.json()["display_title"] == "Reinventing Entropy"
    assert read_back.json()["collection_key"] == "processed"
    assert read_back.json()["processed_at_ms"] == created_ts
    assert read_back.json()["tags"] == [
        {"key": "mathematics", "display_name": "Math"},
        {"key": "compression", "display_name": "Compression"},
    ]
    assert catalog.status_code == 200
    assert catalog.json()["items"][0]["display_title"] == "Reinventing Entropy"
    assert catalog.json()["items"][0]["collection_key"] == "processed"
    assert catalog.json()["items"][0]["processed_at_ms"] == created_ts
    assert catalog.json()["items"][0]["tags"] == [
        {"key": "mathematics", "display_name": "Math", "position": 0},
        {"key": "compression", "display_name": "Compression", "position": 1},
    ]

    assert updated.status_code == 200
    assert updated.json()["status"] == "updated"
    assert updated.json()["metadata"]["collection_key"] == "processed"
    assert updated.json()["metadata"]["processed_at_ms"] == created_ts
    assert updated.json()["metadata"]["tags"] == [
        {"key": "compression", "display_name": "Compression"},
        {"key": "mathematics", "display_name": "Math"},
    ]
    assert updated_catalog.status_code == 200
    assert updated_catalog.json()["items"][0]["display_title"] == "Entropy Revisited"
    assert updated_catalog.json()["items"][0]["collection_key"] == "processed"
    assert updated_catalog.json()["items"][0]["processed_at_ms"] == created_ts
    assert updated_catalog.json()["items"][0]["tags"] == [
        {"key": "compression", "display_name": "Compression", "position": 0},
        {"key": "mathematics", "display_name": "Math", "position": 1},
    ]

    assert cleared.status_code == 200
    assert cleared.json()["status"] == "updated"
    assert cleared.json()["metadata"]["collection_key"] is None
    assert cleared.json()["metadata"]["processed_at_ms"] is None
    assert cleared.json()["metadata"]["display_title"] is None
    assert cleared.json()["metadata"]["tags"] == []
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "unchanged"
    assert cleared_catalog.status_code == 200
    assert cleared_catalog.json()["items"][0]["display_title"] is None
    assert cleared_catalog.json()["items"][0]["collection_key"] is None
    assert cleared_catalog.json()["items"][0]["processed_at_ms"] is None
    assert cleared_catalog.json()["items"][0]["tags"] == []

    assert retagged.status_code == 200
    assert retagged.json()["metadata"]["collection_key"] == "processed"
    retagged_ts = retagged.json()["metadata"]["processed_at_ms"]
    assert retagged_ts is not None
    assert isinstance(retagged_ts, int)
    assert retagged_ts >= created_ts
    assert retagged_read_back.status_code == 200
    assert retagged_read_back.json()["collection_key"] == "processed"
    assert retagged_read_back.json()["processed_at_ms"] == retagged_ts

    for response in (
        imported,
        sparse,
        created,
        read_back,
        catalog,
        updated,
        updated_catalog,
        cleared,
        unchanged,
        cleared_catalog,
        retagged,
        retagged_read_back,
    ):
        assert str(library_root) not in response.text
        assert str(database_path) not in response.text

    assert _snapshot_files(library_root) == before
