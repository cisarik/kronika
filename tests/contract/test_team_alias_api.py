"""Audited administrator team-alias reads without overlay writes."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    find_route_policy,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_METADATA_ALIAS_TEAM_READ,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

ALICE = "alice@example.com"
BOB = "bob@example.com"
ADMIN = "admin@example.com"
MEDIA_A = "11111111-1111-4111-8111-111111111111"
MEDIA_B = "22222222-2222-4222-8222-222222222222"
UNKNOWN = "88888888-8888-4888-8888-888888888888"
ADMIN_ALIASES_PATH = f"/api/admin/media/{MEDIA_A}/aliases"
OWN_ALIAS_PATH = f"/api/media/{MEDIA_A}/alias"
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
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:a, 'video', 1, 1), (:b, 'video', 2, 2)"
                ),
                {"a": MEDIA_A, "b": MEDIA_B},
            )
            connection.execute(
                text(
                    "INSERT INTO media_metadata "
                    "(media_id, display_title, description, collection_key, "
                    "processed_at_ms, created_at_ms, updated_at_ms, "
                    "content_category, acquisition_source) "
                    "VALUES (:a, 'Alice Clip', 'Canonical', NULL, NULL, 1, 1, "
                    "'general', 'unknown')"
                ),
                {"a": MEDIA_A},
            )
            connection.execute(
                text(
                    "INSERT INTO media_content_publications "
                    "(media_id, published_at_ms, publication_origin) "
                    "VALUES (:a, 1, 'admin_explicit')"
                ),
                {"a": MEDIA_A},
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_tags "
                    "(key, display_name, created_at_ms, updated_at_ms) "
                    "VALUES ('meme', 'Meme', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO media_user_aliases "
                    "(media_id, login_key, display_title, description, "
                    "created_at_ms, updated_at_ms) VALUES "
                    "(:a, :alice, 'Alice overlay', 'Alice note', 10, 11), "
                    "(:a, :bob, 'Bob overlay', NULL, 20, 21)"
                ),
                {"a": MEDIA_A, "alice": ALICE, "bob": BOB},
            )
            connection.execute(
                text(
                    "INSERT INTO media_user_alias_tags "
                    "(media_id, login_key, tag_key, position) "
                    "VALUES (:a, :alice, 'meme', 0)"
                ),
                {"a": MEDIA_A, "alice": ALICE},
            )
    finally:
        dispose_engine(engine)
    return settings


def _alias_counts(database_path: Path) -> tuple[int, int]:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            aliases = connection.execute(
                text("SELECT COUNT(*) FROM media_user_aliases")
            ).scalar_one()
            tags = connection.execute(
                text("SELECT COUNT(*) FROM media_user_alias_tags")
            ).scalar_one()
    finally:
        dispose_engine(engine)
    return int(aliases), int(tags)


def test_administrator_reads_aggregated_team_aliases_without_writes(
    tmp_path: Path,
) -> None:
    settings = _prepare(tmp_path)
    before = _alias_counts(settings.database_path)
    response = _client(settings, ADMIN, ROLE_ADMIN).get(ADMIN_ALIASES_PATH)
    after = _alias_counts(settings.database_path)
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")
    payload = response.json()
    assert payload["media_id"] == MEDIA_A
    assert [item["login_key"] for item in payload["items"]] == [ALICE, BOB]
    assert payload["items"][0]["display_title"] == "Alice overlay"
    assert payload["items"][0]["tag_keys"] == ["meme"]
    assert payload["items"][0]["created_at_ms"] == 10
    assert payload["items"][0]["updated_at_ms"] == 11
    assert payload["items"][1]["display_title"] == "Bob overlay"
    assert payload["items"][1]["tag_keys"] == []
    assert before == after == (2, 1)
    empty = _client(settings, ADMIN, ROLE_ADMIN).get(
        f"/api/admin/media/{MEDIA_B}/aliases"
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_dual_capability_denials_include_admin_missing_team_read(
    tmp_path: Path,
) -> None:
    settings = _prepare(tmp_path)
    missing_team = _client(
        settings,
        ADMIN,
        ROLE_ADMIN,
        capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN]
        - {CAPABILITY_METADATA_ALIAS_TEAM_READ},
    ).get(ADMIN_ALIASES_PATH)
    missing_workflow = _client(
        settings,
        ADMIN,
        ROLE_ADMIN,
        capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN]
        - {CAPABILITY_MEDIA_WORKFLOW_READ},
    ).get(ADMIN_ALIASES_PATH)
    ordinary = _client(settings, ALICE, ROLE_USER).get(ADMIN_ALIASES_PATH)
    anonymous = _plain_client(settings).get(ADMIN_ALIASES_PATH)
    assert missing_team.status_code == 403
    assert missing_workflow.status_code == 403
    assert ordinary.status_code == 403
    assert anonymous.status_code == 401
    assert missing_team.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert CAPABILITY_MEDIA_WORKFLOW_READ in (
        CAPABILITIES_BY_ROLE[ROLE_ADMIN] - {CAPABILITY_METADATA_ALIAS_TEAM_READ}
    )
    assert CAPABILITY_METADATA_ALIAS_TEAM_READ in (
        CAPABILITIES_BY_ROLE[ROLE_ADMIN] - {CAPABILITY_MEDIA_WORKFLOW_READ}
    )


def test_ordinary_user_sees_only_own_alias_and_cannot_call_admin_route(
    tmp_path: Path,
) -> None:
    settings = _prepare(tmp_path)
    own = _client(settings, ALICE, ROLE_USER).get(OWN_ALIAS_PATH)
    assert own.status_code == 200
    payload = own.json()
    assert payload == {
        "display_title": "Alice overlay",
        "description": "Alice note",
        "tag_keys": ["meme"],
    }
    assert "login_key" not in payload
    assert "Bob overlay" not in own.text
    bob = _client(settings, BOB, ROLE_USER).get(OWN_ALIAS_PATH)
    assert bob.json()["display_title"] == "Bob overlay"
    assert "Alice overlay" not in bob.text
    denied = _client(settings, ALICE, ROLE_USER).get(ADMIN_ALIASES_PATH)
    assert denied.status_code == 403


def test_gallery_and_workspace_payloads_follow_alias_display_contract(
    tmp_path: Path,
) -> None:
    settings = _prepare(tmp_path)
    alice_gallery = _client(settings, ALICE, ROLE_USER).get("/api/media")
    alice_detail = _client(settings, ALICE, ROLE_USER).get(f"/api/media/{MEDIA_A}")
    alice_workspace = _client(settings, ALICE, ROLE_USER).get("/api/workspace/media")
    bob_gallery = _client(settings, BOB, ROLE_USER).get("/api/media")
    bob_detail = _client(settings, BOB, ROLE_USER).get(f"/api/media/{MEDIA_A}")
    bob_workspace = _client(settings, BOB, ROLE_USER).get("/api/workspace/media")
    anonymous_gallery = _plain_client(settings).get("/api/media")
    anonymous_detail = _plain_client(settings).get(f"/api/media/{MEDIA_A}")
    assert alice_gallery.status_code == 200
    assert alice_detail.status_code == 200
    assert alice_workspace.status_code == 200
    assert bob_gallery.status_code == 200
    assert bob_detail.status_code == 200
    assert bob_workspace.status_code == 200
    assert anonymous_gallery.status_code == 200
    assert anonymous_gallery.json()["items"] == []
    assert anonymous_detail.status_code == 404
    for payload in (
        alice_gallery.json(),
        alice_detail.json(),
        alice_workspace.json(),
        bob_gallery.json(),
        bob_detail.json(),
        bob_workspace.json(),
    ):
        assert "alias" not in payload if isinstance(payload, dict) else True
        if isinstance(payload, dict) and "items" in payload:
            for item in payload["items"]:
                assert "alias" not in item
                assert "aliases" not in item
    for blob in (str(alice_gallery.json()), str(alice_detail.json())):
        assert "Alice overlay" in blob
        assert "Alice note" in blob
        assert "meme" in blob
        assert "Alice Clip" not in blob
        assert "Bob overlay" not in blob
    for blob in (str(bob_gallery.json()), str(bob_detail.json())):
        assert "Alice overlay" not in blob
        assert "Alice note" not in blob
        assert "meme" not in blob
        assert "Canonical" in blob
    anonymous_blob = str(anonymous_gallery.json()) + str(anonymous_detail.json())
    assert "Alice overlay" not in anonymous_blob
    assert "Alice note" not in anonymous_blob
    assert "Bob overlay" not in anonymous_blob
    bob_workspace_blob = str(bob_workspace.json())
    assert "Alice overlay" not in bob_workspace_blob
    assert "Alice note" not in bob_workspace_blob
    assert "meme" not in bob_workspace_blob


def test_unknown_media_is_sanitized_not_found(tmp_path: Path) -> None:
    settings = _prepare(tmp_path)
    unknown = _client(settings, ADMIN, ROLE_ADMIN).get(
        f"/api/admin/media/{UNKNOWN}/aliases"
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "MEDIA_NOT_FOUND"
    assert "Traceback" not in unknown.text


def test_route_policy_requires_both_capabilities_and_distinct_audit_action() -> None:
    policy, match = find_route_policy("GET", ADMIN_ALIASES_PATH)
    assert match is not None
    assert policy.capability == CAPABILITY_MEDIA_WORKFLOW_READ
    assert policy.additional_capabilities == (CAPABILITY_METADATA_ALIAS_TEAM_READ,)
    assert policy.audit_action == "metadata.alias.team.list"
    assert policy.audit_target_type == "media"
    own_get, own_match = find_route_policy("GET", OWN_ALIAS_PATH)
    assert own_match is not None
    assert own_get.capability != CAPABILITY_METADATA_ALIAS_TEAM_READ
    assert own_get.audit_action is None


def test_admin_alias_read_path_performs_no_alias_table_writes() -> None:
    relative_paths = (
        "src/kronika/adapters/api/team_alias_api.py",
        "src/kronika/application/media_user_alias.py",
    )
    forbidden = ("insert(", "update(", "delete(")
    for relative in relative_paths:
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        lowered = source.lower()
        if relative.endswith("media_user_alias.py"):
            listed = inspect.getsource(
                __import__(
                    "kronika.application.media_user_alias",
                    fromlist=["ListTeamMediaAliases"],
                ).ListTeamMediaAliases
            ).lower()
            for token in forbidden:
                assert token not in listed
            continue
        for token in forbidden:
            assert token not in lowered
    listed_repo = inspect.getsource(
        __import__(
            "kronika.infrastructure.persistence.media_user_alias_repository",
            fromlist=["SqliteMediaUserAliasRepository"],
        ).SqliteMediaUserAliasRepository.list_aliases_for_media
    ).lower()
    helper = inspect.getsource(
        __import__(
            "kronika.infrastructure.persistence.media_user_alias_repository",
            fromlist=["_list_aliases_for_media"],
        )._list_aliases_for_media
    ).lower()
    for token in forbidden:
        assert token not in listed_repo
        assert token not in helper
    assert "select(" in helper


def test_trusted_ingress_records_team_alias_list_audit_event(tmp_path: Path) -> None:
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
    finally:
        dispose_engine(engine)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get(
            ADMIN_ALIASES_PATH,
            headers={
                "Tailscale-User-Login": ADMIN,
                "Tailscale-User-Name": "Admin",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "nuc-1.example.ts.net",
            },
        )
        denied = client.get(
            ADMIN_ALIASES_PATH,
            headers={
                "Tailscale-User-Login": ALICE,
                "Tailscale-User-Name": "Alice",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "nuc-1.example.ts.net",
            },
        )
    assert response.status_code == 200
    assert denied.status_code == 403
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        allowed_rows = connection.execute(
            "SELECT actor_login, capability, action, target_type, target_id, "
            "outcome, http_status FROM security_audit_events "
            "WHERE action = 'metadata.alias.team.list' AND outcome = 'allowed'"
        ).fetchall()
    finally:
        connection.close()
    assert len(allowed_rows) == 1
    row = allowed_rows[0]
    assert row["actor_login"] == ADMIN
    assert row["capability"] == "media.workflow.read"
    assert row["action"] == "metadata.alias.team.list"
    assert row["target_type"] == "media"
    assert row["target_id"] == MEDIA_A
    assert row["http_status"] == 200


def test_schema_head_sentences_are_0035() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    product = (REPOSITORY_ROOT / "PRODUCT.md").read_text(encoding="utf-8")
    roadmap = (REPOSITORY_ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    spec = (REPOSITORY_ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert "schema head `0035`" in readme
    assert "schema head `0032`" not in readme
    assert "schema head `0035`" in product
    assert "schema head `0032`" not in product
    assert "The current schema head is revision `0035`" in roadmap
    assert "The current schema head is revision `0032`" not in roadmap
    assert "schema head `0035`" in spec
    assert "implemented-for-backend" in spec
    assert "`GET /api/admin/media/{media_id}/aliases`" in spec
    assert "`metadata.alias.team.read`" in spec
    assert "remains a successor workspace capability" not in spec
