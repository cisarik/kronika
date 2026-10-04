"""Durable ordinary-user analysis proposals without provider execution."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.analysis_proposal_api import AnalysisProposalApiDependencies
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    find_route_policy,
)
from kronika.application.analysis_proposal import (
    MS_PER_HOUR,
    ListAnalysisProposals,
    ProposeAnalysis,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_ANALYSIS_PROPOSE,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.infrastructure.persistence.analysis_proposal_repository import (
    SqliteAnalysisProposalRepository,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

ALICE = "alice@example.com"
BOB = "bob@example.com"
ADMIN = "admin@example.com"
MEDIA_A = "11111111-1111-4111-8111-111111111111"
MEDIA_B = "22222222-2222-4222-8222-222222222222"
MEDIA_C = "33333333-3333-4333-8333-333333333333"
UNKNOWN = "88888888-8888-4888-8888-888888888888"
INVALID_ID = "not-a-uuid"
PROPOSE_PATH = f"/api/workspace/media/{MEDIA_A}/analysis-proposals"
LIST_PATH = "/api/admin/analysis-proposals"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _identity(login: str, role: str, *, capabilities: frozenset[str] | None = None) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login.split("@", 1)[0].title(),
        role=role,
        capabilities=capabilities if capabilities is not None else CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


def _client(
    settings: KronikaSettings,
    login: str,
    role: str,
    **identity_kwargs: object,
) -> TestClient:
    app = create_app(settings=settings)

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(login, role, **identity_kwargs)
        request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-event"
        return await call_next(request)

    return TestClient(app)


def _plain_client(settings: KronikaSettings) -> TestClient:
    return TestClient(create_app(settings=settings))


def _rate_limited_client(
    settings: KronikaSettings,
    login: str,
    role: str,
    *,
    max_submits_per_hour: int,
    clock: list[int],
) -> TestClient:
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteAnalysisProposalRepository(engine)
    dependencies = AnalysisProposalApiDependencies(
        propose_analysis=ProposeAnalysis(
            repository,
            now_ms=lambda: clock[0],
            max_submits_per_hour=max_submits_per_hour,
        ),
        list_analysis_proposals=ListAnalysisProposals(repository),
        catalog_available=lambda: True,
    )
    app = create_app(
        settings=settings,
        analysis_proposal_api_dependencies=dependencies,
    )

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(login, role)
        request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-event"
        return await call_next(request)

    return TestClient(app)


def _prepare(tmp_path: Path) -> KronikaSettings:
    database_path = tmp_path / "database" / "catalog.sqlite3"
    database_path.parent.mkdir(parents=True)
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            for media_id, title, created, published in (
                (MEDIA_A, "Alice Clip", 30, False),
                (MEDIA_B, "Bob Clip", 20, True),
                (MEDIA_C, None, 10, False),
            ):
                connection.execute(
                    text(
                        "INSERT INTO logical_media "
                        "(id, media_kind, created_at_ms, updated_at_ms) "
                        "VALUES (:id, 'video', :created, :created)"
                    ),
                    {"id": media_id, "created": created},
                )
                connection.execute(
                    text(
                        "INSERT INTO media_metadata "
                        "(media_id, display_title, description, collection_key, "
                        "processed_at_ms, created_at_ms, updated_at_ms, "
                        "content_category, acquisition_source) "
                        "VALUES (:media_id, :title, :description, NULL, NULL, "
                        ":created, :created, 'general', 'unknown')"
                    ),
                    {
                        "media_id": media_id,
                        "title": title,
                        "description": None if title is None else "Proposal description",
                        "created": created,
                    },
                )
                if published:
                    connection.execute(
                        text(
                            "INSERT INTO media_content_publications "
                            "(media_id, published_at_ms, publication_origin) "
                            "VALUES (:id, :created, 'admin_explicit')"
                        ),
                        {"id": media_id, "created": created},
                    )
    finally:
        dispose_engine(engine)
    return settings


def _propose_headers() -> dict[str, str]:
    return {"X-FrameNest-Request": "1", "Accept": "application/json"}


def test_propose_creates_durable_row_visible_to_admin_after_fresh_engine(
    tmp_path: Path,
) -> None:
    settings = _prepare(tmp_path)
    created = _client(settings, ALICE, ROLE_USER).post(
        PROPOSE_PATH, headers=_propose_headers()
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["media_id"] == MEDIA_A
    assert payload["status"] == "open"
    assert payload["proposal_id"]
    first_id = payload["proposal_id"]
    dispose_engine(create_sqlite_engine(settings.database_path))
    listed = _client(settings, ADMIN, ROLE_ADMIN).get(LIST_PATH)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 1
    assert page["items"][0]["proposal_id"] == first_id
    assert page["items"][0]["proposer_login"] == ALICE
    assert page["items"][0]["display_title"] == "Alice Clip"
    assert page["items"][0]["content_publication_state"] == "unpublished"
    assert page["items"][0]["status"] == "open"
    assert page["items"][0]["publication_ready"] is False
    assert "tags" in page["items"][0]["missing_fields"]


def test_duplicate_proposals_each_create_a_row(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    client = _client(settings, ALICE, ROLE_USER)
    first = client.post(PROPOSE_PATH, headers=_propose_headers())
    second = client.post(PROPOSE_PATH, headers=_propose_headers())
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["proposal_id"] != second.json()["proposal_id"]
    listed = _client(settings, ADMIN, ROLE_ADMIN).get(LIST_PATH)
    assert listed.json()["total"] == 2


def test_unknown_and_malformed_media_are_sanitized_not_found(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    client = _client(settings, ALICE, ROLE_USER)
    unknown = client.post(
        f"/api/workspace/media/{UNKNOWN}/analysis-proposals",
        headers=_propose_headers(),
    )
    malformed = client.post(
        f"/api/workspace/media/{INVALID_ID}/analysis-proposals",
        headers=_propose_headers(),
    )
    assert unknown.status_code == 404
    assert malformed.status_code == 404
    assert unknown.json() == malformed.json()
    assert unknown.json()["error"]["code"] == "MEDIA_NOT_FOUND"
    assert "Traceback" not in unknown.text


def test_capability_denials_and_anonymous(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    denied = _client(
        settings,
        ALICE,
        ROLE_USER,
        capabilities=CAPABILITIES_BY_ROLE[ROLE_USER] - {CAPABILITY_ANALYSIS_PROPOSE},
    ).post(PROPOSE_PATH, headers=_propose_headers())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CAPABILITY_DENIED"
    anonymous = _plain_client(settings).post(PROPOSE_PATH, headers=_propose_headers())
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "IDENTITY_REQUIRED"


def test_ordinary_users_cannot_list_proposals(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    _client(settings, ALICE, ROLE_USER).post(PROPOSE_PATH, headers=_propose_headers())
    listed = _client(settings, ALICE, ROLE_USER).get(LIST_PATH)
    assert listed.status_code == 403
    assert listed.json()["error"]["code"] == "CAPABILITY_DENIED"
    admin_without_workflow = _client(
        settings,
        ADMIN,
        ROLE_ADMIN,
        capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN] - {CAPABILITY_MEDIA_WORKFLOW_READ},
    ).get(LIST_PATH)
    assert admin_without_workflow.status_code == 403


def test_admin_list_pagination_is_newest_first(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_analysis_proposals "
                    "(id, media_id, proposed_by_login_key, created_at_ms, status) "
                    "VALUES "
                    "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', :a, :alice, 10, 'open'), "
                    "('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', :b, :bob, 30, 'open'), "
                    "('cccccccc-cccc-4ccc-8ccc-cccccccccccc', :c, :alice, 20, 'open'), "
                    "('dddddddd-dddd-4ddd-8ddd-dddddddddddd', :a, :alice, 40, 'dismissed')"
                ),
                {"a": MEDIA_A, "b": MEDIA_B, "c": MEDIA_C, "alice": ALICE, "bob": BOB},
            )
    finally:
        dispose_engine(engine)
    page = _client(settings, ADMIN, ROLE_ADMIN).get(f"{LIST_PATH}?limit=2&offset=0")
    assert page.status_code == 200
    payload = page.json()
    assert payload["total"] == 3
    assert payload["has_next"] is True
    assert payload["has_previous"] is False
    assert [item["proposal_id"] for item in payload["items"]] == [
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ]
    assert payload["items"][0]["proposer_login"] == BOB
    assert payload["items"][0]["content_publication_state"] == "published"
    next_page = _client(settings, ADMIN, ROLE_ADMIN).get(f"{LIST_PATH}?limit=2&offset=2")
    assert next_page.json()["items"][0]["proposal_id"] == (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )
    assert next_page.json()["has_next"] is False
    assert next_page.json()["has_previous"] is True


def test_route_policies_require_propose_and_workflow_capabilities() -> None:
    propose, propose_match = find_route_policy(
        "POST", f"/api/workspace/media/{MEDIA_A}/analysis-proposals"
    )
    listed, list_match = find_route_policy("GET", LIST_PATH)
    assert propose_match is not None
    assert list_match is not None
    assert propose.capability == CAPABILITY_ANALYSIS_PROPOSE
    assert propose.audit_action == "analysis.propose"
    assert listed.capability == CAPABILITY_MEDIA_WORKFLOW_READ
    assert listed.audit_action == "analysis.proposals.list"


def test_proposal_modules_do_not_import_provider_or_enqueue_paths() -> None:
    relative_paths = (
        "src/kronika/application/analysis_proposal.py",
        "src/kronika/application/ports/analysis_proposal.py",
        "src/kronika/infrastructure/persistence/analysis_proposal_repository.py",
        "src/kronika/adapters/api/analysis_proposal_api.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/"
        "0033_media_analysis_proposals.py",
    )
    forbidden_import_tokens = (
        "nvidia",
        "nim",
        "media_analysis_coordinator",
        "media_analysis_lifecycle",
        "resolve_ai_provider",
        "ai_provider",
        "LocalMediaAnalysisAdapter",
        "RequestManualMediaAnalysis",
        "ScheduleAutomaticMediaAnalysis",
        "infrastructure.ai",
        "infrastructure.media_analysis",
    )
    import_text = []
    for relative in relative_paths:
        for line in (REPOSITORY_ROOT / relative).read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                import_text.append(stripped)
    combined = "\n".join(import_text)
    for token in forbidden_import_tokens:
        assert token not in combined
    source = inspect.getsource(
        __import__(
            "kronika.infrastructure.persistence.analysis_proposal_repository",
            fromlist=["SqliteAnalysisProposalRepository"],
        )
    )
    assert "media_analysis_proposals" in source
    assert "media_analysis_runs" not in source
    assert "FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS" not in source


def test_propose_does_not_create_analysis_runs(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    _client(settings, ALICE, ROLE_USER).post(PROPOSE_PATH, headers=_propose_headers())
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            run_count = connection.execute(
                text("SELECT COUNT(*) FROM media_analysis_runs")
            ).scalar_one()
            proposal_count = connection.execute(
                text("SELECT COUNT(*) FROM media_analysis_proposals")
            ).scalar_one()
    finally:
        dispose_engine(engine)
    assert run_count == 0
    assert proposal_count == 1


def test_trusted_ingress_records_propose_audit_event(tmp_path: Path) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin="https://nuc-1.example.ts.net",
        identity_map={ADMIN: "admin", ALICE: "user"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 1, 1)"
                ),
                {"id": MEDIA_A},
            )
            connection.execute(
                text(
                    "INSERT INTO media_metadata "
                    "(media_id, display_title, description, collection_key, "
                    "processed_at_ms, created_at_ms, updated_at_ms, "
                    "content_category, acquisition_source) "
                    "VALUES (:id, 'Alice Clip', 'Proposal description', NULL, "
                    "NULL, 1, 1, 'general', 'unknown')"
                ),
                {"id": MEDIA_A},
            )
    finally:
        dispose_engine(engine)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            PROPOSE_PATH,
            headers={
                "Tailscale-User-Login": ALICE,
                "Tailscale-User-Name": "Alice",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "nuc-1.example.ts.net",
                "Origin": "https://nuc-1.example.ts.net",
                "X-FrameNest-Request": "1",
                "Accept": "application/json",
            },
        )
    assert response.status_code == 201
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT actor_login, capability, action, target_type, target_id, "
            "outcome, http_status FROM security_audit_events"
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["actor_login"] == ALICE
    assert row["capability"] == "analysis.propose"
    assert row["action"] == "analysis.propose"
    assert row["target_type"] == "media"
    assert row["target_id"] == MEDIA_A
    assert row["outcome"] == "allowed"
    assert row["http_status"] == 201


def test_per_user_submit_rate_limit_returns_sanitized_429(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    client = _rate_limited_client(
        settings, ALICE, ROLE_USER, max_submits_per_hour=2, clock=[1_000_000]
    )
    first = client.post(PROPOSE_PATH, headers=_propose_headers())
    second = client.post(PROPOSE_PATH, headers=_propose_headers())
    assert first.status_code == 201
    assert second.status_code == 201
    limited = client.post(PROPOSE_PATH, headers=_propose_headers())
    assert limited.status_code == 429
    payload = limited.json()
    assert payload == {
        "error": {
            "code": "ANALYSIS_PROPOSAL_RATE_LIMIT",
            "message": "Too many analysis proposals this hour.",
        }
    }
    assert limited.headers.get("cache-control") == "no-store"


def test_rate_limit_window_resets_after_one_hour(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    clock = [2_000_000]
    client = _rate_limited_client(
        settings, ALICE, ROLE_USER, max_submits_per_hour=1, clock=clock
    )
    assert client.post(PROPOSE_PATH, headers=_propose_headers()).status_code == 201
    blocked = client.post(PROPOSE_PATH, headers=_propose_headers())
    assert blocked.status_code == 429
    clock[0] += MS_PER_HOUR + 1
    reset = client.post(PROPOSE_PATH, headers=_propose_headers())
    assert reset.status_code == 201


def test_rate_limit_is_isolated_per_user(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    alice = _rate_limited_client(
        settings, ALICE, ROLE_USER, max_submits_per_hour=1, clock=[3_000_000]
    )
    bob = _rate_limited_client(
        settings, BOB, ROLE_USER, max_submits_per_hour=1, clock=[3_000_000]
    )
    alice_first = alice.post(PROPOSE_PATH, headers=_propose_headers())
    assert alice_first.status_code == 201
    alice_blocked = alice.post(PROPOSE_PATH, headers=_propose_headers())
    assert alice_blocked.status_code == 429
    bob_response = bob.post(PROPOSE_PATH, headers=_propose_headers())
    assert bob_response.status_code == 201
    assert bob_response.json()["proposal_id"] != alice_first.json()["proposal_id"]


def test_admin_list_unaffected_while_user_rate_limited(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    alice = _rate_limited_client(
        settings, ALICE, ROLE_USER, max_submits_per_hour=1, clock=[4_000_000]
    )
    assert alice.post(PROPOSE_PATH, headers=_propose_headers()).status_code == 201
    assert alice.post(PROPOSE_PATH, headers=_propose_headers()).status_code == 429
    listed = _client(settings, ADMIN, ROLE_ADMIN).get(LIST_PATH)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 1
    assert page["items"][0]["proposer_login"] == ALICE
