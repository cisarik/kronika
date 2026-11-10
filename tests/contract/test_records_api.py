"""Records API evidence: history, Timeline, detail, render and approval."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.gallery_preview_api import GalleryPreviewApiDependencies
from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.application.ports.gallery_preview import OpenedGalleryPreview
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
from kronika.domain.records import (
    CompletedDocument,
    DocumentId,
    RecordId,
    RecordKind,
)
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_immediate_transaction,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import (
    SqliteRecordRepository,
    note_successful_analysis,
)
from tests.support.record_access import install_synthetic_caller

OWNER = "alice@example.com"


class _Audit:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            scope[SCOPE_AUDIT_EVENT_ID] = "records-api-audit"
        await self.app(scope, receive, send)


def _client(app: FastAPI, login: str | None, role: str = ROLE_USER) -> TestClient:
    if login is not None:
        install_synthetic_caller(app, login, role=role)
    app.add_middleware(_Audit)
    return TestClient(app, client=("127.0.0.1", 9))


@pytest.fixture()
def records(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "records-api.sqlite3",
        identity_map={
            "alice@example.com": "user",
            "bob@example.com": "user",
            "ada@example.com": "admin",
        },
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-records-api-1",
        kind=RecordKind.SEARCH,
        question_text="What is <script>alert(1)</script>?",
        answer_text="The **answer** stays safe.",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=1_000,
        completed_at_ms=2_000,
    )
    repository = SqliteRecordRepository(engine)
    detail = repository.create_completed_document(
        document,
        owner_login_key=OWNER,
        record_id=RecordId.new(),
    )
    record_id = detail.summary.record_id

    def build(login: str | None, role: str = ROLE_USER) -> TestClient:
        return _client(create_app(settings=settings), login, role)

    try:
        yield {
            "settings": settings,
            "record_id": record_id,
            "alice": build(OWNER),
            "bob": build("bob@example.com"),
            "ada": build("ada@example.com", ROLE_ADMIN),
            "anonymous": build(None),
        }
    finally:
        dispose_engine(engine)


def test_owner_reads_detail_and_safe_render(records) -> None:
    detail = records["alice"].get(f"/api/records/{records['record_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["record"]["kind"] == "search"
    assert body["document"]["question_text"].startswith("What is")
    rendered = records["alice"].get(f"/api/records/{records['record_id']}/render")
    assert rendered.status_code == 200
    assert rendered.headers["x-content-type-options"] == "nosniff"
    assert "&lt;script&gt;" in rendered.text
    assert "<script>" not in rendered.text
    assert "<strong>answer</strong>" in rendered.text
    assert "default-src 'none'" in rendered.headers["content-security-policy"]


def test_household_member_sees_only_the_approved_projection(records) -> None:
    record_id = records["record_id"]
    assert records["bob"].get(f"/api/records/{record_id}").status_code == 404
    assert records["bob"].get("/api/my/records").json()["total"] == 0
    candidate = records["ada"].get("/api/admin/records").json()
    assert candidate["total"] == 1
    detail = records["alice"].get(f"/api/my/records").json()
    assert detail["total"] == 1
    version = records["ada"].get(f"/api/records/{record_id}").json()["version"]
    approval = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "approve", "expected_version": version},
    )
    assert approval.status_code == 200
    assert approval.json()["changed"] is True
    household = records["bob"].get(f"/api/records/{record_id}")
    assert household.status_code == 200
    assert household.json()["record"]["read_decision"] == "approved"
    timeline = records["bob"].get("/api/timeline").json()
    assert timeline["total"] == 1
    rendered = records["bob"].get(f"/api/records/{record_id}/render")
    assert rendered.status_code == 200
    assert "answer" in rendered.text


def test_stale_version_conflicts_and_users_cannot_approve(records) -> None:
    record_id = records["record_id"]
    stale = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "approve", "expected_version": 999},
    )
    assert stale.status_code == 409
    denied = records["alice"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "approve", "expected_version": 1},
    )
    assert denied.status_code == 403
    unsupported = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "reject", "expected_version": 1},
    )
    assert unsupported.status_code == 422


def test_anonymous_is_denied_everywhere(records) -> None:
    record_id = records["record_id"]
    assert records["anonymous"].get("/api/my/records").status_code == 401
    assert records["anonymous"].get("/api/timeline").status_code == 401
    assert records["anonymous"].get(f"/api/records/{record_id}").status_code == 401
    assert records["anonymous"].get("/api/admin/records").status_code == 401


def test_summaries_keep_hostile_titles_and_omit_answers(records) -> None:
    page = records["alice"].get("/api/my/records")
    assert page.status_code == 200
    item = page.json()["items"][0]
    assert "<script>" in item["display_title"]
    assert item["content_category"] is None
    assert "answer_text" not in item
    assert "stays safe" not in page.text
    assert records["ada"].get("/api/my/records").json()["total"] == 0
    assert records["bob"].get(f"/api/records/{records['record_id']}").status_code == 404
    assert records["bob"].get(f"/api/records/{records['record_id']}/render").status_code == 404


def test_timeline_membership_follows_owner_admin_and_household_visibility(records) -> None:
    record_id = records["record_id"]
    assert records["bob"].get("/api/timeline").json()["total"] == 0
    for name in ("alice", "ada"):
        private = records[name].get("/api/timeline")
        assert private.status_code == 200
        assert private.headers["cache-control"] == "private, no-store"
        assert private.json()["total"] == 1
        assert private.json()["items"][0]["record_id"] == record_id
        assert private.json()["items"][0]["read_decision"] == "current"
        assert private.json()["items"][0]["visibility"] == "private"
    version = records["ada"].get(f"/api/records/{record_id}").json()["version"]
    approval = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "approve", "expected_version": version},
    )
    assert approval.status_code == 200
    for name, decision in (("alice", "current"), ("ada", "current"), ("bob", "approved")):
        timeline = records[name].get("/api/timeline").json()
        assert timeline["total"] == 1
        assert timeline["items"][0]["read_decision"] == decision
        assert timeline["items"][0]["display_title"]
    stale = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "withdraw", "expected_version": version},
    )
    assert stale.status_code == 409
    current = records["ada"].get(f"/api/records/{record_id}").json()["version"]
    withdrawn = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "withdraw", "expected_version": current},
    )
    assert withdrawn.status_code == 200
    assert records["bob"].get("/api/timeline").json()["total"] == 0
    for name in ("alice", "ada"):
        retained = records[name].get("/api/timeline").json()
        assert retained["total"] == 1
        assert retained["items"][0]["publication_state"] == "withdrawn"
    assert records["alice"].get("/api/my/records").json()["total"] == 1


def test_record_list_filters_reject_invalid_combinations(records) -> None:
    private = records["alice"].get("/api/timeline", params={"visibility": "private"})
    assert private.status_code == 200
    assert private.json()["total"] == 1
    assert records["alice"].get("/api/timeline", params={"visibility": "nope"}).status_code == 422
    assert records["alice"].get("/api/my/records", params={"visibility": "private"}).status_code == 422
    assert records["alice"].get(
        "/api/my/records",
        params={"content_category": "meme"},
    ).status_code == 422
    assert records["ada"].get("/api/admin/records", params={"kind": "nope"}).status_code == 422
    private = records["ada"].get("/api/admin/records", params={"visibility": "private"})
    assert private.status_code == 200
    assert private.json()["total"] == 1
    assert private.json()["items"][0]["display_title"]


def test_unready_media_cannot_be_approved(tmp_path: Path) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "records-api.sqlite3",
        identity_map={"alice@example.com": "user", "ada@example.com": "admin"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    media_id = "55555555-5555-4555-8555-555555555555"
    repository = SqliteRecordRepository(engine)
    try:
        def seed(connection) -> None:
            connection.exec_driver_sql(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES ('55555555-5555-4555-8555-555555555555', 'video', 10, 10)"
            )
            repository.bind_media_record(
                connection,
                record_id=RecordId.new(),
                media_id=media_id,
                owner_login_key="alice@example.com",
                created_at_ms=10,
            )

        run_in_immediate_transaction(engine, seed)
    finally:
        dispose_engine(engine)
    ada = _client(create_app(settings=settings), "ada@example.com", ROLE_ADMIN)
    listed = ada.get("/api/admin/records")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    denied = ada.post(
        f"/api/admin/records/{item['record_id']}/approval",
        json={"action": "approve", "expected_version": item["version"]},
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "RECORD_CONFLICT"


_MEDIA_ID = "66000000-0000-4000-8000-000000000001"
_APPROVED_LOCATION_ID = "67000000-0000-4000-8000-000000000001"
_WORKING_LOCATION_ID = "65000000-0000-4000-8000-000000000001"
_RUN_ID = "68000000-0000-4000-8000-000000000001"
_MEDIA_RECORD_ID = "69000000-0000-4000-8000-000000000001"
_MEDIA_DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_MEDIA_LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_PREVIEW_PRIVATE_PATH = "/Users/example/private/cache"
_PREVIEW_JPEG = b"\xff\xd8\xff\xd9"
_PREVIEW_ETAG = '"round-trip"'
_SUMMARY_KEYS = {
    "record_id",
    "kind",
    "owner_login_key",
    "visibility",
    "created_at_ms",
    "completed_at_ms",
    "timeline_entered_at_ms",
    "version",
    "media_id",
    "read_decision",
    "display_title",
    "content_category",
}


@dataclass
class _RecordingPreviewService:
    calls: list[tuple[str, str]] = field(default_factory=list)
    error: Exception | None = None

    def open_ready(self, media_id, location_id):
        self.calls.append((str(media_id), str(location_id)))
        if self.error is not None:
            raise self.error
        return OpenedGalleryPreview(
            media_type="image/jpeg",
            byte_size=len(_PREVIEW_JPEG),
            etag=_PREVIEW_ETAG,
            payload=_PREVIEW_JPEG,
            close=lambda: None,
        )


class _UnboundMediaRepository:
    """The bound-record decision runs before this fallback for synthetic media."""

    def media_exists(self, media_id) -> bool:
        return False

    def is_published(self, media_id) -> bool:
        return False


def _suggestion_json(title: str) -> str:
    return json.dumps(
        {
            "title": title,
            "description": "Synthetic description",
            "collection": "inbox",
            "tags": ["clip"],
            "suggested_filename": "clip.mp4",
            "confidence": 0.5,
            "evidence": ["frame"],
            "uncertainties": ["none"],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _seed_media_catalog(settings: KronikaSettings, engine) -> None:
    repository = SqliteRecordRepository(engine)
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic')",
            (_MEDIA_DEVICE_ID,),
        )
        connection.execute(
            "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, 'Synthetic', 'posix', ?)",
            (_MEDIA_LIBRARY_ID, _MEDIA_DEVICE_ID, "/tmp/synthetic-library"),
        )
        connection.execute(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (?, 'video', 10, 10)",
            (_MEDIA_ID,),
        )
        connection.execute(
            "INSERT INTO physical_media_locations ("
            "id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, 'approved.mp4', 'available', 8, 8, 10, 10)",
            (_APPROVED_LOCATION_ID, _MEDIA_ID, _MEDIA_LIBRARY_ID),
        )
        connection.execute(
            "INSERT INTO media_metadata ("
            "media_id, display_title, description, content_category, acquisition_source, "
            "created_at_ms, updated_at_ms"
            ") VALUES (?, 'Synthetic title', 'Synthetic description', 'general', 'manual_upload', 10, 10)",
            (_MEDIA_ID,),
        )
        connection.execute(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('clip', 'Clip', 10, 10)"
        )
        connection.execute(
            "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
            "VALUES (?, 'clip', 0)",
            (_MEDIA_ID,),
        )
        connection.execute(
            "INSERT INTO media_analysis_runs ("
            "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
            "provider_id, model_id, prompt_version, result_schema_version, result_json, "
            "analysis_profile, created_at_ms, started_at_ms, completed_at_ms, version"
            ") VALUES (?, ?, ?, 'automatic_post_catalog', 'analyzed', 1, "
            "'synthetic', 'synthetic-model', 'suggestion-v1', "
            "'framenest-media-suggestion-result-v1', ?, 'generic_media', 10, 10, 10, 1)",
            (
                _RUN_ID,
                _MEDIA_ID,
                _APPROVED_LOCATION_ID,
                _suggestion_json("Synthetic title"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    def bind(connection) -> None:
        repository.bind_media_record(
            connection,
            record_id=RecordId.from_string(_MEDIA_RECORD_ID),
            media_id=_MEDIA_ID,
            owner_login_key=OWNER,
            created_at_ms=10,
        )
        note_successful_analysis(connection, _MEDIA_ID, _RUN_ID)

    run_in_immediate_transaction(engine, bind)


@pytest.fixture()
def media_records(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "media-records-api.sqlite3",
        identity_map={
            OWNER: "user",
            "bob@example.com": "user",
            "ada@example.com": "admin",
        },
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    _seed_media_catalog(settings, engine)
    preview = _RecordingPreviewService()
    policy = ContentAudiencePolicy(
        _UnboundMediaRepository(),
        record_bindings=SqliteRecordRepository(engine),
    )

    def build(login: str, role: str = ROLE_USER) -> TestClient:
        dependencies = GalleryPreviewApiDependencies(
            preview_service=preview,
            catalog_available=lambda: True,
            audience_policy=policy,
        )
        return _client(
            create_app(
                settings=settings,
                gallery_preview_api_dependencies=dependencies,
            ),
            login,
            role,
        )

    try:
        yield {
            "settings": settings,
            "engine": engine,
            "record_id": _MEDIA_RECORD_ID,
            "preview": preview,
            "alice": build(OWNER),
            "bob": build("bob@example.com"),
            "ada": build("ada@example.com", ROLE_ADMIN),
        }
    finally:
        dispose_engine(engine)


def _preview_url(location_id: str = _APPROVED_LOCATION_ID) -> str:
    return f"/api/media/{_MEDIA_ID}/locations/{location_id}/gallery-preview"


def _approve(client: TestClient, record_id: str) -> dict[str, object]:
    version = client.get(f"/api/records/{record_id}").json()["version"]
    response = client.post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "approve", "expected_version": version},
    )
    assert response.status_code == 200
    return response.json()


def _withdraw(client: TestClient, record_id: str) -> dict[str, object]:
    version = client.get(f"/api/records/{record_id}").json()["version"]
    response = client.post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "withdraw", "expected_version": version},
    )
    assert response.status_code == 200
    return response.json()


def test_media_list_preview_locator_is_additive_and_path_free(media_records) -> None:
    page = media_records["alice"].get("/api/my/records")
    assert page.status_code == 200
    body = page.json()
    item = body["items"][0]
    assert item["preview_location_id"] == _APPROVED_LOCATION_ID
    assert set(item) == _SUMMARY_KEYS | {"preview_location_id"}
    assert "locations" not in item
    assert "relative_path" not in page.text
    assert _PREVIEW_PRIVATE_PATH not in page.text
    assert "approved.mp4" not in page.text
    timeline = media_records["alice"].get("/api/timeline")
    assert timeline.status_code == 200
    assert "approved.mp4" not in timeline.text
    assert _PREVIEW_PRIVATE_PATH not in timeline.text
    body = timeline.json()
    assert body["total"] == 1
    assert body["items"][0]["preview_location_id"] == _APPROVED_LOCATION_ID
    assert body["items"][0]["read_decision"] == "current"
    assert "owner_login_key" not in body["items"][0]
    assert media_records["bob"].get("/api/timeline").json()["total"] == 0


def test_preview_locator_does_not_change_admin_or_detail_response_shapes(
    media_records,
) -> None:
    detail = media_records["alice"].get(f"/api/records/{_MEDIA_RECORD_ID}")
    assert detail.status_code == 200
    assert set(detail.json()["record"]) == _SUMMARY_KEYS
    inventory = media_records["ada"].get("/api/admin/records")
    assert inventory.status_code == 200
    assert set(inventory.json()["items"][0]) == _SUMMARY_KEYS
    personal = media_records["alice"].get("/api/my/records").json()["items"][0]
    assert set(personal) == _SUMMARY_KEYS | {"preview_location_id"}


def test_list_preview_locator_round_trip_preserves_record_audience(
    media_records,
) -> None:
    preview = media_records["preview"]
    alice = media_records["alice"]
    bob = media_records["bob"]
    ada = media_records["ada"]
    locator = alice.get("/api/my/records").json()["items"][0]["preview_location_id"]
    assert locator == _APPROVED_LOCATION_ID
    owner_preview = alice.get(_preview_url(locator))
    assert owner_preview.status_code == 200
    assert owner_preview.content == _PREVIEW_JPEG
    assert preview.calls == [(_MEDIA_ID, _APPROVED_LOCATION_ID)]
    admin_preview = ada.get(_preview_url(locator))
    assert admin_preview.status_code == 200
    denied = bob.get(_preview_url(locator))
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "GALLERY_PREVIEW_NOT_FOUND"
    assert preview.calls == [
        (_MEDIA_ID, _APPROVED_LOCATION_ID),
        (_MEDIA_ID, _APPROVED_LOCATION_ID),
    ]
    _approve(ada, _MEDIA_RECORD_ID)
    timeline = bob.get("/api/timeline").json()
    assert timeline["total"] == 1
    household_locator = timeline["items"][0]["preview_location_id"]
    assert household_locator == _APPROVED_LOCATION_ID
    household = bob.get(_preview_url(household_locator))
    assert household.status_code == 200
    _withdraw(ada, _MEDIA_RECORD_ID)
    assert bob.get("/api/timeline").json()["total"] == 0
    calls_before = len(preview.calls)
    denied_after_withdrawal = bob.get(
        _preview_url(household_locator),
        headers={"If-None-Match": _PREVIEW_ETAG},
    )
    assert denied_after_withdrawal.status_code == 404
    assert denied_after_withdrawal.headers["cache-control"] == "no-store"
    assert len(preview.calls) == calls_before
    assert alice.get("/api/my/records").json()["items"][0]["preview_location_id"] == _APPROVED_LOCATION_ID
    assert alice.get(_preview_url(household_locator)).status_code == 200


def test_working_only_preview_location_is_not_disclosed_on_timeline(
    media_records,
) -> None:
    ada = media_records["ada"]
    bob = media_records["bob"]
    alice = media_records["alice"]
    _approve(ada, _MEDIA_RECORD_ID)
    connection = sqlite3.connect(media_records["settings"].database_path)
    try:
        connection.execute(
            "INSERT INTO physical_media_locations ("
            "id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, 'working.mp4', 'available', 9, 9, 30, 30)",
            (_WORKING_LOCATION_ID, _MEDIA_ID, _MEDIA_LIBRARY_ID),
        )
        connection.commit()
    finally:
        connection.close()
    timeline = bob.get("/api/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["items"][0]["preview_location_id"] == _APPROVED_LOCATION_ID
    assert _WORKING_LOCATION_ID not in timeline.text
    for client in (alice, ada):
        current = client.get("/api/timeline").json()["items"][0]
        assert current["preview_location_id"] == _WORKING_LOCATION_ID
        assert current["read_decision"] == "current"
    assert alice.get("/api/my/records").json()["items"][0]["preview_location_id"] == _WORKING_LOCATION_ID


def test_withdrawn_preview_is_denied_even_with_a_cached_etag(media_records) -> None:
    preview = media_records["preview"]
    bob = media_records["bob"]
    ada = media_records["ada"]
    _approve(ada, _MEDIA_RECORD_ID)
    allowed = bob.get(_preview_url())
    assert allowed.status_code == 200
    assert allowed.headers["etag"] == _PREVIEW_ETAG
    _withdraw(ada, _MEDIA_RECORD_ID)
    calls_before = len(preview.calls)
    denied = bob.get(
        _preview_url(),
        headers={"If-None-Match": _PREVIEW_ETAG},
    )
    assert denied.status_code == 404
    assert denied.content != b""
    assert denied.headers["cache-control"] == "no-store"
    assert len(preview.calls) == calls_before
