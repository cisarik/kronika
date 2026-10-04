"""Contract tests for administrator companion review inbox GET routes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.media_suggestion_api import MediaSuggestionApiDependencies
from kronika.application.companion_review import encode_companion_review_cursor
from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    MediaRelativePath,
    PreparedAnalysisResult,
    REQUESTED_FRAME_COUNT,
    TechnicalMetadata,
    build_representative_frame,
    PNG_SIGNATURE,
)
from kronika.application.media_analysis_lifecycle import PersistImportedPreviewAnalysis
from kronika.application.media_suggestion import (
    MediaSuggestion,
    PreviewImportedMediaSuggestion,
    PreviewMediaSuggestion,
    PROMPT_VERSION,
)
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_analysis_run_repository import (
    SqliteMediaAnalysisRunRepository,
)
from kronika.infrastructure.persistence.media_metadata_repository import (
    SqliteMediaMetadataRepository,
)
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_LOGIN = "owner@example.com"
BOB_LOGIN = "bob@example.com"

DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GENERIC = "11111111-1111-4111-8111-111111111111"
GENERIC_LOC = "21111111-1111-4111-8111-111111111111"
GENERIC_RUN = "31111111-1111-4111-8111-111111111111"
MOVIE = "22222222-2222-4222-8222-222222222222"
MOVIE_LOC = "42222222-2222-4222-8222-222222222222"
MOVIE_RUN = "52222222-2222-4222-8222-222222222222"
MISSING = "33333333-3333-4333-8333-333333333333"
PUBLISH = "44444444-4444-4444-8444-444444444444"
PUBLISH_LOC = "54444444-4444-4444-8444-444444444444"
PENDING = "61111111-1111-4111-8111-111111111111"
PENDING_LOC = "71111111-1111-4111-8111-111111111111"
PENDING_CLAIM = "81111111-1111-4111-8111-111111111111"
PENDING_ASSET = "91111111-1111-4111-8111-111111111111"
COMPANION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _serve_headers(login: str, name: str = "User") -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": name,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


def _result_json(title: str) -> str:
    return json.dumps(
        {
            "collection": "memes",
            "confidence": 0.9,
            "description": "A description.",
            "evidence": ["visible subject"],
            "suggested_filename": "clip.gif",
            "tags": ["Cats"],
            "title": title,
            "uncertainties": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _mutation_headers(login: str = ADMIN_LOGIN, origin: str = EXTERNAL_ORIGIN) -> dict[str, str]:
    return {
        **_serve_headers(login, "Admin"),
        "Origin": origin,
        "X-FrameNest-Request": "1",
    }


def _client(
    tmp_path: Path, *, companion_origins: tuple[str, ...] = ()
) -> TestClient:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={
            ADMIN_LOGIN: "admin",
            USER_LOGIN: "user",
            BOB_LOGIN: "user",
        },
        companion_extension_origins=list(companion_origins),
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
                    "VALUES ('cats', 'Cats', 1, 1)"
                )
            )
            _insert_media(connection, GENERIC, GENERIC_LOC, "general", "Inbox title")
            _insert_media(connection, MOVIE, MOVIE_LOC, "movie", "Movie title")
            _insert_media(connection, PUBLISH, PUBLISH_LOC, "general", "Website ready")
            _insert_media(
                connection,
                PENDING,
                PENDING_LOC,
                "meme",
                "Pending canonical",
                created_at_ms=30,
            )
            connection.execute(
                text(
                    "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
                    "VALUES (:media, 'cats', 0)"
                ),
                {"media": PUBLISH},
            )
            _insert_run(connection, GENERIC_RUN, GENERIC, GENERIC_LOC, "Inbox stored")
            _insert_run(connection, MOVIE_RUN, MOVIE, MOVIE_LOC, "Movie stored")
            _insert_x_save(connection)
    finally:
        dispose_engine(engine)


def _insert_media(
    connection,
    media_id: str,
    location_id: str,
    category: str,
    title: str | None,
    *,
    created_at_ms: int = 10,
) -> None:
    connection.execute(
        text(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (:id, 'video', :created, :created)"
        ),
        {"id": media_id, "created": created_at_ms},
    )
    connection.execute(
        text(
            "INSERT INTO physical_media_locations ("
            "id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
            ") VALUES (:id, :media, :library, :path, 'available', 8, NULL, 10, 10)"
        ),
        {"id": location_id, "media": media_id, "library": LIBRARY_ID, "path": f"{media_id}.mp4"},
    )
    connection.execute(
        text(
            "INSERT INTO media_metadata ("
            "media_id, display_title, description, created_at_ms, updated_at_ms, "
            "content_category, acquisition_source"
            ") VALUES (:id, :title, 'Desc', 10, 10, :category, 'manual_upload')"
        ),
        {"id": media_id, "title": title, "category": category},
    )


def _insert_x_save(connection) -> None:
    connection.execute(
        text(
            "INSERT INTO x_post_claims ("
            "id, state, acquisition_source, submitted_url, canonical_url, "
            "x_post_id, extractor_key, created_by_login_key, title, "
            "discovered_asset_count, success_count, failure_count, "
            "created_at_ms, updated_at_ms, completed_at_ms, cleanup_state, "
            "cleanup_completed_at_ms, requested_content_category, version"
            ") VALUES ("
            ":id, 'completed', 'x_manual_claim', :url, :url, '123456789', 'X', "
            ":owner, 'Pending claim', 1, 1, 0, 10, 20, 20, 'complete', 20, "
            "'meme', 1)"
        ),
        {
            "id": PENDING_CLAIM,
            "url": "https://x.com/a/status/123456789",
            "owner": ADMIN_LOGIN,
        },
    )
    connection.execute(
        text(
            "INSERT INTO x_assets ("
            "id, claim_id, ordinal, media_type, expected_mime, state, stage_key, "
            "media_id, media_location_id, created_at_ms, updated_at_ms, "
            "completed_at_ms, cleanup_state, cleanup_completed_at_ms, version"
            ") VALUES ("
            ":id, :claim, 0, 'video', 'video/mp4', 'cataloged', :stage, "
            ":media, :location, 10, 20, 20, 'complete', 20, 1)"
        ),
        {
            "id": PENDING_ASSET,
            "claim": PENDING_CLAIM,
            "stage": "e" * 32,
            "media": PENDING,
            "location": PENDING_LOC,
        },
    )


def _insert_run(connection, run_id: str, media_id: str, location_id: str, title: str) -> None:
    connection.execute(
        text(
            "INSERT INTO media_analysis_runs ("
            "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
            "provider_id, model_id, prompt_version, result_schema_version, result_json, "
            "error_code, error_message, analysis_profile, created_at_ms, started_at_ms, "
            "completed_at_ms, version"
            ") VALUES ("
            ":id, :media, :location, 'automatic_post_catalog', 'analyzed', 1, "
            "'nvidia-nim', 'test-model', 'framenest-media-suggestion-v4', "
            "'framenest-media-suggestion-result-v1', :result, "
            "NULL, NULL, 'generic_media', 20, 21, 22, 2)"
        ),
        {
            "id": run_id,
            "media": media_id,
            "location": location_id,
            "result": _result_json(title),
        },
    )


def test_admin_list_and_detail_are_no_store_and_ordinary_is_forbidden(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert admin.status_code == 200
        assert admin.headers.get("cache-control") == "no-store"
        payload = admin.json()
        assert payload["unopened_count"] == 1
        assert [item["media_id"] for item in payload["items"]] == [GENERIC]
        analyzed = payload["items"][0]
        assert analyzed["created_at_ms"] == 10
        assert analyzed["analyzed"] is True
        assert analyzed["analysis_run_id"] == GENERIC_RUN
        assert analyzed["completed_at_ms"] == 22
        assert analyzed["unopened"] is True
        assert "collection" not in admin.text
        assert "suggested_filename" not in admin.text
        ordinary = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(USER_LOGIN, "Owner"),
        )
        assert ordinary.status_code == 403
        assert "Inbox title" not in ordinary.text
        assert ordinary.json()["error"]["code"] == "CAPABILITY_DENIED"
        ordinary_detail = client.get(
            f"/api/companion/review-inbox/{GENERIC}",
            headers=_serve_headers(USER_LOGIN, "Owner"),
        )
        assert ordinary_detail.status_code == 403
        own_history = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(USER_LOGIN, "Owner"),
        )
        assert own_history.status_code == 200
        assert own_history.headers.get("cache-control") == "no-store"
        assert own_history.json()["items"] == []
        assert own_history.json()["unopened_count"] == 0
        admin_own = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert admin_own.status_code == 200
        assert [item["media_id"] for item in admin_own.json()["items"]] == [PENDING]
        assert admin_own.json()["items"][0]["analyzed"] is False
        assert admin_own.json()["items"][0]["unopened"] is False
        assert admin_own.json()["unopened_count"] == 0
        detail = client.get(
            f"/api/companion/review-inbox/{GENERIC}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert detail.status_code == 200
        assert detail.headers.get("cache-control") == "no-store"
        body = detail.json()
        assert body["suggestions"][0]["title"] == "Inbox stored"
        assert body["canonical"]["field_sources"]["display_title"] is None
        assert body["canonical"]["tag_sources"] == {}
        assert "collection" not in body["suggestions"][0]
        pending_detail = client.get(
            f"/api/companion/review-inbox/{PENDING}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert pending_detail.status_code == 200
        assert pending_detail.json()["canonical"]["display_title"] == "Pending canonical"
        assert pending_detail.json()["suggestions"] == []


def test_bad_cursor_is_422_and_movie_detail_is_409(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bad = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
            params={"cursor": "%%%"},
        )
        assert bad.status_code == 422
        assert bad.headers.get("cache-control") == "no-store"
        movie = client.get(
            f"/api/companion/review-inbox/{MOVIE}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert movie.status_code == 409
        assert "Movie stored" not in movie.text
        assert movie.json()["error"]["code"] == "COMPANION_REVIEW_MOVIE_EXCLUDED"
        missing = client.get(
            f"/api/companion/review-inbox/{MISSING}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert missing.status_code == 404
        stale = encode_companion_review_cursor(
            completed_at_ms=1, analysis_run_id=GENERIC_RUN
        )
        valid_cursor = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
            params={"cursor": stale},
        )
        assert valid_cursor.status_code == 200


def test_opened_and_apply_contracts(tmp_path: Path) -> None:
    with _client(tmp_path, companion_origins=(COMPANION_ORIGIN,)) as client:
        ordinary_opened = client.post(
            f"/api/companion/review-inbox/{GENERIC}/opened",
            headers=_mutation_headers(USER_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": GENERIC_RUN},
        )
        assert ordinary_opened.status_code == 404
        assert ordinary_opened.json()["error"]["code"] == "MEDIA_NOT_FOUND"
        ordinary_apply = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(USER_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert ordinary_apply.status_code == 403
        assert ordinary_apply.json()["error"]["code"] == "CAPABILITY_DENIED"
        extra_apply = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
                "display_title": "client text",
            },
        )
        assert extra_apply.status_code == 422
        empty_tags = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["tags"],
                "tag_keys": [],
            },
        )
        assert empty_tags.status_code == 422
        opened = client.post(
            f"/api/companion/review-inbox/{GENERIC}/opened",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": GENERIC_RUN},
        )
        assert opened.status_code == 200
        assert opened.headers.get("cache-control") == "no-store"
        assert opened.json()["opened_run_id"] == GENERIC_RUN
        assert opened.json()["unopened"] is False
        listed = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert listed.json()["items"][0]["unopened"] is False
        not_ready = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert not_ready.status_code == 200
        assert not_ready.json()["publication"]["status"] == "not_ready"
        assert not_ready.json()["publication"]["state"] == "unpublished"
        gallery = client.get("/api/media", headers=_serve_headers(USER_LOGIN, "Owner"))
        assert GENERIC not in {item["media_id"] for item in gallery.json()["items"]}
        applied = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["tags"],
                "tag_keys": ["cats"],
            },
        )
        assert applied.status_code == 200
        assert applied.json()["publication"]["status"] == "requires_administrator_publish"
        assert applied.json()["publication"]["state"] == "unpublished"
        assert applied.json()["publication"]["origin"] is None
        assert applied.json()["publication"]["ready"] is True
        assert applied.json()["canonical"]["field_sources"]["tags"] is not None
        assert "cats" in applied.json()["canonical"]["tag_sources"]
        assert applied.json()["canonical"]["tag_sources"]["cats"]["analysis_run_id"] == (
            GENERIC_RUN
        )
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.connect() as db:
                publications = db.execute(
                    text("SELECT COUNT(*) FROM media_content_publications")
                ).scalar_one()
            assert int(publications) == 0
        finally:
            dispose_engine(engine)
        gallery_after_apply = client.get(
            "/api/media", headers=_serve_headers(USER_LOGIN, "Owner")
        )
        assert GENERIC not in {
            item["media_id"] for item in gallery_after_apply.json()["items"]
        }
        published = client.put(
            f"/api/admin/media/{GENERIC}/content-publication",
            headers=_mutation_headers(ADMIN_LOGIN),
        )
        assert published.status_code == 201
        assert published.json()["status"] == "published"
        assert published.json()["publication"]["publication_origin"] == "admin_explicit"
        gallery_after_publish = client.get(
            "/api/media", headers=_serve_headers(USER_LOGIN, "Owner")
        )
        assert GENERIC in {
            item["media_id"] for item in gallery_after_publish.json()["items"]
        }
        already = client.put(
            f"/api/admin/media/{GENERIC}/content-publication",
            headers=_mutation_headers(ADMIN_LOGIN),
        )
        assert already.status_code == 200
        assert already.json()["status"] == "already_published"
        apply_after_publish = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["tags"],
                "tag_keys": ["cats"],
            },
        )
        assert apply_after_publish.status_code == 200
        assert apply_after_publish.json()["publication"]["status"] == "already_published"
        assert apply_after_publish.json()["publication"]["origin"] == "admin_explicit"
        assert apply_after_publish.json()["publication"]["state"] == "published"
        gallery_after_repeat_apply = client.get(
            "/api/media", headers=_serve_headers(USER_LOGIN, "Owner")
        )
        assert GENERIC in {
            item["media_id"] for item in gallery_after_repeat_apply.json()["items"]
        }
        website = client.put(
            f"/api/admin/media/{PUBLISH}/content-publication",
            headers=_mutation_headers(ADMIN_LOGIN),
        )
        assert website.status_code == 201
        assert website.json()["publication"]["publication_origin"] == "admin_explicit"
        movie = client.post(
            f"/api/companion/review-inbox/{MOVIE}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": MOVIE_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert movie.status_code == 409
        missing = client.post(
            f"/api/companion/review-inbox/{MISSING}/opened",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": GENERIC_RUN},
        )
        assert missing.status_code == 404


def test_historical_companion_review_origin_remains_readable(tmp_path: Path) -> None:
    with _client(tmp_path, companion_origins=(COMPANION_ORIGIN,)) as client:
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO media_content_publications "
                        "(media_id, published_at_ms, publication_origin) "
                        "VALUES (:media, 99, 'companion_review')"
                    ),
                    {"media": GENERIC},
                )
        finally:
            dispose_engine(engine)
        detail = client.get(
            f"/api/companion/review-inbox/{GENERIC}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert detail.status_code == 200
        assert detail.json()["publication"]["state"] == "published"
        assert detail.json()["publication"]["origin"] == "companion_review"
        assert detail.json()["publication"]["published_at_ms"] == 99
        gallery = client.get("/api/media", headers=_serve_headers(USER_LOGIN, "Owner"))
        assert GENERIC in {item["media_id"] for item in gallery.json()["items"]}
        applied = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert applied.status_code == 200
        assert applied.json()["publication"]["status"] == "already_published"
        assert applied.json()["publication"]["origin"] == "companion_review"
        assert applied.json()["publication"]["published_at_ms"] == 99
        gallery_after = client.get(
            "/api/media", headers=_serve_headers(USER_LOGIN, "Owner")
        )
        assert GENERIC in {item["media_id"] for item in gallery_after.json()["items"]}


def test_historical_companion_review_origin_unpublishes_and_history_remains(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, companion_origins=(COMPANION_ORIGIN,)) as client:
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO media_content_publications "
                        "(media_id, published_at_ms, publication_origin) "
                        "VALUES (:media, 99, 'companion_review')"
                    ),
                    {"media": GENERIC},
                )
        finally:
            dispose_engine(engine)
        applied = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert applied.status_code == 200
        assert applied.json()["publication"]["origin"] == "companion_review"
        before = client.get(
            f"/api/companion/review-inbox/{GENERIC}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert before.status_code == 200
        unpublished = client.put(
            f"/api/admin/media/{GENERIC}/content-publication",
            headers=_mutation_headers(ADMIN_LOGIN),
            json={"published": False},
        )
        assert unpublished.status_code == 200
        assert unpublished.json()["status"] == "unpublished"
        assert unpublished.json()["publication"] is None
        gallery = client.get("/api/media", headers=_serve_headers(USER_LOGIN, "Owner"))
        assert GENERIC not in {item["media_id"] for item in gallery.json()["items"]}
        after = client.get(
            f"/api/companion/review-inbox/{GENERIC}",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert after.status_code == 200
        assert after.json()["publication"]["state"] == "unpublished"
        assert after.json()["publication"]["origin"] is None
        assert after.json()["canonical"]["display_title"] == before.json()["canonical"][
            "display_title"
        ]
        assert after.json()["canonical"]["field_sources"] == before.json()["canonical"][
            "field_sources"
        ]
        assert after.json()["suggestions"] == before.json()["suggestions"]
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.connect() as db:
                publications = db.execute(
                    text(
                        "SELECT COUNT(*) FROM media_content_publications "
                        "WHERE media_id = :media"
                    ),
                    {"media": GENERIC},
                ).scalar_one()
                receipts = db.execute(
                    text(
                        "SELECT COUNT(*) FROM companion_review_field_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": GENERIC},
                ).scalar_one()
                runs = db.execute(
                    text(
                        "SELECT COUNT(*) FROM media_analysis_runs "
                        "WHERE media_id = :media"
                    ),
                    {"media": GENERIC},
                ).scalar_one()
            assert int(publications) == 0
            assert int(receipts) >= 1
            assert int(runs) == 1
        finally:
            dispose_engine(engine)


def test_audit_failure_blocks_opened_and_apply(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            connection.execute("DROP TABLE security_audit_events")
            connection.commit()
        finally:
            connection.close()
        opened = client.post(
            f"/api/companion/review-inbox/{GENERIC}/opened",
            headers=_mutation_headers(),
            json={"analysis_run_id": GENERIC_RUN},
        )
        apply = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert opened.status_code == 500
        assert opened.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
        assert apply.status_code == 500
        assert apply.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
        listed = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        analyzed = next(
            item for item in listed.json()["items"] if item["media_id"] == GENERIC
        )
        assert analyzed["unopened"] is True
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.connect() as db:
                receipts = db.execute(
                    text("SELECT COUNT(*) FROM companion_review_field_sources")
                ).scalar_one()
                publications = db.execute(
                    text("SELECT COUNT(*) FROM media_content_publications")
                ).scalar_one()
            assert int(receipts) == 0
            assert int(publications) == 0
        finally:
            dispose_engine(engine)


def test_apply_tag_limit_conflict_is_409_and_does_not_write(tmp_path: Path) -> None:
    with _client(tmp_path, companion_origins=(COMPANION_ORIGIN,)) as client:
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.begin() as connection:
                for index in range(1, 33):
                    key = f"extra-{index:02d}"
                    connection.execute(
                        text(
                            "INSERT INTO canonical_tags "
                            "(key, display_name, created_at_ms, updated_at_ms) "
                            "VALUES (:key, :name, 1, 1)"
                        ),
                        {"key": key, "name": f"Extra {index:02d}"},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO media_canonical_tags "
                            "(media_id, tag_key, position) VALUES (:media, :key, :position)"
                        ),
                        {"media": GENERIC, "key": key, "position": index - 1},
                    )
        finally:
            dispose_engine(engine)
        overflow = client.post(
            f"/api/companion/review-inbox/{GENERIC}/apply",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": GENERIC_RUN,
                "fields": ["tags"],
                "tag_keys": ["cats"],
            },
        )
        assert overflow.status_code == 409
        assert overflow.json()["error"]["code"] == "COMPANION_REVIEW_TAG_LIMIT_CONFLICT"
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.connect() as connection:
                tags = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM media_canonical_tags WHERE media_id = :media"
                    ),
                    {"media": GENERIC},
                ).scalar_one()
                sources = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM companion_review_tag_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": GENERIC},
                ).scalar_one()
                receipts = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM companion_review_field_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": GENERIC},
                ).scalar_one()
            assert int(tags) == 32
            assert int(sources) == 0
            assert int(receipts) == 0
        finally:
            dispose_engine(engine)


def test_own_history_opened_alice_bob_admin_isolation(tmp_path: Path) -> None:
    alice_media = "a1111111-1111-4111-8111-111111111111"
    alice_loc = "a2111111-1111-4111-8111-111111111111"
    alice_run = "a3111111-1111-4111-8111-111111111111"
    alice_claim = "a4111111-1111-4111-8111-111111111111"
    alice_asset = "a5111111-1111-4111-8111-111111111111"
    bob_media = "b1111111-1111-4111-8111-111111111111"
    bob_loc = "b2111111-1111-4111-8111-111111111111"
    bob_run = "b3111111-1111-4111-8111-111111111111"
    bob_claim = "b4111111-1111-4111-8111-111111111111"
    bob_asset = "b5111111-1111-4111-8111-111111111111"
    with _client(tmp_path, companion_origins=(COMPANION_ORIGIN,)) as client:
        engine = create_sqlite_engine(tmp_path / "catalog.sqlite3")
        try:
            with engine.begin() as connection:
                _insert_media(connection, alice_media, alice_loc, "meme", "Alice title")
                _insert_run(connection, alice_run, alice_media, alice_loc, "Alice stored")
                _insert_owned_x(
                    connection,
                    claim_id=alice_claim,
                    asset_id=alice_asset,
                    media_id=alice_media,
                    location_id=alice_loc,
                    owner=USER_LOGIN,
                    post_id="111000111",
                    stage_key="a" * 32,
                )
                _insert_media(connection, bob_media, bob_loc, "meme", "Bob title")
                _insert_run(connection, bob_run, bob_media, bob_loc, "Bob stored")
                _insert_owned_x(
                    connection,
                    claim_id=bob_claim,
                    asset_id=bob_asset,
                    media_id=bob_media,
                    location_id=bob_loc,
                    owner=BOB_LOGIN,
                    post_id="111000222",
                    stage_key="b" * 32,
                )
        finally:
            dispose_engine(engine)
        alice_history = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(USER_LOGIN, "Alice"),
        )
        bob_history = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(BOB_LOGIN, "Bob"),
        )
        admin_inbox = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert alice_history.status_code == 200
        assert [item["media_id"] for item in alice_history.json()["items"]] == [
            alice_media
        ]
        assert alice_history.json()["items"][0]["unopened"] is True
        assert alice_history.json()["unopened_count"] == 1
        assert bob_media not in {
            item["media_id"] for item in alice_history.json()["items"]
        }
        assert bob_history.json()["unopened_count"] == 1
        assert bob_history.json()["items"][0]["unopened"] is True
        admin_ids = {item["media_id"] for item in admin_inbox.json()["items"]}
        assert alice_media in admin_ids
        assert bob_media in admin_ids
        alice_open = client.post(
            f"/api/companion/review-inbox/{alice_media}/opened",
            headers=_mutation_headers(USER_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": alice_run},
        )
        assert alice_open.status_code == 200
        assert alice_open.json()["unopened"] is False
        alice_after = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(USER_LOGIN, "Alice"),
        )
        bob_after = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(BOB_LOGIN, "Bob"),
        )
        admin_after = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(ADMIN_LOGIN, "Admin"),
        )
        assert alice_after.json()["items"][0]["unopened"] is False
        assert alice_after.json()["unopened_count"] == 0
        assert bob_after.json()["items"][0]["unopened"] is True
        assert bob_after.json()["unopened_count"] == 1
        admin_alice = next(
            item
            for item in admin_after.json()["items"]
            if item["media_id"] == alice_media
        )
        assert admin_alice["unopened"] is True
        admin_open = client.post(
            f"/api/companion/review-inbox/{alice_media}/opened",
            headers=_mutation_headers(ADMIN_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": alice_run},
        )
        assert admin_open.status_code == 200
        alice_after_admin = client.get(
            "/api/companion/own-history",
            headers=_serve_headers(USER_LOGIN, "Alice"),
        )
        assert alice_after_admin.json()["items"][0]["unopened"] is False
        bob_id = client.post(
            f"/api/companion/review-inbox/{bob_media}/opened",
            headers=_mutation_headers(USER_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": bob_run},
        )
        assert bob_id.status_code == 404
        assert bob_id.json()["error"]["code"] == "MEDIA_NOT_FOUND"
        unknown = client.post(
            f"/api/companion/review-inbox/{MISSING}/opened",
            headers=_mutation_headers(USER_LOGIN, COMPANION_ORIGIN),
            json={"analysis_run_id": alice_run},
        )
        assert unknown.status_code == 404
        apply_denied = client.post(
            f"/api/companion/review-inbox/{alice_media}/apply",
            headers=_mutation_headers(USER_LOGIN, COMPANION_ORIGIN),
            json={
                "analysis_run_id": alice_run,
                "fields": ["display_title"],
                "tag_keys": [],
            },
        )
        assert apply_denied.status_code == 403
        assert apply_denied.json()["error"]["code"] == "CAPABILITY_DENIED"
        bob_inbox = client.get(
            "/api/companion/review-inbox",
            headers=_serve_headers(BOB_LOGIN, "Bob"),
        )
        assert bob_inbox.status_code == 403


def _insert_owned_x(
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
            "x_post_id, extractor_key, created_by_login_key, title, "
            "discovered_asset_count, success_count, failure_count, "
            "created_at_ms, updated_at_ms, completed_at_ms, cleanup_state, "
            "cleanup_completed_at_ms, requested_content_category, version"
            ") VALUES ("
            ":id, 'completed', 'x_manual_claim', :url, :url, :post_id, 'X', "
            ":owner, 'Owned claim', 1, 1, 0, 10, 20, 20, 'complete', 20, "
            "'meme', 1)"
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
            ":id, :claim, 0, 'video', 'video/mp4', 'cataloged', :stage, "
            ":media, :location, 10, 20, 20, 'complete', 20, 1)"
        ),
        {
            "id": asset_id,
            "claim": claim_id,
            "stage": stage_key,
            "media": media_id,
            "location": location_id,
        },
    )


def _run_count(engine) -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(text("SELECT COUNT(*) FROM media_analysis_runs")).scalar_one()
        )


class _JoinTestPreparer:
    def __init__(self) -> None:
        self.calls = 0

    def prepare(self, root, relative_path):
        del root, relative_path
        self.calls += 1
        frame = build_representative_frame(
            timestamp_ms=100, payload=PNG_SIGNATURE + b"frame"
        )
        return PreparedAnalysisResult(
            relative_path=MediaRelativePath("clip.mp4"),
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=TechnicalMetadata(
                duration_ms=1000,
                width=64,
                height=48,
                video_codec="h264",
                container_formats=("mp4",),
                has_audio=False,
            ),
            representative_frames=(frame,),
            requested_frame_count=REQUESTED_FRAME_COUNT,
            warnings=(),
            ffprobe_version="ffprobe version hidden",
            ffmpeg_version="ffmpeg version hidden",
        )


class _JoinTestProvider:
    def __init__(self) -> None:
        self.calls = 0

    def suggest(self, request) -> MediaSuggestion:
        del request
        self.calls += 1
        return MediaSuggestion(
            title="Preview join title",
            description="Preview join description for companion history.",
            collection="Meme",
            tags=("Cats",),
            suggested_filename="preview-join.mp4",
            confidence=0.9,
            evidence=("Visible subject",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="test-model",
            prompt_version=PROMPT_VERSION,
        )


def test_imported_preview_joins_inbox_and_own_history(tmp_path: Path) -> None:
    alice_media = "c1111111-1111-4111-8111-111111111111"
    alice_loc = "c2111111-1111-4111-8111-111111111111"
    alice_claim = "c4111111-1111-4111-8111-111111111111"
    alice_asset = "c5111111-1111-4111-8111-111111111111"
    extra_movie = "c2222222-2222-4222-8222-222222222222"
    extra_movie_loc = "c3222222-2222-4222-8222-222222222222"
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={
            ADMIN_LOGIN: "admin",
            USER_LOGIN: "user",
            BOB_LOGIN: "user",
        },
        companion_extension_origins=[COMPANION_ORIGIN],
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed(settings.database_path)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.begin() as connection:
            _insert_media(connection, alice_media, alice_loc, "meme", "Alice preview")
            _insert_owned_x(
                connection,
                claim_id=alice_claim,
                asset_id=alice_asset,
                media_id=alice_media,
                location_id=alice_loc,
                owner=USER_LOGIN,
                post_id="111000333",
                stage_key="c" * 32,
            )
            _insert_media(
                connection, extra_movie, extra_movie_loc, "movie", "Movie preview"
            )
        media_repository = SqliteMediaRepository(engine)
        library_repository = SqliteLibraryRepository(engine)
        preparer = _JoinTestPreparer()
        provider = _JoinTestProvider()
        imported = PreviewImportedMediaSuggestion(
            media_repository,
            library_repository,
            preparer,
            provider,
            PersistImportedPreviewAnalysis(
                SqliteMediaAnalysisRunRepository(engine),
                SqliteMediaMetadataRepository(engine),
            ),
        )
        library_preview = PreviewMediaSuggestion(
            library_repository,
            preparer,
            provider,
        )
        with TestClient(
            create_app(
                settings=settings,
                media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
                    preview_suggestion=library_preview,
                    preview_imported_suggestion=imported,
                    provider_configured=True,
                    credential_available=True,
                    status="available",
                    audience_policy=__import__(
                        "tests.support.record_access", fromlist=["scoped_policy"]
                    ).scoped_policy({alice_media, extra_movie}),
                ),
            )
        ) as client:
            before = _run_count(engine)
            first = client.post(
                f"/api/media/{alice_media}/locations/{alice_loc}/ai-suggestion-preview",
                headers=_mutation_headers(ADMIN_LOGIN),
                json={"confirm_cloud_upload": True},
            )
            assert first.status_code == 200
            assert provider.calls == 1
            after_first = _run_count(engine)
            assert after_first == before + 1
            second = client.post(
                f"/api/media/{alice_media}/locations/{alice_loc}/ai-suggestion-preview",
                headers=_mutation_headers(ADMIN_LOGIN),
                json={"confirm_cloud_upload": True},
            )
            assert second.status_code == 200
            assert provider.calls == 2
            after_second = _run_count(engine)
            assert after_second == after_first + 1
            movie_preview = client.post(
                f"/api/media/{extra_movie}/locations/{extra_movie_loc}/ai-suggestion-preview",
                headers=_mutation_headers(ADMIN_LOGIN),
                json={"confirm_cloud_upload": True},
            )
            assert movie_preview.status_code == 200
            assert provider.calls == 3
            assert _run_count(engine) == after_second
            library_response = client.post(
                f"/api/libraries/{LIBRARY_ID}/media-suggestion-preview",
                headers=_mutation_headers(ADMIN_LOGIN),
                json={
                    "relative_path": f"{GENERIC}.mp4",
                    "confirm_cloud_upload": True,
                },
            )
            assert library_response.status_code == 200
            assert provider.calls == 4
            assert _run_count(engine) == after_second
            inbox = client.get(
                "/api/companion/review-inbox",
                headers=_serve_headers(ADMIN_LOGIN, "Admin"),
            )
            assert inbox.status_code == 200
            inbox_items = inbox.json()["items"]
            inbox_ids = {item["media_id"] for item in inbox_items}
            assert alice_media in inbox_ids
            assert extra_movie not in inbox_ids
            alice_inbox = next(
                item for item in inbox_items if item["media_id"] == alice_media
            )
            assert alice_inbox["analyzed"] is True
            assert alice_inbox["unopened"] is True
            alice_history = client.get(
                "/api/companion/own-history",
                headers=_serve_headers(USER_LOGIN, "Alice"),
            )
            assert alice_history.status_code == 200
            assert [item["media_id"] for item in alice_history.json()["items"]] == [
                alice_media
            ]
            assert alice_history.json()["items"][0]["analyzed"] is True
            assert alice_history.json()["items"][0]["unopened"] is True
            assert alice_history.json()["unopened_count"] == 1
            bob_history = client.get(
                "/api/companion/own-history",
                headers=_serve_headers(BOB_LOGIN, "Bob"),
            )
            assert bob_history.status_code == 200
            assert alice_media not in {
                item["media_id"] for item in bob_history.json()["items"]
            }
            assert bob_history.json()["unopened_count"] == 0
            with engine.begin() as connection:
                latest = connection.execute(
                    text(
                        "SELECT analysis_definition, analysis_profile, state "
                        "FROM media_analysis_runs WHERE media_id = :media "
                        "ORDER BY completed_at_ms DESC, id DESC LIMIT 1"
                    ),
                    {"media": alice_media},
                ).mappings().one()
            assert latest["analysis_definition"] == "automatic_post_catalog"
            assert latest["analysis_profile"] == "generic_media"
            assert latest["state"] == "analyzed"
    finally:
        dispose_engine(engine)


def test_analysis_run_id_parsing_returns_analysis_run_identity() -> None:
    from kronika.application.companion_review import _parse_analysis_run_id
    from kronika.domain.identities import MediaId
    from kronika.domain.media_analysis_runs import MediaAnalysisRunId

    parsed = _parse_analysis_run_id(GENERIC_RUN)
    assert isinstance(parsed, MediaAnalysisRunId)
    assert not isinstance(parsed, MediaId)
    assert parsed.to_string() == GENERIC_RUN


def test_analysis_run_id_parsing_keeps_sanitized_failure_contract() -> None:
    from kronika.application.companion_review import _parse_analysis_run_id
    from kronika.application.ports.companion_review_repository import (
        CompanionReviewAnalysisRunNotFoundError,
    )

    for invalid in ("not-a-run-id", "", "91111111-1111-1111-8111-111111111111"):
        try:
            _parse_analysis_run_id(invalid)
        except CompanionReviewAnalysisRunNotFoundError:
            continue
        raise AssertionError(f"{invalid!r} did not raise the sanitized not-found error")

