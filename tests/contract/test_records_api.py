"""Records API evidence: history, Timeline, detail, render and approval."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID
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


def test_timeline_membership_is_approved_for_every_caller(records) -> None:
    record_id = records["record_id"]
    for client in (records["alice"], records["bob"], records["ada"]):
        assert client.get("/api/timeline").json()["total"] == 0
    version = records["ada"].get(f"/api/records/{record_id}").json()["version"]
    approval = records["ada"].post(
        f"/api/admin/records/{record_id}/approval",
        json={"action": "approve", "expected_version": version},
    )
    assert approval.status_code == 200
    for client in (records["alice"], records["bob"], records["ada"]):
        timeline = client.get("/api/timeline").json()
        assert timeline["total"] == 1
        assert timeline["items"][0]["read_decision"] == "approved"
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
    for client in (records["alice"], records["bob"], records["ada"]):
        assert client.get("/api/timeline").json()["total"] == 0
    assert records["alice"].get("/api/my/records").json()["total"] == 1


def test_record_list_filters_reject_invalid_combinations(records) -> None:
    assert records["alice"].get("/api/timeline", params={"visibility": "family"}).status_code == 422
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
