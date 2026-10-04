"""Integration proof for same-origin persistent media metadata API."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.configuration import KronikaSettings
from kronika.domain import MediaId
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _insert_media(database_path: Path) -> MediaId:
    media_id = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 10, 10)"
                ),
                {"id": media_id.to_string()},
            )
    finally:
        dispose_engine(engine)
    return media_id


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


def _publish_for_test(database_path: Path, media_id: MediaId) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_content_publications "
                    "(media_id, published_at_ms, publication_origin) "
                    "VALUES (:media_id, 1, 'admin_explicit')"
                ),
                {"media_id": media_id.to_string()},
            )
    finally:
        dispose_engine(engine)


def test_local_web_persists_display_title_and_canonical_tags(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    media_id = _insert_media(database_path)

    with _admin_client(settings) as client:
        math = client.post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Math"})
        compression = client.post(
            "/api/canonical-tags",
            json={"key": "compression", "display_name": "Compression"},
        )
        unsaved = client.get(f"/api/media/{media_id}/metadata")
        created = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Reinventing Entropy", "description": None, "tag_keys": ["mathematics", "compression"]},
        )
        unchanged = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Reinventing Entropy", "description": None, "tag_keys": ["mathematics", "compression"]},
        )
        cleared = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": None, "description": None, "tag_keys": []},
        )

    assert math.status_code == 201
    assert compression.status_code == 201
    assert unsaved.json()["persisted"] is False
    assert created.json()["status"] == "created"
    assert created.json()["metadata"]["display_title"] == "Reinventing Entropy"
    assert created.json()["metadata"]["tags"] == [
        {"key": "mathematics", "display_name": "Math"},
        {"key": "compression", "display_name": "Compression"},
    ]
    assert unchanged.json()["status"] == "unchanged"
    assert unchanged.json()["metadata"]["updated_at_ms"] == created.json()["metadata"]["updated_at_ms"]
    assert cleared.json()["status"] == "updated"
    assert cleared.json()["metadata"]["display_title"] is None
    assert cleared.json()["metadata"]["tags"] == []

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM canonical_tags")).scalar_one() == 2
            assert connection.execute(text("SELECT COUNT(*) FROM media_metadata")).scalar_one() == 1
            assert connection.execute(text("SELECT COUNT(*) FROM media_canonical_tags")).scalar_one() == 0
            assert connection.execute(text("SELECT COUNT(*) FROM physical_media_locations")).scalar_one() == 0
    finally:
        dispose_engine(engine)


def test_local_web_metadata_api_exposes_processed_collection_lifecycle(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "processed-lifecycle.sqlite3"
    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    media_id = _insert_media(database_path)
    _publish_for_test(database_path, media_id)

    with _admin_client(settings) as client:
        client.post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Math"})
        client.post("/api/canonical-tags", json={"key": "compression", "display_name": "Compression"})

        first = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Reinventing Entropy", "description": None, "tag_keys": ["mathematics"]},
        )
        first_metadata = first.json()["metadata"]
        assert first.status_code == 200
        assert first_metadata["collection_key"] == "processed"
        first_ts = first_metadata["processed_at_ms"]
        assert first_ts is not None
        assert isinstance(first_ts, int)
        assert not isinstance(first_ts, bool)
        assert first_ts >= 0

        title_only = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Entropy", "description": None, "tag_keys": ["mathematics"]},
        )
        assert title_only.status_code == 200
        assert title_only.json()["metadata"]["collection_key"] == "processed"
        assert title_only.json()["metadata"]["processed_at_ms"] == first_ts

        reorder = client.put(
            f"/api/media/{media_id}/metadata",
            json={
                "display_title": "Entropy",
                "description": None,
                "tag_keys": ["compression", "mathematics"],
            },
        )
        assert reorder.status_code == 200
        assert reorder.json()["metadata"]["processed_at_ms"] == first_ts

        cleared = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Entropy", "description": None, "tag_keys": []},
        )
        assert cleared.status_code == 200
        assert cleared.json()["metadata"]["collection_key"] is None
        assert cleared.json()["metadata"]["processed_at_ms"] is None

        reentry = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "Entropy", "description": None, "tag_keys": ["mathematics"]},
        )
        assert reentry.status_code == 200
        assert reentry.json()["metadata"]["collection_key"] == "processed"
        reentry_ts = reentry.json()["metadata"]["processed_at_ms"]
        assert reentry_ts is not None
        assert isinstance(reentry_ts, int)
        assert reentry_ts >= first_ts

        read_back = client.get(f"/api/media/{media_id}/metadata")
        assert read_back.status_code == 200
        assert read_back.json()["collection_key"] == "processed"
        assert read_back.json()["processed_at_ms"] == reentry_ts

        processed_catalog = client.get("/api/media", params={"collection": "processed"})
        assert processed_catalog.status_code == 200
        assert any(item["media_id"] == media_id.to_string() for item in processed_catalog.json()["items"])
        processed_item = next(
            item for item in processed_catalog.json()["items"] if item["media_id"] == media_id.to_string()
        )
        assert processed_item["collection_key"] == "processed"
        assert processed_item["processed_at_ms"] == reentry_ts

        unknown_collection = client.get("/api/media", params={"collection": "custom-collection"})
        assert unknown_collection.status_code == 422


def test_metadata_save_leaves_automatic_analysis_result_and_path_unchanged(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "durable-suggestion-immutability.sqlite3"
    settings = KronikaSettings(
        database_path=database_path,
        automatic_media_analysis_enabled=True,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    media_id = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
    location_id = "87654321-4321-4321-8321-cba987654321"
    device_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    library_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result_json = (
        '{"collection":"MustStay","confidence":0.5,"description":"Durable description",'
        '"evidence":["frame"],"suggested_filename":"durable.mp4","tags":["alpha"],'
        '"title":"Durable title","uncertainties":[]}'
    )
    relative_path = "clips/original-name.mp4"

    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO devices (id, display_name) VALUES (:id, 'Synthetic')"
                ),
                {"id": device_id},
            )
            connection.execute(
                text(
                    "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device_id, 'Library', 'posix', '/synthetic')"
                ),
                {"id": library_id, "device_id": device_id},
            )
            connection.execute(
                text(
                    "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 10, 10)"
                ),
                {"id": media_id.to_string()},
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations ("
                    "id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                    ") VALUES ("
                    ":id, :media_id, :library_id, :relative_path, 'available', 8, NULL, 10, 10)"
                ),
                {
                    "id": location_id,
                    "media_id": media_id.to_string(),
                    "library_id": library_id,
                    "relative_path": relative_path,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_analysis_runs ("
                    "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
                    "provider_id, model_id, prompt_version, result_schema_version, result_json, "
                    "error_code, error_message, created_at_ms, started_at_ms, completed_at_ms, version"
                    ") VALUES ("
                    ":id, :media_id, :location_id, 'automatic_post_catalog', 'analyzed', 1, "
                    "'nvidia-nim', 'test-model', 'framenest-media-suggestion-v3', "
                    "'framenest-media-suggestion-result-v1', :result_json, "
                    "NULL, NULL, 10, 11, 12, 3)"
                ),
                {
                    "id": run_id,
                    "media_id": media_id.to_string(),
                    "location_id": location_id,
                    "result_json": result_json,
                },
            )
    finally:
        dispose_engine(engine)

    with _admin_client(settings) as client:
        before = client.get(f"/api/media/{media_id}/automatic-analysis")
        assert before.status_code == 200
        assert before.json()["state"] == "analyzed"
        assert before.json()["result"]["title"] == "Durable title"
        assert "sk-" not in before.text

        client.post("/api/canonical-tags", json={"key": "alpha", "display_name": "Alpha"})
        saved = client.put(
            f"/api/media/{media_id}/metadata",
            json={
                "display_title": "Operator title",
                "description": "Operator description",
                "tag_keys": ["alpha"],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["metadata"]["display_title"] == "Operator title"
        assert "suggested_filename" not in saved.json()["metadata"]
        assert saved.json()["metadata"]["collection_key"] == "processed"

        invalid = client.put(
            f"/api/media/{media_id}/metadata",
            json={"display_title": "", "description": None, "tag_keys": []},
        )
        assert invalid.status_code == 422

        after = client.get(f"/api/media/{media_id}/automatic-analysis")
        assert after.status_code == 200
        assert after.json()["result"] == before.json()["result"]

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            stored_result = connection.execute(
                text("SELECT result_json, state FROM media_analysis_runs WHERE id = :id"),
                {"id": run_id},
            ).one()
            assert stored_result.result_json == result_json
            assert stored_result.state == "analyzed"
            stored_path = connection.execute(
                text(
                    "SELECT relative_path FROM physical_media_locations WHERE id = :id"
                ),
                {"id": location_id},
            ).scalar_one()
            assert stored_path == relative_path
            title = connection.execute(
                text("SELECT display_title FROM media_metadata WHERE media_id = :id"),
                {"id": media_id.to_string()},
            ).scalar_one()
            assert title == "Operator title"
    finally:
        dispose_engine(engine)
