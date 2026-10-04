"""Contract tests for the Tailscale trusted-ingress security boundary.

These tests bind the production ``tailscale_uds`` ingress mode to its
invariants: Serve-injected identity is the only remote identity source,
capabilities are enforced server-side, browser mutations require the exact
external Origin plus the FrameNest mutation header, privileged actions are
audited, and the local channel stays narrowly operational.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import (
    ROUTE_POLICIES,
    _UNCLASSIFIED_FALLBACK_POLICY,
    find_route_policy,
)
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_MEDIA_CONTENT_PUBLISH,
    CAPABILITY_METADATA_CANONICAL_WRITE,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_LOGIN = "user@example.com"
STRANGER_LOGIN = "stranger@example.com"

ADMIN_CAPABILITY_SAMPLE = "metadata.canonical.write"
USER_CAPABILITIES = {
    "gallery.read",
    "media.original.read",
    "media.download",
    "media.workspace.read",
    "analysis.propose",
    "upload.submit",
    "youtube.request",
    "x.request",
    "metadata.alias.write",
    "research.run",
}


def _serve_headers(
    login: str = ADMIN_LOGIN,
    name: str = "Admin User",
) -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": name,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


def _mutation_headers(login: str = ADMIN_LOGIN) -> dict[str, str]:
    return {
        **_serve_headers(login),
        "Origin": EXTERNAL_ORIGIN,
        "X-FrameNest-Request": "1",
    }


@pytest.fixture
def tailscale_client(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={ADMIN_LOGIN: "admin", USER_LOGIN: "user"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client, settings


@pytest.fixture
def unclassified_client(tmp_path: Path):
    """App with one dynamically mounted route that has no explicit policy."""
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={ADMIN_LOGIN: "admin", USER_LOGIN: "user"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    invoked: list[str] = []

    @app.get("/api/internal-unclassified")
    def unclassified_read() -> dict[str, bool]:
        invoked.append("GET")
        return {"ok": True}

    @app.post("/api/internal-unclassified")
    def unclassified_mutation() -> dict[str, bool]:
        invoked.append("POST")
        return {"ok": True}

    with TestClient(app) as client:
        yield client, invoked


def _audit_rows(database_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT request_id, actor_login, actor_key, identity_provenance, role,"
            " capability, action, target_type, target_id, outcome, http_status"
            " FROM security_audit_events ORDER BY occurred_at_ms, id"
        ).fetchall()
    finally:
        connection.close()


def _seed_publication_target(
    database_path: Path,
    *,
    ready: bool,
) -> str:
    media_id = str(uuid.uuid4())
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (?, 'video', 1, 1)",
            (media_id,),
        )
        if ready:
            connection.execute(
                "INSERT OR IGNORE INTO canonical_tags "
                "(key, display_name, created_at_ms, updated_at_ms) "
                "VALUES ('publish-ready', 'Publish ready', 1, 1)"
            )
            connection.execute(
                "INSERT INTO media_metadata "
                "(media_id, display_title, description, created_at_ms, updated_at_ms) "
                "VALUES (?, 'Ready title', 'Ready description', 1, 1)",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO media_canonical_tags "
                "(media_id, tag_key, position) VALUES (?, 'publish-ready', 0)",
                (media_id,),
            )
        connection.commit()
    finally:
        connection.close()
    return media_id


def _error_code(response) -> str:
    payload = response.json()
    return payload["error"]["code"]


# --- Trusted ingress and identity ---------------------------------------


def test_valid_serve_identity_parses_admin_identity(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get("/api/identity/me", headers=_serve_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["login"] == ADMIN_LOGIN
    assert payload["display_name"] == "Admin User"
    assert payload["role"] == "admin"
    assert payload["provenance"] == "tailscale-serve"
    assert ADMIN_CAPABILITY_SAMPLE in payload["capabilities"]
    assert payload["capabilities"] == sorted(payload["capabilities"])


def test_missing_identity_login_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    del headers["Tailscale-User-Login"]
    response = client.get("/api/media", headers=headers)
    assert response.status_code == 401
    assert _error_code(response) == "IDENTITY_REQUIRED"


def test_tagged_device_without_user_login_fails_closed(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = {
        "Tailscale-User-Name": "Tagged Device",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }
    response = client.get("/api/media", headers=headers)
    assert response.status_code == 401
    assert _error_code(response) == "IDENTITY_REQUIRED"


def test_malformed_identity_login_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get(
        "/api/media", headers=_serve_headers(login="bad login@example.com")
    )
    assert response.status_code == 401
    assert _error_code(response) == "IDENTITY_REQUIRED"


def test_duplicate_identity_header_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get(
        "/api/media",
        headers=[
            ("Tailscale-User-Login", ADMIN_LOGIN),
            ("Tailscale-User-Login", USER_LOGIN),
            ("X-Forwarded-Proto", "https"),
            ("X-Forwarded-Host", EXTERNAL_HOST),
        ],
    )
    assert response.status_code == 400
    assert _error_code(response) == "INGRESS_HEADERS_CONFLICT"


def test_conflicting_forwarded_proto_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-Forwarded-Proto"] = "http"
    response = client.get("/api/media", headers=headers)
    assert response.status_code == 403
    assert _error_code(response) == "INGRESS_HEADERS_FORBIDDEN"


def test_conflicting_forwarded_host_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-Forwarded-Host"] = "other-host.example.ts.net"
    response = client.get("/api/media", headers=headers)
    assert response.status_code == 403
    assert _error_code(response) == "INGRESS_HEADERS_FORBIDDEN"


def test_unmarked_local_channel_keeps_only_narrow_health(tailscale_client) -> None:
    client, _ = tailscale_client
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    for path in ("/", "/api/media", "/api/identity/me", "/api/status/cloud"):
        response = client.get(path)
        assert response.status_code == 401
        assert _error_code(response) == "IDENTITY_REQUIRED"


def test_tcp_mode_has_no_identity_api_and_ignores_spoofed_headers(
    tmp_path: Path,
) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        identity = client.get("/api/identity/me", headers=_serve_headers())
        assert identity.status_code == 404
        cloud = client.get("/api/status/cloud", headers=_serve_headers())
        assert cloud.status_code == 200
        assert cloud.json()["connection"] == "loopback"
        media = client.get("/api/media", headers=_serve_headers(STRANGER_LOGIN))
        assert media.status_code == 200


def test_display_name_change_never_alters_privilege(tailscale_client) -> None:
    client, _ = tailscale_client
    first = client.get(
        "/api/identity/me", headers=_serve_headers(USER_LOGIN, "First Name")
    )
    second = client.get(
        "/api/identity/me", headers=_serve_headers(USER_LOGIN, "Renamed")
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["role"] == second.json()["role"] == "user"
    assert first.json()["capabilities"] == second.json()["capabilities"]
    assert second.json()["display_name"] == "Renamed"
    denied = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(USER_LOGIN),
        json={"key": "alpha", "display_name": "Alpha"},
    )
    assert denied.status_code == 403


def test_login_normalization_maps_case_and_whitespace_variants(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    response = client.get(
        "/api/identity/me", headers=_serve_headers(login=" Admin@Example.COM ")
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "admin"
    assert payload["login"] == "Admin@Example.COM"


def test_unknown_identity_is_denied_closed(tailscale_client) -> None:
    client, settings = tailscale_client
    response = client.get("/api/media", headers=_serve_headers(STRANGER_LOGIN))
    assert response.status_code == 403
    assert _error_code(response) == "IDENTITY_NOT_AUTHORIZED"
    identity = client.get("/api/identity/me", headers=_serve_headers(STRANGER_LOGIN))
    assert identity.status_code == 403
    assert _audit_rows(settings.database_path) == []


def test_remote_health_stays_sanitized(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get("/health", headers=_serve_headers(USER_LOGIN))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    unmapped = client.get("/health", headers=_serve_headers(STRANGER_LOGIN))
    assert unmapped.status_code == 403


# --- Mapping and authorization ------------------------------------------


def test_ordinary_user_identity_payload(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get("/api/identity/me", headers=_serve_headers(USER_LOGIN))
    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "user"
    assert set(payload["capabilities"]) == USER_CAPABILITIES


def test_ordinary_user_reads_succeed(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers(USER_LOGIN)
    assert client.get("/", headers=headers).status_code == 200
    assert client.get("/api/media", headers=headers).status_code == 200
    assert client.get("/api/libraries", headers=headers).status_code == 200
    assert client.get("/api/canonical-tags", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/canonical-tags", {"key": "alpha", "display_name": "Alpha"}),
        ("PUT", f"/api/media/{uuid.uuid4()}/metadata", {}),
        ("POST", f"/api/libraries/{uuid.uuid4()}/scan-preview", None),
        ("POST", f"/api/libraries/{uuid.uuid4()}/media-imports", {}),
        ("POST", f"/api/libraries/{uuid.uuid4()}/media-analysis-preview", {}),
        ("POST", f"/api/libraries/{uuid.uuid4()}/media-suggestion-preview", {}),
        (
            "POST",
            f"/api/media/{uuid.uuid4()}/locations/{uuid.uuid4()}/ai-suggestion-preview",
            {},
        ),
        ("GET", "/api/ai/media-suggestion-capability", None),
        ("GET", "/api/ai/automatic-analysis-capability", None),
        ("GET", "/api/admin/media", None),
        (
            "PUT",
            f"/api/admin/media/{uuid.uuid4()}/content-publication",
            None,
        ),
    ],
)
def test_ordinary_user_direct_privileged_calls_fail(
    tailscale_client, method: str, path: str, body: dict | None
) -> None:
    client, _ = tailscale_client
    headers = _mutation_headers(USER_LOGIN)
    if method == "GET":
        response = client.get(path, headers=_serve_headers(USER_LOGIN))
    elif method == "PUT":
        response = client.put(path, headers=headers, json=body)
    else:
        response = client.post(path, headers=headers, json=body)
    assert response.status_code == 403
    assert _error_code(response) == "CAPABILITY_DENIED"


def test_ordinary_user_upload_submit_routes_are_capability_allowed(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    upload_id = str(uuid.uuid4())
    capability = client.get(
        "/api/uploads/capability", headers=_serve_headers(USER_LOGIN)
    )
    assert capability.status_code == 200
    assert capability.json()["uploads_enabled"] is False
    create = client.post(
        "/api/uploads",
        headers=_mutation_headers(USER_LOGIN),
        json={"display_filename": "clip.mp4", "declared_size_bytes": 8},
    )
    assert create.status_code == 503
    assert _error_code(create) != "CAPABILITY_DENIED"
    status = client.get(
        f"/api/uploads/{upload_id}", headers=_serve_headers(USER_LOGIN)
    )
    assert status.status_code in {404, 503}
    assert _error_code(status) != "CAPABILITY_DENIED"


def test_seven_upload_routes_require_upload_submit() -> None:
    upload_id = str(uuid.uuid4())
    cases = (
        ("POST", "/api/uploads"),
        ("GET", "/api/uploads/capability"),
        ("GET", f"/api/uploads/{upload_id}"),
        ("PATCH", f"/api/uploads/{upload_id}"),
        ("POST", f"/api/uploads/{upload_id}/complete"),
        ("POST", f"/api/uploads/{upload_id}/duplicate-resolution"),
        ("DELETE", f"/api/uploads/{upload_id}"),
    )
    for method, path in cases:
        policy, match = find_route_policy(method, path)
        assert match is not None
        assert policy.capability == "upload.submit"
    admin_policy, admin_match = find_route_policy("GET", "/api/admin/media")
    assert admin_match is not None
    assert admin_policy.capability == "media.workflow.read"
    assert admin_policy.capability != "upload.submit"


def test_admin_privileged_operation_succeeds(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(),
        json={"key": "alpha", "display_name": "Alpha"},
    )
    assert response.status_code == 201


def test_operator_routes_are_not_remote_routes(tailscale_client) -> None:
    client, _ = tailscale_client
    claim_id = str(uuid.uuid4())
    for method, path in (
        ("GET", f"/api/operator/youtube/claims/{claim_id}"),
        ("POST", "/api/operator/youtube/claims"),
        ("POST", f"/api/operator/youtube/claims/{claim_id}/retry"),
    ):
        if method == "GET":
            response = client.get(path, headers=_serve_headers())
        else:
            response = client.post(path, headers=_mutation_headers(), json={})
        assert response.status_code == 404
        assert _error_code(response) == "NOT_FOUND"


def test_browser_youtube_routes_use_authenticated_policy_and_remain_separate(
    tailscale_client,
) -> None:
    client, settings = tailscale_client
    claim_id = str(uuid.uuid4())
    admin_status = client.get(
        f"/api/admin/youtube/claims/{claim_id}",
        headers=_serve_headers(),
    )
    ordinary_status = client.get(
        f"/api/admin/youtube/claims/{claim_id}",
        headers=_serve_headers(USER_LOGIN),
    )
    missing_origin = client.post(
        "/api/admin/youtube/claims",
        headers=_serve_headers(),
        json={},
    )
    missing_mutation_header = client.post(
        "/api/admin/youtube/claims",
        headers={**_serve_headers(), "Origin": EXTERNAL_ORIGIN},
        json={},
    )
    audited_but_disabled = client.post(
        "/api/admin/youtube/claims",
        headers=_mutation_headers(),
        json={
            "url": "https://youtu.be/AbCdEf123_-",
            "confirmation_method": "interactive",
        },
    )
    configured_policy = find_route_policy(
        "POST", "/api/admin/youtube/claims"
    )
    lookalike_policy = find_route_policy(
        "GET", "/api/admin/youtube/claims-extra"
    )

    assert admin_status.status_code == 503
    assert _error_code(admin_status) == "YOUTUBE_BROWSER_NOT_CONFIGURED"
    assert ordinary_status.status_code == 403
    assert _error_code(ordinary_status) == "CAPABILITY_DENIED"
    assert missing_origin.status_code == 403
    assert _error_code(missing_origin) == "MUTATION_ORIGIN_FORBIDDEN"
    assert missing_mutation_header.status_code == 403
    assert _error_code(missing_mutation_header) == "MUTATION_HEADER_REQUIRED"
    assert audited_but_disabled.status_code == 503
    rows = _audit_rows(settings.database_path)
    assert [row[6] for row in rows if row[5] == "youtube.acquire"] == [
        "youtube.claim.submit"
    ]
    assert rows[-1][10] == 503
    assert configured_policy[1] is not None
    assert configured_policy[0].capability == "youtube.acquire"
    assert configured_policy[0].audit_action == "youtube.claim.submit"
    assert lookalike_policy[1] is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/operator/youtubeX",
        "/api/operator/youtube-evil",
        "/api/operator/youtube.evil",
        "/api/operator/youtube%2Devil",
    ],
)
def test_operator_lookalike_paths_are_not_local_operator_paths(
    tailscale_client, path: str
) -> None:
    client, _ = tailscale_client
    response = client.get(path)
    assert response.status_code == 401
    assert _error_code(response) == "IDENTITY_REQUIRED"


def test_bare_operator_prefix_stays_on_the_local_operator_channel(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    response = client.get("/api/operator/youtube")
    assert response.status_code == 404


def test_remote_operator_lookalike_paths_are_sanitized_not_found(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    for path in ("/api/operator/youtubeX", "/api/operator/youtube-evil"):
        response = client.get(path, headers=_serve_headers())
        assert response.status_code == 404
        assert _error_code(response) == "NOT_FOUND"


def test_operator_routes_remain_available_on_local_channel(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.post("/api/operator/youtube/claims", json={})
    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "YOUTUBE_OPERATOR_NOT_CONFIGURED"


def test_route_policies_match_the_application_route_inventory(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    found: list[tuple[str, list[str]]] = []

    def walk(routes) -> None:
        for route in routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                found.append(
                    (
                        route.path,
                        [
                            method
                            for method in sorted(route.methods)
                            if method not in ("HEAD", "OPTIONS")
                        ],
                    )
                )
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                walk(route.routes)

    walk(client.app.routes)
    assert found

    def concrete(path: str) -> str:
        resolved = path
        while "{" in resolved:
            start = resolved.index("{")
            end = resolved.index("}", start)
            name = resolved[start + 1 : end]
            replacement = str(uuid.uuid4()) if name == "upload_id" else f"{name}-value"
            resolved = resolved[:start] + replacement + resolved[end + 1 :]
        return resolved

    for path, methods in found:
        for method in methods:
            matching = [
                policy
                for policy in ROUTE_POLICIES
                if policy.match(method, concrete(path)) is not None
            ]
            assert len(matching) == 1, (method, path)
    for policy in ROUTE_POLICIES:
        assert any(
            policy.match(method, concrete(path)) is not None
            for path, methods in found
            for method in methods
        ), (policy.method, policy.pattern.pattern)


# --- Unclassified routes fail closed --------------------------------------


def test_unclassified_route_is_denied_for_missing_identity(
    unclassified_client,
) -> None:
    client, invoked = unclassified_client
    headers = _serve_headers()
    del headers["Tailscale-User-Login"]
    response = client.get("/api/internal-unclassified", headers=headers)
    assert response.status_code == 404
    assert _error_code(response) == "NOT_FOUND"
    assert invoked == []


@pytest.mark.parametrize("login", [STRANGER_LOGIN, USER_LOGIN, ADMIN_LOGIN])
def test_unclassified_route_is_denied_for_every_identity_class(
    unclassified_client, login: str
) -> None:
    client, invoked = unclassified_client
    response = client.get(
        "/api/internal-unclassified", headers=_serve_headers(login)
    )
    assert response.status_code == 404
    assert _error_code(response) == "NOT_FOUND"
    assert invoked == []


def test_unclassified_route_mutation_is_denied_with_valid_mutation_proof(
    unclassified_client,
) -> None:
    client, invoked = unclassified_client
    response = client.post(
        "/api/internal-unclassified",
        headers=_mutation_headers(),
        json={},
    )
    assert response.status_code == 404
    assert _error_code(response) == "NOT_FOUND"
    assert invoked == []


def test_unclassified_route_response_is_indistinguishable_from_nonexistent(
    unclassified_client,
) -> None:
    client, _ = unclassified_client
    unclassified = client.get("/api/internal-unclassified", headers=_serve_headers())
    nonexistent = client.get("/api/no-such-route", headers=_serve_headers())
    assert unclassified.status_code == nonexistent.status_code == 404
    assert unclassified.json() == nonexistent.json()


def test_nonexistent_remote_path_is_sanitized_not_found(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get("/api/no-such-route", headers=_serve_headers())
    assert response.status_code == 404
    assert _error_code(response) == "NOT_FOUND"


def test_find_route_policy_returns_fail_closed_fallback() -> None:
    policy, match = find_route_policy("GET", "/api/no-such-route")
    assert policy is _UNCLASSIFIED_FALLBACK_POLICY
    assert match is None
    for method, path in (
        ("GET", "/health"),
        ("GET", "/"),
        ("GET", "/assets/app.js"),
        ("GET", "/api/identity/me"),
        ("GET", "/api/audience/me"),
        ("GET", "/api/status/cloud"),
        ("GET", "/api/media"),
        ("GET", "/api/workspace/media"),
        ("POST", "/api/workspace/media/11111111-1111-4111-8111-111111111111/analysis-proposals"),
        ("GET", "/api/admin/analysis-proposals"),
        ("POST", "/api/canonical-tags"),
        ("GET", f"/api/operator/youtube/claims/{uuid.uuid4()}"),
    ):
        _, matched = find_route_policy(method, path)
        assert matched is not None, (method, path)


def test_error_responses_are_sanitized(tailscale_client) -> None:
    client, settings = tailscale_client
    responses = [
        client.get("/api/media"),
        client.get("/api/media", headers=_serve_headers(STRANGER_LOGIN)),
        client.post(
            "/api/canonical-tags",
            headers=_mutation_headers(USER_LOGIN),
            json={"key": "alpha", "display_name": "Alpha"},
        ),
    ]
    for response in responses:
        assert response.headers["content-type"] == "application/json"
        payload = response.json()
        assert set(payload) == {"error"}
        assert set(payload["error"]) == {"code", "message"}
        body = response.text
        assert "Traceback" not in body
        assert str(settings.database_path) not in body
        assert str(tmp_path_sentinel(settings)) not in body


def tmp_path_sentinel(settings: KronikaSettings) -> Path:
    return settings.database_path.parent


# --- CSRF, Origin, and CORS ----------------------------------------------


def test_same_origin_mutation_with_header_succeeds(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(),
        json={"key": "beta", "display_name": "Beta"},
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://nuc-1.example.ts.net",
        "https://nuc-1.example.ts.net.",
        "https://nuc-1.example.ts.net:443",
        "https://nuc-1.example.ts.net/",
        "HTTPS://nuc-1.example.ts.net",
        "null",
    ],
)
def test_hostile_or_inexact_origin_is_rejected(
    tailscale_client, origin: str
) -> None:
    client, _ = tailscale_client
    headers = _mutation_headers()
    headers["Origin"] = origin
    response = client.post(
        "/api/canonical-tags",
        headers=headers,
        json={"key": "gamma", "display_name": "Gamma"},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_ORIGIN_FORBIDDEN"


def test_missing_origin_is_rejected_for_mutations(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _mutation_headers()
    del headers["Origin"]
    response = client.post(
        "/api/canonical-tags",
        headers=headers,
        json={"key": "delta", "display_name": "Delta"},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_ORIGIN_FORBIDDEN"


def test_duplicate_origin_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers=[
            ("Tailscale-User-Login", ADMIN_LOGIN),
            ("Tailscale-User-Name", "Admin User"),
            ("X-Forwarded-Proto", "https"),
            ("X-Forwarded-Host", EXTERNAL_HOST),
            ("Origin", EXTERNAL_ORIGIN),
            ("Origin", EXTERNAL_ORIGIN),
            ("X-FrameNest-Request", "1"),
            ("Content-Type", "application/json"),
        ],
        content=b'{"key": "epsilon", "display_name": "Epsilon"}',
    )
    assert response.status_code == 400
    assert _error_code(response) == "INGRESS_HEADERS_CONFLICT"


def test_missing_mutation_header_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _mutation_headers()
    del headers["X-FrameNest-Request"]
    response = client.post(
        "/api/canonical-tags",
        headers=headers,
        json={"key": "zeta", "display_name": "Zeta"},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_HEADER_REQUIRED"


def test_cross_origin_form_style_mutation_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers={
            **_serve_headers(),
            "Origin": "https://evil.example",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=b"key=eta&display_name=Eta",
    )
    assert response.status_code == 403
    same_origin_form = client.post(
        "/api/canonical-tags",
        headers={
            **_serve_headers(),
            "Origin": EXTERNAL_ORIGIN,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=b"key=eta&display_name=Eta",
    )
    assert same_origin_form.status_code == 403
    assert _error_code(same_origin_form) == "MUTATION_HEADER_REQUIRED"


def test_responses_carry_no_cors_headers(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.get(
        "/api/media",
        headers={**_serve_headers(), "Origin": "https://evil.example"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_hostile_preflight_is_not_authorized(tailscale_client) -> None:
    client, _ = tailscale_client
    response = client.options(
        "/api/media",
        headers={
            **_serve_headers(),
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code in (400, 401, 403, 404, 405)
    assert "access-control-allow-origin" not in response.headers


def test_legitimate_upload_content_types_remain_functional(tailscale_client) -> None:
    client, _ = tailscale_client
    missing = str(uuid.uuid4())
    response = client.patch(
        f"/api/uploads/{missing}",
        headers={
            **_mutation_headers(),
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        },
        content=b"chunk",
    )
    assert response.status_code in {404, 503}
    assert response.json()["error"]["code"] in {
        "UPLOAD_SESSION_NOT_FOUND",
        "UPLOAD_CAPABILITY_NOT_CONFIGURED",
    }
    assert _error_code(response) != "CAPABILITY_DENIED"
    delete = client.delete(f"/api/uploads/{missing}", headers=_mutation_headers())
    assert delete.status_code in {404, 503}
    assert delete.json()["error"]["code"] in {
        "UPLOAD_SESSION_NOT_FOUND",
        "UPLOAD_CAPABILITY_NOT_CONFIGURED",
    }
    assert _error_code(delete) != "CAPABILITY_DENIED"


# --- Audit ---------------------------------------------------------------


def test_privileged_success_is_recorded_exactly(tailscale_client) -> None:
    client, settings = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(),
        json={"key": "theta", "display_name": "Theta"},
    )
    assert response.status_code == 201
    request_id = response.headers["x-request-id"]
    uuid.UUID(request_id)
    rows = _audit_rows(settings.database_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == request_id
    assert row["actor_login"] == ADMIN_LOGIN
    assert row["actor_key"] == ADMIN_LOGIN
    assert row["identity_provenance"] == "tailscale-serve"
    assert row["role"] == "admin"
    assert row["capability"] == "metadata.canonical.write"
    assert row["action"] == "canonical_tag.create"
    assert row["target_type"] == "canonical_tag"
    assert row["target_id"] is None
    assert row["outcome"] == "allowed"
    assert row["http_status"] == 201


def test_ordinary_user_denial_is_recorded(tailscale_client) -> None:
    client, settings = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(USER_LOGIN),
        json={"key": "iota", "display_name": "Iota"},
    )
    assert response.status_code == 403
    rows = _audit_rows(settings.database_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == response.headers["x-request-id"]
    assert row["actor_login"] == USER_LOGIN
    assert row["role"] == "user"
    assert row["capability"] == "metadata.canonical.write"
    assert row["action"] == "canonical_tag.create"
    assert row["outcome"] == "denied"
    assert row["http_status"] == 403


def test_publication_attempt_is_audited_before_action_with_final_status(
    tailscale_client,
) -> None:
    client, settings = tailscale_client
    media_id = str(uuid.uuid4())

    response = client.put(
        f"/api/admin/media/{media_id}/content-publication",
        headers=_mutation_headers(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MEDIA_NOT_FOUND"
    rows = _audit_rows(settings.database_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == response.headers["x-request-id"]
    assert row["capability"] == "media.content.publish"
    assert row["action"] == "media.content_publish"
    assert row["target_type"] == "media"
    assert row["target_id"] == media_id
    assert row["outcome"] == "allowed"
    assert row["http_status"] == 404


def test_publication_incomplete_failure_and_repeated_attempts_are_audited(
    tailscale_client,
) -> None:
    client, settings = tailscale_client
    incomplete_id = _seed_publication_target(
        settings.database_path,
        ready=False,
    )
    incomplete = client.put(
        f"/api/admin/media/{incomplete_id}/content-publication",
        headers=_mutation_headers(),
    )
    assert incomplete.status_code == 409

    ready_id = _seed_publication_target(settings.database_path, ready=True)
    first = client.put(
        f"/api/admin/media/{ready_id}/content-publication",
        headers=_mutation_headers(),
    )
    repeated = client.put(
        f"/api/admin/media/{ready_id}/content-publication",
        headers=_mutation_headers(),
    )
    assert first.status_code == 201
    assert repeated.status_code == 200

    failed_id = _seed_publication_target(settings.database_path, ready=True)
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("DROP TABLE media_content_publications")
        connection.commit()
    finally:
        connection.close()
    failed = client.put(
        f"/api/admin/media/{failed_id}/content-publication",
        headers=_mutation_headers(),
    )
    assert failed.status_code == 500

    rows = [
        row
        for row in _audit_rows(settings.database_path)
        if row["action"] == "media.content_publish"
    ]
    assert [row["target_id"] for row in rows] == [
        incomplete_id,
        ready_id,
        ready_id,
        failed_id,
    ]
    assert [row["http_status"] for row in rows] == [409, 201, 200, 500]


def test_denied_publication_attempt_is_audited_without_mutation(
    tailscale_client,
) -> None:
    client, settings = tailscale_client
    media_id = _seed_publication_target(settings.database_path, ready=True)

    denied = client.put(
        f"/api/admin/media/{media_id}/content-publication",
        headers=_mutation_headers(USER_LOGIN),
    )

    assert denied.status_code == 403
    rows = _audit_rows(settings.database_path)
    assert len(rows) == 1
    assert rows[0]["action"] == "media.content_publish"
    assert rows[0]["target_id"] == media_id
    assert rows[0]["outcome"] == "denied"
    assert rows[0]["http_status"] == 403
    connection = sqlite3.connect(settings.database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM media_content_publications WHERE media_id = ?",
            (media_id,),
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_unmapped_privileged_denial_is_recorded_once(tailscale_client) -> None:
    client, settings = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(STRANGER_LOGIN),
        json={"key": "kappa", "display_name": "Kappa"},
    )
    assert response.status_code == 403
    rows = _audit_rows(settings.database_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["actor_login"] == STRANGER_LOGIN
    assert row["role"] == "unmapped"
    assert row["outcome"] == "denied"
    assert row["http_status"] == 403


def test_audit_records_no_credentials_or_raw_headers(tailscale_client) -> None:
    client, settings = tailscale_client
    response = client.post(
        "/api/canonical-tags",
        headers={
            **_mutation_headers(),
            "Cookie": "session=SECRET-COOKIE-MARKER",
            "Authorization": "Bearer SECRET-TOKEN-MARKER",
        },
        json={"key": "lambda", "display_name": "Lambda"},
    )
    assert response.status_code == 201
    rows = _audit_rows(settings.database_path)
    assert len(rows) == 1
    recorded = " ".join(str(value) for value in tuple(rows[0]))
    assert "SECRET-COOKIE-MARKER" not in recorded
    assert "SECRET-TOKEN-MARKER" not in recorded


def test_missing_additional_capability_is_denied(tailscale_client) -> None:
    client, settings = tailscale_client
    media_id = _seed_publication_target(settings.database_path, ready=True)
    policy, match = find_route_policy(
        "PUT", f"/api/admin/media/{media_id}/content-publication"
    )
    assert match is not None
    original = policy.additional_capabilities
    reduced_admin = CAPABILITIES_BY_ROLE[ROLE_ADMIN] - {
        CAPABILITY_METADATA_CANONICAL_WRITE
    }
    policy.additional_capabilities = (CAPABILITY_METADATA_CANONICAL_WRITE,)
    try:
        with patch.dict(
            "kronika.domain.identity_access.CAPABILITIES_BY_ROLE",
            {ROLE_ADMIN: reduced_admin, ROLE_USER: CAPABILITIES_BY_ROLE[ROLE_USER]},
        ):
            response = client.put(
                f"/api/admin/media/{media_id}/content-publication",
                headers=_mutation_headers(),
            )
        assert response.status_code == 403
        assert _error_code(response) == "CAPABILITY_DENIED"
        assert CAPABILITY_MEDIA_CONTENT_PUBLISH in reduced_admin
        assert CAPABILITY_METADATA_CANONICAL_WRITE not in reduced_admin
    finally:
        policy.additional_capabilities = original


def test_audit_failure_blocks_the_privileged_action(tailscale_client) -> None:
    client, settings = tailscale_client
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("DROP TABLE security_audit_events")
        connection.commit()
    finally:
        connection.close()
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(),
        json={"key": "mu", "display_name": "Mu"},
    )
    assert response.status_code == 500
    assert _error_code(response) == "AUDIT_UNAVAILABLE"
    assert response.headers["x-request-id"]
    tags = client.get("/api/canonical-tags", headers=_serve_headers())
    assert all(tag["key"] != "mu" for tag in tags.json()["tags"])


def test_correlation_id_is_present_on_remote_responses(tailscale_client) -> None:
    client, _ = tailscale_client
    ok = client.get("/api/media", headers=_serve_headers())
    uuid.UUID(ok.headers["x-request-id"])
    denied = client.get("/api/media", headers=_serve_headers(STRANGER_LOGIN))
    uuid.UUID(denied.headers["x-request-id"])


# --- Requester-private media detail ingress ------------------------------


OWNER_LOGIN = "owner@example.com"
FOREIGN_LOGIN = "foreign@example.com"
PRIVATE_MEDIA_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PRIVATE_LOCATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PRIVATE_CLAIM_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
UNKNOWN_MEDIA_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
PRIVATE_TITLE = "Owner Private Ingress Title"
PRIVATE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100


def _seed_requester_private_media(
    database_path: Path,
    *,
    library_root: Path,
    owner_login: str,
) -> None:
    library_root.mkdir(parents=True, exist_ok=True)
    (library_root / "owner-private.mp4").write_bytes(PRIVATE_MP4)
    engine = create_sqlite_engine(database_path)
    try:
        device = Device(id=DeviceId.new(), display_name="Ingress Private Device")
        SqliteDeviceRepository(engine).add(device)
        library_id = LibraryId.new()
        flavor = (
            LibraryPathFlavor.WINDOWS if os.name == "nt" else LibraryPathFlavor.POSIX
        )
        SqliteLibraryRepository(engine).add(
            Library(
                id=library_id,
                device_id=device.id,
                display_name="Ingress Private Library",
                root=LibraryRoot(
                    flavor=flavor,
                    path=os.path.normpath(str(library_root)),
                ),
            )
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO logical_media "
                    "(id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, 'video', 10, 10)"
                ),
                {"id": PRIVATE_MEDIA_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO media_metadata "
                    "(media_id, display_title, description, collection_key, "
                    "processed_at_ms, created_at_ms, updated_at_ms, "
                    "content_category, acquisition_source) "
                    "VALUES (:media_id, :title, :description, NULL, NULL, 10, 10, "
                    "'general', 'youtube_manual_claim')"
                ),
                {
                    "media_id": PRIVATE_MEDIA_ID,
                    "title": PRIVATE_TITLE,
                    "description": "Private requester description",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations "
                    "(id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, "
                    "updated_at_ms) "
                    "VALUES (:id, :media_id, :library_id, :relative, 'available', "
                    ":size, 1, 10, 10)"
                ),
                {
                    "id": PRIVATE_LOCATION_ID,
                    "media_id": PRIVATE_MEDIA_ID,
                    "library_id": library_id.to_string(),
                    "relative": "owner-private.mp4",
                    "size": len(PRIVATE_MP4),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO youtube_acquisition_claims "
                    "(id, state, acquisition_source, submitted_url, canonical_url, "
                    "youtube_video_id, extractor_key, retry_of_claim_id, "
                    "resolved_claim_id, upload_id, media_id, media_location_id, "
                    "confirmation_method, confirmed_at_ms, upstream_title, "
                    "upstream_channel, upstream_channel_id, upstream_source_date, "
                    "downloader_name, downloader_version, extractor_version, "
                    "selected_video_format_id, selected_audio_format_id, "
                    "remote_filename, generated_filename, staging_key, "
                    "downloaded_size_bytes, created_at_ms, updated_at_ms, "
                    "downloaded_at_ms, completed_at_ms, catalog_removed_at_ms, "
                    "failure_stage, failure_code, cleanup_state, "
                    "cleanup_completed_at_ms, version, created_by_login_key) VALUES "
                    "(:id, 'duplicate_resolved', 'youtube_manual_claim', "
                    ":submitted, :canonical, :video_id, 'Youtube', NULL, NULL, "
                    "NULL, :media_id, :location_id, 'interactive', 10, "
                    "'Upstream', 'Channel', 'channel', '2026-01-02', 'yt-dlp', "
                    "'2026.07.23', '2026.07.23', '18', NULL, 'remote.mp4', "
                    ":generated, :staging, :size, 10, 20, NULL, 20, NULL, NULL, NULL, "
                    "'complete', 20, 1, :owner)"
                ),
                {
                    "id": PRIVATE_CLAIM_ID,
                    "submitted": "https://youtu.be/AbCdEf123_-",
                    "canonical": "https://www.youtube.com/watch?v=AbCdEf123_-",
                    "video_id": "AbCdEf123_-",
                    "media_id": PRIVATE_MEDIA_ID,
                    "location_id": PRIVATE_LOCATION_ID,
                    "generated": "youtube-AbCdEf123_-.mp4",
                    "staging": "a" * 32,
                    "size": len(PRIVATE_MP4),
                    "owner": owner_login,
                },
            )
    finally:
        dispose_engine(engine)


def test_media_detail_route_policy_uses_gallery_read_without_shadowing() -> None:
    detail_policy, detail_match = find_route_policy(
        "GET", f"/api/media/{PRIVATE_MEDIA_ID}"
    )
    list_policy, list_match = find_route_policy("GET", "/api/media")
    metadata_policy, metadata_match = find_route_policy(
        "GET", f"/api/media/{PRIVATE_MEDIA_ID}/metadata"
    )
    content_policy, content_match = find_route_policy(
        "GET",
        f"/api/media/{PRIVATE_MEDIA_ID}/locations/{PRIVATE_LOCATION_ID}/content",
    )
    download_policy, download_match = find_route_policy(
        "GET",
        f"/api/media/{PRIVATE_MEDIA_ID}/locations/{PRIVATE_LOCATION_ID}/download",
    )

    assert detail_match is not None
    assert detail_policy.capability == "gallery.read"
    assert list_match is not None
    assert list_policy.capability == "gallery.read"
    assert metadata_match is not None
    assert metadata_policy.capability == "gallery.read"
    assert content_match is not None
    assert content_policy.capability == "media.original.read"
    assert download_match is not None
    assert download_policy.capability == "media.download"

    detail_matches = [
        policy
        for policy in ROUTE_POLICIES
        if policy.match("GET", f"/api/media/{PRIVATE_MEDIA_ID}") is not None
    ]
    list_matches = [
        policy
        for policy in ROUTE_POLICIES
        if policy.match("GET", "/api/media") is not None
    ]
    assert len(detail_matches) == 1
    assert len(list_matches) == 1
    assert detail_matches[0] is not list_matches[0]
    assert detail_policy.match("GET", "/api/media") is None
    assert list_policy.match("GET", f"/api/media/{PRIVATE_MEDIA_ID}") is None
    assert detail_policy.match(
        "GET", f"/api/media/{PRIVATE_MEDIA_ID}/metadata"
    ) is None


def test_requester_private_media_detail_reaches_application_through_ingress(
    tmp_path: Path,
) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={
            ADMIN_LOGIN: "admin",
            OWNER_LOGIN: "user",
            FOREIGN_LOGIN: "user",
        },
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    _seed_requester_private_media(
        settings.database_path,
        library_root=tmp_path / "library",
        owner_login=OWNER_LOGIN,
    )

    app = create_app(settings=settings)
    with TestClient(app) as client:
        owner_detail = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}",
            headers=_serve_headers(OWNER_LOGIN, "Owner"),
        )
        assert owner_detail.status_code == 200
        assert owner_detail.json()["media_id"] == PRIVATE_MEDIA_ID
        assert owner_detail.json()["display_title"] == PRIVATE_TITLE
        assert "error" not in owner_detail.json()

        foreign_detail = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}",
            headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
        )
        unknown_detail = client.get(
            f"/api/media/{UNKNOWN_MEDIA_ID}",
            headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
        )
        assert foreign_detail.status_code == unknown_detail.status_code == 404
        assert foreign_detail.json() == unknown_detail.json()
        assert _error_code(foreign_detail) == "MEDIA_NOT_FOUND"
        assert PRIVATE_TITLE not in foreign_detail.text
        assert OWNER_LOGIN not in foreign_detail.text
        assert "owner-private.mp4" not in foreign_detail.text

        admin_detail = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}",
            headers=_serve_headers(ADMIN_LOGIN),
        )
        assert admin_detail.status_code == 200
        assert admin_detail.json()["display_title"] == PRIVATE_TITLE

        missing_identity = client.get(f"/api/media/{PRIVATE_MEDIA_ID}")
        assert missing_identity.status_code == 401
        assert _error_code(missing_identity) == "IDENTITY_REQUIRED"

        stranger = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}",
            headers=_serve_headers(STRANGER_LOGIN),
        )
        assert stranger.status_code == 403
        assert _error_code(stranger) == "IDENTITY_NOT_AUTHORIZED"

        owner_gallery = client.get(
            "/api/media", headers=_serve_headers(OWNER_LOGIN, "Owner")
        )
        foreign_gallery = client.get(
            "/api/media", headers=_serve_headers(FOREIGN_LOGIN, "Foreign")
        )
        assert owner_gallery.status_code == foreign_gallery.status_code == 200
        assert owner_gallery.json()["items"] == []
        assert foreign_gallery.json()["items"] == []

        owner_metadata = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}/metadata",
            headers=_serve_headers(OWNER_LOGIN, "Owner"),
        )
        owner_content = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}/locations/{PRIVATE_LOCATION_ID}/content",
            headers=_serve_headers(OWNER_LOGIN, "Owner"),
        )
        owner_download = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}/locations/{PRIVATE_LOCATION_ID}/download",
            headers=_serve_headers(OWNER_LOGIN, "Owner"),
        )
        assert owner_metadata.status_code == 200
        assert owner_metadata.json()["display_title"] == PRIVATE_TITLE
        assert owner_content.status_code == 200
        assert owner_content.content == PRIVATE_MP4
        assert owner_download.status_code == 200
        assert owner_download.content == PRIVATE_MP4

        foreign_metadata = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}/metadata",
            headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
        )
        foreign_content = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}/locations/{PRIVATE_LOCATION_ID}/content",
            headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
        )
        foreign_download = client.get(
            f"/api/media/{PRIVATE_MEDIA_ID}/locations/{PRIVATE_LOCATION_ID}/download",
            headers=_serve_headers(FOREIGN_LOGIN, "Foreign"),
        )
        assert foreign_metadata.status_code == 404
        assert foreign_content.status_code == 404
        assert foreign_download.status_code == 404
        assert _error_code(foreign_metadata) != "NOT_FOUND"
        assert _error_code(foreign_content) != "NOT_FOUND"
        assert _error_code(foreign_download) != "NOT_FOUND"


# --- Companion extension origin allowlist --------------------------------


COMPANION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SPOOFED_COMPANION_ORIGIN = "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def companion_client(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={ADMIN_LOGIN: "admin", USER_LOGIN: "user"},
        companion_extension_origins=[COMPANION_ORIGIN],
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client, settings


def _companion_mutation_headers(login: str = USER_LOGIN) -> dict[str, str]:
    return {
        **_serve_headers(login, "User"),
        "Origin": COMPANION_ORIGIN,
        "X-FrameNest-Request": "1",
    }


def test_companion_origin_is_accepted_only_on_flagged_companion_routes(
    companion_client,
) -> None:
    client, settings = companion_client
    media_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    submit = client.post(
        "/api/x/requests",
        headers=_companion_mutation_headers(),
        json={"url": "https://x.com/a/status/123456789"},
    )
    retry = client.post(
        f"/api/x/requests/{uuid.uuid4()}/retry",
        headers=_companion_mutation_headers(),
        json={},
    )
    opened = client.post(
        f"/api/companion/review-inbox/{media_id}/opened",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={"analysis_run_id": run_id},
    )
    apply = client.post(
        f"/api/companion/review-inbox/{media_id}/apply",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={
            "analysis_run_id": run_id,
            "fields": ["display_title"],
            "tag_keys": [],
        },
    )
    settings_put = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={"automatic_media_analysis_enabled": False},
    )
    unflagged = client.post(
        "/api/canonical-tags",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={"key": "alpha", "display_name": "Alpha"},
    )

    assert submit.status_code != 403 or _error_code(submit) != "MUTATION_ORIGIN_FORBIDDEN"
    assert _error_code(submit) != "MUTATION_ORIGIN_FORBIDDEN"
    assert retry.status_code != 403 or _error_code(retry) != "MUTATION_ORIGIN_FORBIDDEN"
    assert _error_code(retry) != "MUTATION_ORIGIN_FORBIDDEN"
    assert _error_code(opened) != "MUTATION_ORIGIN_FORBIDDEN"
    assert _error_code(apply) != "MUTATION_ORIGIN_FORBIDDEN"
    assert settings_put.status_code == 200
    assert settings_put.json() == {"automatic_media_analysis_enabled": False}
    assert unflagged.status_code == 403
    assert _error_code(unflagged) == "MUTATION_ORIGIN_FORBIDDEN"
    assert "access-control-allow-origin" not in submit.headers
    rows = _audit_rows(settings.database_path)
    assert any(row["action"] == "x.request.submit" for row in rows)


def test_empty_companion_allowlist_rejects_extension_origin(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    response = client.post(
        "/api/x/requests",
        headers=_companion_mutation_headers(),
        json={"url": "https://x.com/a/status/123456789"},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_ORIGIN_FORBIDDEN"
    opened = client.post(
        f"/api/companion/review-inbox/{uuid.uuid4()}/opened",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={"analysis_run_id": str(uuid.uuid4())},
    )
    apply = client.post(
        f"/api/companion/review-inbox/{uuid.uuid4()}/apply",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={
            "analysis_run_id": str(uuid.uuid4()),
            "fields": ["display_title"],
            "tag_keys": [],
        },
    )
    assert opened.status_code == 403
    assert _error_code(opened) == "MUTATION_ORIGIN_FORBIDDEN"
    assert apply.status_code == 403
    assert _error_code(apply) == "MUTATION_ORIGIN_FORBIDDEN"
    settings_put = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_companion_mutation_headers(ADMIN_LOGIN),
        json={"automatic_media_analysis_enabled": False},
    )
    assert settings_put.status_code == 403
    assert _error_code(settings_put) == "MUTATION_ORIGIN_FORBIDDEN"
    inbox = client.get(
        "/api/companion/review-inbox",
        headers=_serve_headers(ADMIN_LOGIN),
    )
    assert inbox.status_code == 200
    own = client.get(
        "/api/companion/own-history",
        headers=_serve_headers(USER_LOGIN, "User"),
    )
    assert own.status_code == 200


def test_spoofed_or_absent_companion_origin_is_rejected(companion_client) -> None:
    client, _ = companion_client
    spoofed = client.post(
        "/api/x/requests",
        headers={
            **_serve_headers(USER_LOGIN, "User"),
            "Origin": SPOOFED_COMPANION_ORIGIN,
            "X-FrameNest-Request": "1",
        },
        json={"url": "https://x.com/a/status/123456789"},
    )
    missing = client.post(
        "/api/x/requests",
        headers={
            **_serve_headers(USER_LOGIN, "User"),
            "X-FrameNest-Request": "1",
        },
        json={"url": "https://x.com/a/status/123456789"},
    )
    missing_header = client.post(
        "/api/x/requests",
        headers={
            **_serve_headers(USER_LOGIN, "User"),
            "Origin": COMPANION_ORIGIN,
        },
        json={"url": "https://x.com/a/status/123456789"},
    )
    assert spoofed.status_code == 403
    assert _error_code(spoofed) == "MUTATION_ORIGIN_FORBIDDEN"
    assert missing.status_code == 403
    assert _error_code(missing) == "MUTATION_ORIGIN_FORBIDDEN"
    assert missing_header.status_code == 403
    assert _error_code(missing_header) == "MUTATION_HEADER_REQUIRED"


def test_web_ui_mutation_path_is_unchanged_when_companion_origins_are_configured(
    companion_client,
) -> None:
    client, _ = companion_client
    response = client.post(
        "/api/canonical-tags",
        headers=_mutation_headers(),
        json={"key": "nu", "display_name": "Nu"},
    )
    assert response.status_code == 201


def test_companion_picker_route_is_readable_without_mutation_origin(
    companion_client,
) -> None:
    client, _ = companion_client
    response = client.get(
        "/api/x/companion/media",
        headers=_serve_headers(USER_LOGIN, "User"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["companion_api_version"] == "framenest-companion.v1"
    assert payload["items"] == []
    assert response.headers.get("cache-control") == "no-store"
    assert "access-control-allow-origin" not in response.headers


def test_companion_origin_post_with_alias_is_not_origin_forbidden(
    companion_client,
) -> None:
    client, _ = companion_client
    response = client.post(
        "/api/x/requests",
        headers=_companion_mutation_headers(),
        json={
            "url": "https://x.com/a/status/123456789",
            "alias": {"display_title": "Mine"},
        },
    )
    assert response.status_code != 403 or _error_code(response) != "MUTATION_ORIGIN_FORBIDDEN"
    assert _error_code(response) != "MUTATION_ORIGIN_FORBIDDEN"
    assert "access-control-allow-origin" not in response.headers


def test_companion_origin_put_alias_is_forbidden(companion_client) -> None:
    client, settings = companion_client
    media_id = _seed_publication_target(settings.database_path, ready=True)
    response = client.put(
        f"/api/media/{media_id}/alias",
        headers=_companion_mutation_headers(),
        json={"display_title": "Overlay"},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_ORIGIN_FORBIDDEN"


def test_web_origin_put_alias_succeeds_for_ordinary_user(companion_client) -> None:
    client, settings = companion_client
    media_id = str(uuid.uuid4())
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (?, 'video', 1, 1)",
            (media_id,),
        )
        connection.execute(
            "INSERT INTO media_content_publications "
            "(media_id, published_at_ms, publication_origin) "
            "VALUES (?, 1, 'admin_explicit')",
            (media_id,),
        )
        connection.commit()
    finally:
        connection.close()
    response = client.put(
        f"/api/media/{media_id}/alias",
        headers=_mutation_headers(USER_LOGIN),
        json={"display_title": "Overlay"},
    )
    assert response.status_code == 200
    assert response.json()["display_title"] == "Overlay"
    canonical = client.put(
        f"/api/media/{media_id}/metadata",
        headers=_mutation_headers(USER_LOGIN),
        json={"display_title": "Canonical", "tag_keys": []},
    )
    assert canonical.status_code == 403
    assert _error_code(canonical) == "CAPABILITY_DENIED"


def test_companion_apply_requires_dual_capabilities_and_hosted_origin(
    companion_client,
) -> None:
    client, settings = companion_client
    media_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    apply_path = f"/api/companion/review-inbox/{media_id}/apply"
    body = {
        "analysis_run_id": run_id,
        "fields": ["display_title"],
        "tag_keys": [],
    }
    ordinary = client.post(
        apply_path,
        headers=_companion_mutation_headers(USER_LOGIN),
        json=body,
    )
    assert ordinary.status_code == 403
    assert _error_code(ordinary) == "CAPABILITY_DENIED"
    missing_header = client.post(
        apply_path,
        headers={
            **_serve_headers(ADMIN_LOGIN),
            "Origin": COMPANION_ORIGIN,
        },
        json=body,
    )
    assert missing_header.status_code == 403
    assert _error_code(missing_header) == "MUTATION_HEADER_REQUIRED"
    hosted = client.post(
        apply_path,
        headers=_mutation_headers(ADMIN_LOGIN),
        json=body,
    )
    assert _error_code(hosted) != "MUTATION_ORIGIN_FORBIDDEN"
    reduced_admin = CAPABILITIES_BY_ROLE[ROLE_ADMIN] - {
        CAPABILITY_METADATA_CANONICAL_WRITE
    }
    with patch.dict(
        "kronika.domain.identity_access.CAPABILITIES_BY_ROLE",
        {ROLE_ADMIN: reduced_admin, ROLE_USER: CAPABILITIES_BY_ROLE[ROLE_USER]},
    ):
        publish_only = client.post(
            apply_path,
            headers=_companion_mutation_headers(ADMIN_LOGIN),
            json=body,
        )
    assert publish_only.status_code == 403
    assert _error_code(publish_only) == "CAPABILITY_DENIED"
    canonical_only = CAPABILITIES_BY_ROLE[ROLE_ADMIN] - {
        CAPABILITY_MEDIA_CONTENT_PUBLISH
    }
    with patch.dict(
        "kronika.domain.identity_access.CAPABILITIES_BY_ROLE",
        {ROLE_ADMIN: canonical_only, ROLE_USER: CAPABILITIES_BY_ROLE[ROLE_USER]},
    ):
        write_only = client.post(
            apply_path,
            headers=_companion_mutation_headers(ADMIN_LOGIN),
            json=body,
        )
    assert write_only.status_code == 403
    assert _error_code(write_only) == "CAPABILITY_DENIED"



# --- Administrator AI provider routes --------------------------------------


AI_ADMIN_ROUTE_SPECS = (
    ("GET", "/api/admin/ai/providers"),
    ("PUT", "/api/admin/ai/providers/provider-id-value"),
    ("DELETE", "/api/admin/ai/providers/provider-id-value"),
    ("PUT", "/api/admin/ai/active-selection"),
    ("POST", "/api/admin/ai/ping"),
    ("POST", "/api/admin/ai/pong"),
)


def test_ai_admin_route_policies_are_provider_operate_gated_and_non_companion() -> None:
    from kronika.domain.identity_access import CAPABILITY_PROVIDER_OPERATE

    for method, path in AI_ADMIN_ROUTE_SPECS:
        matching = [
            policy
            for policy in ROUTE_POLICIES
            if policy.match(method, path) is not None
        ]
        assert len(matching) == 1, (method, path)
        policy = matching[0]
        assert policy.capability == CAPABILITY_PROVIDER_OPERATE, (method, path)
        assert policy.additional_capabilities == (), (method, path)
        assert policy.companion_mutation is False, (method, path)


def test_ai_admin_routes_deny_ordinary_tailscale_identities(
    tailscale_client,
) -> None:
    client, _ = tailscale_client
    for method, path in AI_ADMIN_ROUTE_SPECS:
        headers = (
            _mutation_headers(USER_LOGIN)
            if method in {"PUT", "POST", "DELETE"}
            else _serve_headers(USER_LOGIN)
        )
        response = client.request(method, path, headers=headers, json={})
        assert response.status_code == 403, (method, path)
        assert _error_code(response) == "CAPABILITY_DENIED", (method, path)
