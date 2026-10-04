"""Contract tests for the searchable media catalog API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.media_catalog_api import (
    MediaCatalogApiDependencies,
    create_media_catalog_api_router,
)
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.media_catalog import ListMediaCatalog
from kronika.application.media_user_alias import (
    CallerAliasOverlayPage,
    MediaUserAliasView,
)
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaItem,
    CatalogMediaLocation,
    CatalogMediaTag,
    FrameNestMediaCatalogRepositoryError,
    MediaCatalogPage,
    MediaCatalogQuery,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import (
    CAPABILITY_GALLERY_READ,
    CAPABILITY_METADATA_ALIAS_WRITE,
    IdentityContext,
)
from kronika.domain.media_metadata import MediaCollectionKey

MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
LOCATION_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
LIBRARY_ID = "11111111-2222-4333-8444-555555555555"
PRIVATE_ROOT_MARKER = "private-root-marker"
UNDERLYING_EXCEPTION_TEXT = "sqlite failed beside private table"


@dataclass
class _FakeListMediaCatalog:
    error: Exception | None = None
    queries: list[dict[str, object]] | None = None

    def execute(
        self,
        *,
        q: str | None,
        tag_keys: list[str],
        limit: int,
        offset: int,
        collection_key: MediaCollectionKey | None = None,
        content_category: str | None = None,
        acquisition_source: str | None = None,
        creator_attribution_kind: str | None = None,
        creator_stable_id: str | None = None,
        creator_handle: str | None = None,
        access_scope: object | None = None,
    ) -> MediaCatalogPage:
        if self.queries is None:
            self.queries = []
        self.queries.append(
            {
                "q": q,
                "tag_keys": tag_keys,
                "limit": limit,
                "offset": offset,
                "collection_key": collection_key,
                "content_category": content_category,
                "acquisition_source": acquisition_source,
                "creator_attribution_kind": creator_attribution_kind,
                "creator_stable_id": creator_stable_id,
                "creator_handle": creator_handle,
            }
        )
        if self.error is not None:
            raise self.error
        from kronika.domain.record_access import RecordAccessScope

        return ListMediaCatalog(_FakeCatalogRepository()).execute(
            q=q,
            tag_keys=tag_keys,
            limit=limit,
            offset=offset,
            collection_key=collection_key,
            content_category=content_category,
            acquisition_source=acquisition_source,
            creator_attribution_kind=creator_attribution_kind,
            creator_stable_id=creator_stable_id,
            creator_handle=creator_handle,
            access_scope=access_scope
            if access_scope is not None
            else RecordAccessScope.legacy_public(),
        )


class _FakeCatalogRepository:
    def list_media(self, query: MediaCatalogQuery) -> MediaCatalogPage:
        return MediaCatalogPage(
            items=(
                CatalogMediaItem(
                    media_id=MEDIA_ID,
                    media_kind="video",
                    created_at_ms=10,
                    updated_at_ms=20,
                    display_title="Reinventing Entropy",
                    collection_key=None,
                    processed_at_ms=None,
                    description="A treatise on entropy",
                    tags=(CatalogMediaTag(key="mathematics", display_name="Math", position=0),),
                    locations=(
                        CatalogMediaLocation(
                            location_id=LOCATION_ID,
                            library_id=LIBRARY_ID,
                            relative_path="clips/reinventing-entropy.mp4",
                            availability="available",
                            observed_size_bytes=123,
                            observed_mtime_ns=456,
                        ),
                    ),
                ),
            ),
            total=1,
            limit=query.limit,
            offset=query.offset,
            q=query.q,
            tag_keys=query.tag_keys,
        )


def _client(
    *,
    catalog_available: bool = True,
    list_media: _FakeListMediaCatalog | None = None,
    database_path: Path | None = None,
) -> TestClient:
    settings = KronikaSettings(
        database_path=database_path or Path("/tmp/framenest-media-catalog-api.sqlite3"),
        _env_file=None,
    )
    from tests.support.record_access import install_synthetic_caller

    app = create_app(
        settings=settings,
        media_catalog_api_dependencies=MediaCatalogApiDependencies(
            list_media=list_media or _FakeListMediaCatalog(),
            catalog_available=lambda: catalog_available,
        ),
    )
    return TestClient(install_synthetic_caller(app, "alice"))


def test_successful_default_listing_exposes_complete_catalog_safe_fields() -> None:
    response = _client().get("/api/media")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "media_id": MEDIA_ID,
                "media_kind": "video",
                "created_at_ms": 10,
                "updated_at_ms": 20,
                "display_title": "Reinventing Entropy",
                "collection_key": None,
                "processed_at_ms": None,
                "description": "A treatise on entropy",
                "tags": [{"key": "mathematics", "display_name": "Math", "position": 0}],
                "locations": [
                    {
                        "location_id": LOCATION_ID,
                        "library_id": LIBRARY_ID,
                        "relative_path": "clips/reinventing-entropy.mp4",
                        "availability": "available",
                        "observed_size_bytes": 123,
                        "observed_mtime_ns": 456,
                    }
                ],
                "content_category": "general",
                "acquisition_source": "unknown",
                "cover_ready": False,
                "creator_attribution_kind": None,
                "creator_stable_id": None,
                "creator_handle": None,
                "creator_display_name": None,
            }
        ],
        "total": 1,
        "limit": 24,
        "offset": 0,
        "q": None,
        "tag_keys": [],
        "content_category": None,
        "acquisition_source": None,
        "creator_attribution_kind": None,
        "creator_stable_id": None,
        "creator_handle": None,
    }
    assert PRIVATE_ROOT_MARKER not in response.text


def test_repeated_tags_title_query_combined_filters_and_pagination_metadata() -> None:
    service = _FakeListMediaCatalog()
    response = _client(list_media=service).get(
        "/api/media",
        params=[
            ("q", " Entropy "),
            ("tag", "mathematics"),
            ("tag", "compression"),
            ("limit", "1"),
            ("offset", "2"),
        ],
    )

    assert response.status_code == 200
    assert response.json()["q"] == "Entropy"
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 2
    assert service.queries == [
        {
            "q": " Entropy ",
            "tag_keys": ["mathematics", "compression"],
            "limit": 1,
            "offset": 2,
            "collection_key": None,
            "content_category": None,
            "acquisition_source": None,
            "creator_attribution_kind": None,
            "creator_stable_id": None,
            "creator_handle": None,
        }
    ]


def test_validation_failures_and_catalog_unavailable_response(tmp_path: Path) -> None:
    bad_limit = _client().get("/api/media?limit=0")
    bad_tag = _client().get("/api/media?tag=Bad")
    bad_query = _client().get("/api/media?q=bad%00query")
    database_path = tmp_path / "missing" / "catalog.sqlite3"
    unavailable = _client(catalog_available=False, database_path=database_path).get("/api/media")

    assert bad_limit.status_code == 422
    assert bad_tag.status_code == 422
    assert bad_query.status_code == 422
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_repository_failures_are_sanitized() -> None:
    response = _client(
        list_media=_FakeListMediaCatalog(
            error=FrameNestMediaCatalogRepositoryError(UNDERLYING_EXCEPTION_TEXT)
        )
    ).get("/api/media")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "MEDIA_CATALOG_QUERY_FAILED"
    assert UNDERLYING_EXCEPTION_TEXT not in response.text
    assert PRIVATE_ROOT_MARKER not in response.text


def _identity(login: str) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login,
        role="user",
        capabilities=frozenset({CAPABILITY_GALLERY_READ, CAPABILITY_METADATA_ALIAS_WRITE}),
        provenance="tailscale-serve",
    )


class _FakeGetMedia:
    def execute(self, media_id: str) -> CatalogMediaItem | None:
        del media_id
        return _FakeCatalogRepository().list_media(
            MediaCatalogQuery(q=None, tag_keys=(), limit=1, offset=0, collection_key=None)
        ).items[0]


class _FakeListAliases:
    def __init__(self, overlays_by_login: dict[str, dict[str, MediaUserAliasView]]) -> None:
        self.overlays_by_login = overlays_by_login
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, login_key: str, media_ids: list[str]) -> CallerAliasOverlayPage:
        self.calls.append((login_key, tuple(media_ids)))
        overlays = {
            media_id: overlay
            for media_id, overlay in self.overlays_by_login.get(login_key, {}).items()
            if media_id in media_ids
        }
        names: dict[str, str] = {}
        for overlay in overlays.values():
            for key in overlay.tag_keys:
                names[key] = "Meme" if key == "meme" else key
        return CallerAliasOverlayPage(overlays=overlays, tag_display_names=names)


def _overlay_client(
    *,
    identity: IdentityContext | None,
    list_aliases: _FakeListAliases | None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_media_catalog_api_router(
            MediaCatalogApiDependencies(
                list_media=_FakeListMediaCatalog(),
                catalog_available=lambda: True,
                get_media=_FakeGetMedia(),
                list_aliases=list_aliases,
                audience_policy=__import__(
                    "tests.support.record_access", fromlist=["scoped_policy"]
                ).scoped_policy({MEDIA_ID}),
            )
        )
    )

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        if identity is not None:
            request.scope[SCOPE_IDENTITY] = identity
        return await call_next(request)

    return TestClient(app)


def test_catalog_merge_applies_caller_overlay_and_isolates_logins() -> None:
    alice_overlay = MediaUserAliasView(
        display_title="Alice title",
        description=None,
        tag_keys=("meme",),
        persisted=True,
        created_at_ms=1,
        updated_at_ms=1,
    )
    bob_overlay = MediaUserAliasView(
        display_title="Bob title",
        description="Bob description",
        tag_keys=(),
        persisted=True,
        created_at_ms=1,
        updated_at_ms=1,
    )
    aliases = _FakeListAliases(
        {
            "alice@example.com": {MEDIA_ID: alice_overlay},
            "bob@example.com": {MEDIA_ID: bob_overlay},
        }
    )
    alice = _overlay_client(identity=_identity("alice@example.com"), list_aliases=aliases)
    bob = _overlay_client(identity=_identity("bob@example.com"), list_aliases=aliases)
    anonymous = _overlay_client(identity=None, list_aliases=aliases)

    alice_list = alice.get("/api/media")
    alice_get = alice.get(f"/api/media/{MEDIA_ID}")
    bob_list = bob.get("/api/media")
    bob_get = bob.get(f"/api/media/{MEDIA_ID}")
    public_list = anonymous.get("/api/media")
    public_get = anonymous.get(f"/api/media/{MEDIA_ID}")

    assert alice_list.status_code == 200
    assert alice_list.json()["items"][0]["display_title"] == "Alice title"
    assert alice_list.json()["items"][0]["description"] == "A treatise on entropy"
    assert alice_list.json()["items"][0]["tags"] == [
        {"key": "meme", "display_name": "Meme", "position": 0}
    ]
    assert alice_get.json()["display_title"] == "Alice title"
    assert "Alice title" not in bob_list.text
    assert bob_list.json()["items"][0]["display_title"] == "Bob title"
    assert bob_list.json()["items"][0]["description"] == "Bob description"
    assert bob_list.json()["items"][0]["tags"] == [
        {"key": "mathematics", "display_name": "Math", "position": 0}
    ]
    assert bob_get.json()["display_title"] == "Bob title"
    assert public_list.json()["items"] == []
    assert public_get.status_code == 404
    assert aliases.calls == [
        ("alice@example.com", (MEDIA_ID,)),
        ("alice@example.com", (MEDIA_ID,)),
        ("bob@example.com", (MEDIA_ID,)),
        ("bob@example.com", (MEDIA_ID,)),
    ]
