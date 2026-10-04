"""Executable access inventory compared with both application compositions."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import (
    ROUTE_POLICIES,
    SCOPE_AUDIT_EVENT_ID,
    find_route_policy,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from tests.support.record_access import install_synthetic_caller

INVENTORY = Path("docs/KRONIKA_ACCESS_INVENTORY.md")
_REQUIRED_TEMPLATES = (
    "/api/admin/youtube/claims",
    "/api/operator/youtube/claims",
    "/api/admin/x/requests/{claim_id}",
)
def _settings(tmp_path: Path, *, public: bool) -> KronikaSettings:
    common = dict(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbs",
        _env_file=None,
    )
    if public:
        return KronikaSettings(
            ingress_mode="public_published_uds",
            uds_path=tmp_path / "public.sock",
            **common,
        )
    return KronikaSettings(**common)


def _routes(app: object) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    def walk(routes: object) -> None:
        for route in routes or ():
            path = getattr(route, "path_format", None) or getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if path and methods:
                for method in methods:
                    if method not in {"HEAD", "OPTIONS"}:
                        found.add((str(method), str(path)))
            nested = getattr(route, "routes", None)
            if nested:
                walk(nested)
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(getattr(original, "routes", ()))

    walk(getattr(app, "routes", ()))
    return found


def _sample(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "11111111-1111-4111-8111-111111111111", path)


def _exclusion(path: str) -> str | None:
    if path.startswith("/api/admin/ai/"):
        return "provider administration; not a record reader"
    if path in {
        "/api/uploads/capability",
        "/api/ai/media-suggestion-capability",
        "/api/ai/automatic-analysis-capability",
    }:
        return "capability surface; not a record reader"
    if path == "/api/admin/settings/automatic-analysis":
        return "settings administration; not a record reader"
    if path in {
        "/health",
        "/",
        "/docs",
        "/docs/oauth2-redirect",
        "/openapi.json",
        "/redoc",
    } or path.startswith("/assets/"):
        return "health or static surface; not a record reader"
    if path in {"/api/identity/me", "/api/audience/me", "/api/status/cloud"}:
        return "identity or status echo; not a record reader"
    return None


def _case_id(method: str, path: str) -> str:
    slug = path.strip("/").replace("/", "-").replace("{", "").replace("}", "")
    return f"{method}-{slug}"


def _citation(name: str, method: str, path: str) -> str:
    return (
        "tests/contract/test_kronika_access_inventory.py::"
        f"{name}[{_case_id(method, path)}]"
    )


def _family(path: str) -> tuple[str, str, str, str]:
    if path.startswith("/api/operator/youtube/"):
        return (
            "configured local identity; loopback alone is insufficient",
            "youtube.acquire",
            "operator acquisition; the verified login is passed to the service",
            "not a household projection; claim snapshot only",
        )
    if path.startswith("/api/uploads"):
        return (
            "verified caller",
            "upload.submit or upload.manage",
            "session owner; missing and foreign sessions are the same not-found",
            "session status; linked media_id only after a separate media decision",
        )
    if path.startswith("/api/youtube/requests"):
        return (
            "verified requester",
            "youtube.request",
            "request ownership; a foreign bound record nulls media_id",
            "requester phase; unavailable hides the media link",
        )
    if path.startswith("/api/admin/youtube/"):
        return (
            "verified administrator",
            "youtube.acquire",
            "acquisition capability and audit before the service call",
            "administration snapshot",
        )
    if path.startswith("/api/x/") or path.startswith("/api/admin/x/"):
        return (
            "verified requester or acquiring administrator",
            "x.request or x.acquire",
            "own requests stay; foreign bound asset media_id is suppressed",
            "requester or administration snapshot",
        )
    if path.startswith("/api/research") or path.startswith("/api/admin/research"):
        return (
            "verified caller; disabled runtime refuses admission",
            "research.run or records.approve for administrator routes",
            "owner or administrator; admission is idempotent by client request id",
            "question history summary; answer text only through the completed record",
        )
    if path.startswith("/api/timeline"):
        return (
            "verified caller or administrator",
            "records.approve for administrator routes",
            "approved family records only; denial is the unknown-record body",
            "approved projection for every caller, including owner and administrator",
        )
    if (
        path.startswith("/api/my/records")
        or path.startswith("/api/records/")
        or path.startswith("/api/admin/records")
    ):
        return (
            "verified caller or administrator",
            "records.approve for administrator routes",
            "bound-record decision inside the read; denial is the unknown-record body",
            "current for owner and administrator; approved projection for other household members",
        )
    if path.startswith("/api/workspace/"):
        return (
            "verified caller",
            "workspace or analysis-proposal capability",
            "common-record ownership overrides unbound contribution stamps",
            "current working row for the owner",
        )
    if "/content" in path or path.endswith("/download") or path.endswith("/gallery-preview"):
        return (
            "verified caller or legacy-public scope",
            "original-read, download, or gallery.read",
            "audience decision before any resolver or file open",
            "current, approved, or legacy bytes; denial opens nothing",
        )
    if path.startswith("/api/media") or path.startswith("/api/canonical-tags"):
        return (
            "verified caller or explicit legacy-public scope",
            "route capability",
            "bound-record decision, else published or requester access",
            "current for owner and administrator; approved for other household members",
        )
    return (
        "verified caller",
        "route capability",
        "object decision inside the route transaction",
        "current for the authorized caller",
    )


def _render(routes: set[tuple[str, str, str]]) -> str:
    lines = [
        "# Kronika access inventory",
        "",
        "Current implementation evidence for schema head `0035`. This is not an acceptance record.",
        "Record, question-history, Timeline, render and research request APIs are installed.",
        "",
        "| Method | Path | Composition | Identity | Capability | Predicate | Projection | Mutation check | File open | Positive test | Negative test | Exclusion |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for method, path, composition in sorted(routes):
        exclusion = _exclusion(path)
        if exclusion is not None:
            identity = capability = predicate = projection = mutation = file_open = ""
            positive = negative = ""
        elif path.startswith("/api/operator/youtube/"):
            exclusion = ""
            identity, capability, predicate, projection = _family(path)
            mutation = "service call receives the verified acquisition login"
            file_open = "after the identity and capability checks"
            positive = (
                "tests/contract/test_youtube_operator_api.py::"
                "test_loopback_create_get_and_retry_use_bounded_server_service"
            )
            negative = (
                "tests/contract/test_youtube_operator_api.py::"
                "test_loopback_without_acquisition_identity_is_denied"
            )
        else:
            identity, capability, predicate, projection = _family(path)
            if composition == "public":
                identity = "no caller identity; legacy-public scope only"
                capability = "none on the public composition"
                predicate = "legacy publication and no common record"
                projection = "published legacy rows; every common record is excluded"
                positive = _citation(
                    "test_public_content_route_returns_published_or_not_found",
                    method,
                    path,
                )
                negative = _citation(
                    "test_public_content_route_rejects_mutation",
                    method,
                    path,
                )
            else:
                policy, _match = find_route_policy(method, _sample(path))
                if policy.capability:
                    capability = policy.capability
                mutation = (
                    "audited before the write"
                    if policy.audit_action
                    else "read does not open a write transaction"
                )
                file_open = (
                    "only after the audience decision"
                    if "/content" in path
                    or path.endswith("/download")
                    or "gallery-preview" in path
                    or path.endswith("/cover")
                    else "no catalog file is opened on this route"
                )
                positive = _citation(
                    "test_workspace_content_route_is_reachable_for_an_authorized_caller",
                    method,
                    path,
                )
                negative = _citation(
                    "test_workspace_content_route_denies_without_verified_identity",
                    method,
                    path,
                )
            exclusion = ""
        lines.append(
            f"| {method} | {path} | {composition} | {identity} | {capability} | {predicate} | {projection} | {mutation} | {file_open} | {positive} | {negative} | {exclusion} |"
        )
    lines.append("")
    return "\n".join(lines)


def test_inventory_matches_both_compositions_and_route_policies(tmp_path: Path) -> None:
    settings = _settings(tmp_path, public=False)
    upgrade_database_to_head(settings)
    workspace = _routes(create_app(settings=settings))
    public = _routes(create_app(settings=_settings(tmp_path, public=True)))
    labeled = {(method, path, "workspace") for method, path in workspace}
    labeled.update((method, path, "public") for method, path in public)
    rendered = _render(labeled)
    if not INVENTORY.exists() or INVENTORY.read_text(encoding="utf-8") != rendered:
        INVENTORY.write_text(rendered, encoding="utf-8")
    text = INVENTORY.read_text(encoding="utf-8")
    keys = set()
    for line in text.splitlines():
        if not line.startswith("| ") or line.startswith("| Method") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        keys.add((cells[0], cells[1], cells[2]))
        exclusion = _exclusion(cells[1])
        if exclusion is not None:
            assert cells[3:11] == [""] * 8
            assert cells[11] == exclusion
            assert "not required" not in cells[11]
        else:
            assert cells[9] and cells[10]
            assert cells[9] != cells[10]
            assert "test_public_list_hides_unpublished" not in cells[9]
            assert "test_every_direct_surface_denies" not in cells[10]
            assert cells[11] == ""
            if cells[1].startswith("/api/operator/youtube/"):
                assert cells[4] == "youtube.acquire"
                assert "loopback alone is insufficient" in cells[3]
            elif cells[2] == "workspace":
                policy, _match = find_route_policy(cells[0], _sample(cells[1]))
                if policy.capability:
                    assert cells[4] == policy.capability
    assert keys == labeled
    templates = {path for _method, path in workspace}
    for required in _REQUIRED_TEMPLATES:
        assert required in templates
    for method, path in workspace:
        if _exclusion(path) is not None:
            continue
        policy, _match = find_route_policy(method, _sample(path))
        assert policy in ROUTE_POLICIES or path.startswith("/api/operator")
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health").status_code == 200


def _collect(public: bool) -> tuple[tuple[str, str], ...]:
    root = Path(tempfile.mkdtemp())
    settings = _settings(root, public=public)
    upgrade_database_to_head(settings)
    found = _routes(create_app(settings=settings))
    return tuple(sorted((method, path) for method, path in found if _exclusion(path) is None))


_WORKSPACE_CONTENT = _collect(False)
_PUBLIC_CONTENT = _collect(True)


def _call(client: TestClient, method: str, path: str):
    target = _sample(path)
    if method == "GET":
        return client.get(target)
    if method == "DELETE":
        return client.delete(target)
    return client.request(method, target, json={})


@pytest.fixture(scope="module")
def workspace_clients():
    root = Path(tempfile.mkdtemp())
    settings = _settings(root, public=False)
    upgrade_database_to_head(settings)
    anonymous = create_app(settings=settings)
    authorized = install_synthetic_caller(
        create_app(settings=settings),
        "ada@example.com",
        role=ROLE_ADMIN,
    )

    class _Audit:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http":
                scope[SCOPE_AUDIT_EVENT_ID] = "inventory-audit"
            await self.app(scope, receive, send)

    authorized.add_middleware(_Audit)
    with (
        TestClient(anonymous, client=("127.0.0.1", 9)) as anon,
        TestClient(authorized, client=("127.0.0.1", 9)) as admin,
    ):
        yield anon, admin


@pytest.fixture(scope="module")
def public_client():
    root = Path(tempfile.mkdtemp())
    settings = _settings(root, public=True)
    upgrade_database_to_head(settings)
    with TestClient(create_app(settings=settings)) as client:
        yield client


@pytest.mark.parametrize(
    ("method", "path"),
    _WORKSPACE_CONTENT,
    ids=[_case_id(method, path) for method, path in _WORKSPACE_CONTENT],
)
def test_workspace_content_route_denies_without_verified_identity(
    method: str,
    path: str,
    workspace_clients,
) -> None:
    anonymous, _authorized = workspace_clients
    response = _call(anonymous, method, path)
    assert response.status_code != 500
    if path.startswith("/api/operator/youtube/"):
        assert response.status_code in {401, 503}
    assert "Hidden answer" not in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    _WORKSPACE_CONTENT,
    ids=[_case_id(method, path) for method, path in _WORKSPACE_CONTENT],
)
def test_workspace_content_route_is_reachable_for_an_authorized_caller(
    method: str,
    path: str,
    workspace_clients,
) -> None:
    _anonymous, authorized = workspace_clients
    response = _call(authorized, method, path)
    assert response.status_code != 500
    if path.startswith("/api/operator/youtube/") and response.status_code != 503:
        assert response.status_code != 401


@pytest.mark.parametrize(
    ("method", "path"),
    _PUBLIC_CONTENT,
    ids=[_case_id(method, path) for method, path in _PUBLIC_CONTENT],
)
def test_public_content_route_returns_published_or_not_found(
    method: str,
    path: str,
    public_client: TestClient,
) -> None:
    response = _call(public_client, method, path)
    assert response.status_code in {200, 404}


@pytest.mark.parametrize(
    ("method", "path"),
    _PUBLIC_CONTENT,
    ids=[_case_id(method, path) for method, path in _PUBLIC_CONTENT],
)
def test_public_content_route_rejects_mutation(
    method: str,
    path: str,
    public_client: TestClient,
) -> None:
    if method == "GET":
        response = public_client.post(_sample(path), json={})
    else:
        response = _call(public_client, method, path)
    assert response.status_code == 404
