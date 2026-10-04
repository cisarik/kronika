"""Contract tests for caller-private media alias overlay API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.kronika_identity import expected

from kronika.adapters.api.media_alias_api import (
    MediaAliasApiDependencies,
    create_media_alias_api_router,
)
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.media_user_alias import EMPTY_ALIAS_VIEW, MediaUserAliasView
from kronika.application.ports.media_user_alias_repository import (
    AliasTagNotFoundError,
    MediaUserAliasMediaNotFoundError,
)
from kronika.domain.identity_access import (
    CAPABILITY_GALLERY_READ,
    CAPABILITY_METADATA_ALIAS_WRITE,
    IdentityContext,
)
from kronika.domain.media_user_alias import FrameNestMediaUserAliasError

MEDIA_ID = "12345678-1234-4234-9234-123456789abc"


def _identity(login: str = "alice@example.com") -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login,
        role="user",
        capabilities=frozenset(
            {CAPABILITY_GALLERY_READ, CAPABILITY_METADATA_ALIAS_WRITE}
        ),
        provenance="tailscale-serve",
    )


@dataclass
class _FakeGet:
    view: MediaUserAliasView = EMPTY_ALIAS_VIEW
    error: Exception | None = None

    def execute(self, media_id: str, login_key: str) -> MediaUserAliasView:
        if self.error is not None:
            raise self.error
        return self.view


@dataclass
class _FakeSave:
    view: MediaUserAliasView = EMPTY_ALIAS_VIEW
    error: Exception | None = None
    calls: list[tuple[object, ...]] | None = None

    def execute(
        self,
        media_id: str,
        login_key: str,
        display_title: str | None,
        description: str | None,
        tag_keys: list[str] | None,
    ) -> MediaUserAliasView:
        if self.calls is not None:
            self.calls.append(
                (media_id, login_key, display_title, description, tag_keys)
            )
        if self.error is not None:
            raise self.error
        return self.view


def _app(get_alias=None, save_alias=None) -> FastAPI:
    router = create_media_alias_api_router(
        MediaAliasApiDependencies(
            get_alias=get_alias or _FakeGet(),
            save_alias=save_alias or _FakeSave(),
            catalog_available=lambda: True,
            audience_policy=__import__(
                "tests.support.record_access", fromlist=["scoped_policy"]
            ).scoped_policy({MEDIA_ID}),
        )
    )
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_identity(request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity()
        return await call_next(request)

    return app


def test_get_alias_returns_empty_object_when_absent() -> None:
    client = TestClient(_app())
    response = client.get(f"/api/media/{MEDIA_ID}/alias")
    assert response.status_code == 200
    assert response.json() == {
        "display_title": None,
        "description": None,
        "tag_keys": [],
    }


def test_put_alias_persists_and_empty_deletes() -> None:
    calls: list[tuple[object, ...]] = []
    persisted = MediaUserAliasView(
        display_title="Alias",
        description=None,
        tag_keys=("meme",),
        persisted=True,
        created_at_ms=1,
        updated_at_ms=1,
    )
    client = TestClient(_app(save_alias=_FakeSave(view=persisted, calls=calls)))
    response = client.put(
        f"/api/media/{MEDIA_ID}/alias",
        json={"display_title": "Alias", "tag_keys": ["meme"]},
    )
    assert response.status_code == 200
    assert response.json()["display_title"] == "Alias"
    assert calls[0][1] == "alice@example.com"
    empty_client = TestClient(_app(save_alias=_FakeSave(calls=calls)))
    empty = empty_client.put(f"/api/media/{MEDIA_ID}/alias", json={})
    assert empty.status_code == 200
    assert empty.json() == {
        "display_title": None,
        "description": None,
        "tag_keys": [],
    }


def test_put_alias_maps_unknown_tag_and_invalid_content() -> None:
    missing = TestClient(
        _app(save_alias=_FakeSave(error=AliasTagNotFoundError()))
    ).put(f"/api/media/{MEDIA_ID}/alias", json={"tag_keys": ["missing"]})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "ALIAS_TAG_NOT_FOUND"
    invalid = TestClient(
        _app(save_alias=_FakeSave(error=FrameNestMediaUserAliasError("invalid")))
    ).put(f"/api/media/{MEDIA_ID}/alias", json={"display_title": " Title"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "ALIAS_INVALID"
    assert invalid.json()["error"]["message"] == expected(
        "Invalid {brand} media user alias."
    )


def test_alias_audience_denial_is_not_found() -> None:
    client = TestClient(
        _app(get_alias=_FakeGet(error=MediaUserAliasMediaNotFoundError()))
    )
    response = client.get(f"/api/media/{MEDIA_ID}/alias")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MEDIA_NOT_FOUND"


def test_put_alias_forbids_login_key_in_body() -> None:
    client = TestClient(_app())
    response = client.put(
        f"/api/media/{MEDIA_ID}/alias",
        json={"display_title": "Alias", "login_key": "eve@example.com"},
    )
    assert response.status_code == 422
