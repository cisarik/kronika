"""X request API router behavior with a fake service."""

from __future__ import annotations

import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.kronika_identity import expected

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.adapters.api.x_request_api import (
    XRequestApiDependencies,
    create_x_request_api_router,
)
from kronika.application.x_acquisition import (
    XAcquisitionInvalidCursorError,
    XAcquisitionInvalidRequesterIdentityError,
    XAcquisitionNoRetryableAssetsError,
    XAcquisitionNotRetryableError,
)
from kronika.domain.identity_access import (
    CAPABILITY_X_REQUEST,
    IdentityContext,
)


def _identity(login: str) -> IdentityContext:
    capabilities = frozenset({CAPABILITY_X_REQUEST, "gallery.read"})
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login,
        role="user",
        capabilities=capabilities,
        provenance="tailscale-serve",
    )


def _app(service) -> FastAPI:
    router = create_x_request_api_router(
        XRequestApiDependencies(service=service, audit_recorder=None, enabled=True)
    )
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_identity(request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity("alice")
        return await call_next(request)

    return app


def _snapshot(claim_id: object, state: str = "submitted") -> types.SimpleNamespace:
    claim_id = str(claim_id)
    return types.SimpleNamespace(
        claim_id=claim_id,
        request_id=claim_id,
        phase="queued" if state == "submitted" else "completed",
        state=state,
        x_post_id="123",
        submitted_url="https://x.com/a/status/123",
        canonical_url="https://x.com/a/status/123",
        title=None,
        failure_code=None,
        retry_of_claim_id=None,
        created_at_ms=1,
        updated_at_ms=1,
        completed_at_ms=None,
        assets=[],
    )


_VALID_ID = "00000000-0000-4000-8000-000000000001"


def _service_fake():
    return types.SimpleNamespace(
        submit=lambda url, login_key, alias=None, content_category=None: types.SimpleNamespace(
            request_id=_VALID_ID, submission_result="new"
        ),
        list_owned=lambda login_key, limit, cursor: types.SimpleNamespace(
            items=[_snapshot(_VALID_ID)], next_cursor=None
        ),
        get_owned=lambda cid, login_key: _snapshot(cid),
        retry=lambda cid, login_key: _snapshot(cid, state="queued"),
    )


def test_submit_x_request_returns_item() -> None:
    client = TestClient(_app(_service_fake()))
    response = client.post("/api/x/requests", json={"url": "https://x.com/a/status/123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == _VALID_ID
    assert payload["phase"] == "queued"


def test_missing_identity_fails_closed() -> None:
    router = create_x_request_api_router(
        XRequestApiDependencies(service=_service_fake(), audit_recorder=None, enabled=True)
    )
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/x/requests", json={"url": "https://x.com/a/status/123"}
    )
    assert response.status_code == 401


def test_invalid_url_maps_to_sanitized_422() -> None:
    from kronika.domain.x_acquisition import FrameNestXUrlError

    class _InvalidService:
        def submit(self, url: str, login_key: str, alias=None, content_category=None):
            raise FrameNestXUrlError("Invalid public X post URL.")

    client = TestClient(_app(_InvalidService()))
    for hostile in [
        "http://x.com/a/status/1",
        "https://x.com.attacker.example/a/status/1",
        "https://evil.com/a/status/1",
        "https://x.com/a/status/abc",
        "https://x.com/a/status/1/photo/2",
        "https://x.com/a/status/1?a=1",
        "https://x.com/a/status/1#frag",
        "https://x.com:8443/a/status/1",
        "https://user:pass@x.com/a/status/1",
        "not a url",
    ]:
        response = client.post(
            "/api/x/requests", json={"url": hostile}
        )
        assert response.status_code == 422, hostile
        body = response.json()
        assert body["error"]["code"] == "X_REQUEST_INVALID_URL", hostile
        assert "Invalid" in body["error"]["message"]


def test_unexpected_internal_exception_is_not_mislabeled_422() -> None:
    class _BoomService:
        def submit(self, url: str, login_key: str, alias=None, content_category=None):
            raise RuntimeError("unexpected internal failure")

    # raise_server_exceptions=False lets a genuine internal failure surface as
    # an HTTP 500 response (instead of re-raising in the test client).
    client = TestClient(_app(_BoomService()), raise_server_exceptions=False)
    response = client.post(
        "/api/x/requests", json={"url": "https://x.com/a/status/1"}
    )
    # An unexpected internal exception must remain an internal failure, not be
    # folded into the requester-invalid 422 contract.
    assert response.status_code == 500


def test_omitted_alias_preserves_today_submit_contract() -> None:
    captured: dict[str, object] = {}

    def submit(url, login_key, alias=None, content_category=None):
        captured["alias"] = alias
        return types.SimpleNamespace(request_id=_VALID_ID, submission_result="new")

    service = _service_fake()
    service.submit = submit
    client = TestClient(_app(service))
    response = client.post("/api/x/requests", json={"url": "https://x.com/a/status/123"})
    assert response.status_code == 200
    assert captured["alias"] is None


def test_optional_alias_is_parsed_and_login_key_is_forbidden() -> None:
    captured: dict[str, object] = {}

    def submit(url, login_key, alias=None, content_category=None):
        captured["alias"] = alias
        return types.SimpleNamespace(request_id=_VALID_ID, submission_result="new")

    service = _service_fake()
    service.submit = submit
    client = TestClient(_app(service))
    response = client.post(
        "/api/x/requests",
        json={
            "url": "https://x.com/a/status/123",
            "alias": {"display_title": "Mine", "tag_keys": ["meme"]},
        },
    )
    assert response.status_code == 200
    assert captured["alias"] is not None
    assert captured["alias"].display_title.value == "Mine"
    forbidden = client.post(
        "/api/x/requests",
        json={"url": "https://x.com/a/status/123", "login_key": "eve@example.com"},
    )
    assert forbidden.status_code == 422
    nested = client.post(
        "/api/x/requests",
        json={
            "url": "https://x.com/a/status/123",
            "alias": {"login_key": "eve@example.com"},
        },
    )
    assert nested.status_code == 422


def test_unknown_alias_tag_maps_to_422() -> None:
    from kronika.application.ports.media_user_alias_repository import (
        AliasTagNotFoundError,
    )

    class _UnknownTagService:
        def submit(self, url: str, login_key: str, alias=None, content_category=None):
            raise AliasTagNotFoundError()

    client = TestClient(_app(_UnknownTagService()))
    response = client.post(
        "/api/x/requests",
        json={
            "url": "https://x.com/a/status/123",
            "alias": {"tag_keys": ["missing"]},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ALIAS_TAG_NOT_FOUND"


def test_omitted_category_is_valid_for_old_clients() -> None:
    captured: dict[str, object] = {}

    def submit(url, login_key, alias=None, content_category=None):
        captured["content_category"] = content_category
        return types.SimpleNamespace(request_id=_VALID_ID, submission_result="new")

    service = _service_fake()
    service.submit = submit
    client = TestClient(_app(service))
    response = client.post("/api/x/requests", json={"url": "https://x.com/a/status/123"})
    assert response.status_code == 200
    assert captured["content_category"] is None


@pytest.mark.parametrize("value", ["general", "meme", "movie", "youtube"])
def test_valid_category_is_parsed(value: str) -> None:
    captured: dict[str, object] = {}

    def submit(url, login_key, alias=None, content_category=None):
        captured["content_category"] = content_category
        return types.SimpleNamespace(request_id=_VALID_ID, submission_result="new")

    service = _service_fake()
    service.submit = submit
    client = TestClient(_app(service))
    response = client.post(
        "/api/x/requests",
        json={"url": "https://x.com/a/status/123", "content_category": value},
    )
    assert response.status_code == 200
    assert captured["content_category"].value == value


def test_invalid_category_maps_to_sanitized_422() -> None:
    client = TestClient(_app(_service_fake()))
    for hostile in ["tiktok", "MEME", "", "general ", None]:
        if hostile is None:
            continue
        response = client.post(
            "/api/x/requests",
            json={"url": "https://x.com/a/status/123", "content_category": hostile},
        )
        assert response.status_code == 422, hostile
        body = response.json()
        assert body["error"]["code"] == "X_REQUEST_INVALID_CATEGORY", hostile


def test_category_conflict_maps_to_sanitized_409() -> None:
    """The API substitutes its own sentence, so the fake must not supply one.

    The router catches the conflict by class and builds the response message
    itself. The fake therefore raises a deliberately different text: if the
    response echoed the exception, this assertion would fail, which is what makes
    it a guard on the API's own literal rather than on fixture data.
    """
    from kronika.application.x_acquisition import XAcquisitionCategoryConflictError

    class _ConflictService:
        def submit(self, url: str, login_key: str, alias=None, content_category=None):
            raise XAcquisitionCategoryConflictError("an exception text the API discards")

    client = TestClient(_app(_ConflictService()))
    response = client.post(
        "/api/x/requests",
        json={"url": "https://x.com/a/status/123", "content_category": "movie"},
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == "X_REQUEST_CATEGORY_CONFLICT"
    assert payload["error"]["message"] == expected(
        "Requested category conflicts with the existing {brand} save."
    )


def test_invalid_alias_maps_to_the_derived_brand_422() -> None:
    from kronika.domain.media_user_alias import FrameNestMediaUserAliasError

    class _InvalidAliasService:
        def submit(self, url: str, login_key: str, alias=None, content_category=None):
            raise FrameNestMediaUserAliasError("an exception text the API discards")

    client = TestClient(_app(_InvalidAliasService()))
    response = client.post(
        "/api/x/requests",
        json={
            "url": "https://x.com/a/status/123",
            "alias": {"display_title": " Title"},
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "ALIAS_INVALID"
    assert payload["error"]["message"] == expected(
        "Invalid {brand} media user alias."
    )


def test_extra_fields_remain_forbidden() -> None:
    client = TestClient(_app(_service_fake()))
    response = client.post(
        "/api/x/requests",
        json={
            "url": "https://x.com/a/status/123",
            "content_category": "meme",
            "unexpected": True,
        },
    )
    assert response.status_code == 422


def test_list_route_invalid_request_discriminators_return_static_literals() -> None:
    class _CursorService:
        @staticmethod
        def list_owned(login_key: str, limit: int, cursor: str | None):
            raise XAcquisitionInvalidCursorError("Invalid X request cursor.")

    class _IdentityService:
        @staticmethod
        def list_owned(login_key: str, limit: int, cursor: str | None):
            raise XAcquisitionInvalidRequesterIdentityError(
                "Invalid requester identity."
            )

    hostile_cursor = "!!!not-a-cursor"
    for service, message in (
        (_CursorService, "Invalid X request cursor."),
        (_IdentityService, "Invalid requester identity."),
    ):
        client = TestClient(_app(service()))
        response = client.get("/api/x/requests", params={"cursor": hostile_cursor})
        assert response.status_code == 422
        assert response.json() == {
            "error": {"code": "X_REQUEST_INVALID_URL", "message": message}
        }
        assert hostile_cursor not in response.text


def test_retry_route_state_conflict_discriminators_return_static_literals() -> None:
    class _NotRetryableService:
        @staticmethod
        def retry(claim_id: str, login_key: str):
            raise XAcquisitionNotRetryableError(
                "X claim is not retryable in its current state."
            )

    class _NoRetryableAssetsService:
        @staticmethod
        def retry(claim_id: str, login_key: str):
            raise XAcquisitionNoRetryableAssetsError(
                "X claim has no retryable assets."
            )

    for service, message in (
        (_NotRetryableService, "X claim is not retryable in its current state."),
        (_NoRetryableAssetsService, "X claim has no retryable assets."),
    ):
        client = TestClient(_app(service()))
        response = client.post(f"/api/x/requests/{_VALID_ID}/retry")
        assert response.status_code == 409
        assert response.json() == {
            "error": {"code": "X_REQUEST_STATE_CONFLICT", "message": message}
        }
