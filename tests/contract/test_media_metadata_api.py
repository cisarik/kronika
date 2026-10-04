"""Contract tests for the media metadata API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from kronika.adapters.api.application import create_app
from kronika.adapters.api.media_metadata_api import MediaMetadataApiDependencies
from kronika.application.media_metadata import MediaMetadataView, SaveMediaMetadataResult
from kronika.application.ports.media_metadata_repository import (
    CanonicalTagCreateResult,
    CanonicalTagDefinitionConflictError,
    CanonicalTagNotFoundError,
    FrameNestMediaMetadataRepositoryError,
    MediaMetadataMediaNotFoundError,
    SourceDerivedMetadataImmutableError,
)
from kronika.configuration import KronikaSettings
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
)

MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
PRIVATE_DATABASE_PATH = "/Users/example/private/catalog.sqlite3"
UNDERLYING_EXCEPTION_TEXT = "sqlite failed near private table"


def _tag(key: str, display_name: str) -> CanonicalTag:
    return CanonicalTag(
        key=CanonicalTagKey(key),
        display_name=CanonicalTagDisplayName(display_name),
        created_at_ms=10,
        updated_at_ms=10,
    )


@dataclass
class _FakeCreateTag:
    status: str = "created"
    error: Exception | None = None

    def execute(self, key: str, display_name: str) -> object:
        if self.error is not None:
            raise self.error
        return CanonicalTagCreateResult(status=self.status, tag=_tag(key, display_name))


@dataclass
class _FakeListTags:
    error: Exception | None = None

    def execute(self) -> object:
        if self.error is not None:
            raise self.error
        return type("TagList", (), {"tags": (_tag("compression", "Compression"), _tag("mathematics", "Math"))})()


@dataclass
class _FakeEnsureCompanionXTag:
    error: Exception | None = None
    calls: int = 0

    def execute(self) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return CanonicalTagCreateResult(status="created", tag=_tag("x", "\N{MATHEMATICAL DOUBLE-STRUCK CAPITAL X}"))


@dataclass
class _FakeGetMetadata:
    persisted: bool = False
    error: Exception | None = None

    def execute(self, media_id: str) -> object:
        if self.error is not None:
            raise self.error
        return MediaMetadataView(
            persisted=self.persisted,
            display_title="Reinventing Entropy" if self.persisted else None,
            description="A plain text description." if self.persisted else None,
            tags=(_tag("mathematics", "Math"),) if self.persisted else (),
            collection_key="processed" if self.persisted else None,
            processed_at_ms=15 if self.persisted else None,
            created_at_ms=10 if self.persisted else None,
            updated_at_ms=20 if self.persisted else None,
        )


@dataclass
class _FakeSaveMetadata:
    status: str = "created"
    error: Exception | None = None
    last_display_title: str | None = None
    last_description: str | None = None
    last_tag_keys: list[str] | None = None

    def execute(
        self,
        media_id: str,
        display_title: str | None,
        description: str | None,
        tag_keys: list[str],
        *,
        content_category: str | None | object = "general",
        acquisition_source: str | None = None,
        genres: list[str] | None = None,
        creator_attribution_kind: str | None | object = None,
        creator_stable_id: str | None | object = None,
        creator_handle: str | None | object = None,
        creator_display_name: str | None | object = None,
    ) -> object:
        from kronika.application.ports.media_metadata_repository import OMITTED

        self.last_display_title = display_title
        self.last_description = description
        self.last_tag_keys = tag_keys
        if self.error is not None:
            raise self.error
        has_tags = len(tag_keys) > 0
        if content_category is OMITTED:
            content_category = "general"
        creator_attribution_kind = None if creator_attribution_kind is OMITTED else creator_attribution_kind
        creator_stable_id = None if creator_stable_id is OMITTED else creator_stable_id
        creator_handle = None if creator_handle is OMITTED else creator_handle
        creator_display_name = None if creator_display_name is OMITTED else creator_display_name
        metadata = MediaMetadataView(
            persisted=True,
            display_title=display_title,
            description=description,
            tags=tuple(_tag(key, key) for key in tag_keys),
            collection_key="processed" if has_tags else None,
            processed_at_ms=10 if has_tags else None,
            created_at_ms=10,
            updated_at_ms=10,
            content_category=content_category,
            acquisition_source=acquisition_source or "unknown",
            genres=tuple(genres or ()),
            creator_attribution_kind=creator_attribution_kind,
            creator_stable_id=creator_stable_id,
            creator_handle=creator_handle,
            creator_display_name=creator_display_name,
        )
        return SaveMediaMetadataResult(status=self.status, metadata=metadata)


def _client(
    *,
    catalog_available: bool = True,
    create_tag: _FakeCreateTag | None = None,
    list_tags: _FakeListTags | None = None,
    get_metadata: _FakeGetMetadata | None = None,
    save_metadata: _FakeSaveMetadata | None = None,
    ensure_companion_x_tag: _FakeEnsureCompanionXTag | None = None,
    database_path: Path | None = None,
) -> TestClient:
    settings = KronikaSettings(
        database_path=database_path or Path("/tmp/framenest-media-metadata-api.sqlite3"),
        _env_file=None,
    )
    from tests.support.record_access import install_synthetic_caller, scoped_policy

    app = create_app(
        settings=settings,
        media_metadata_api_dependencies=MediaMetadataApiDependencies(
            create_tag=create_tag or _FakeCreateTag(),
            list_tags=list_tags or _FakeListTags(),
            get_metadata=get_metadata or _FakeGetMetadata(),
            save_metadata=save_metadata or _FakeSaveMetadata(),
            catalog_available=lambda: catalog_available,
            ensure_companion_x_tag=ensure_companion_x_tag,
            audience_policy=scoped_policy({MEDIA_ID}),
        ),
    )
    return TestClient(install_synthetic_caller(app))


def test_tag_creation_created_and_already_exists_statuses() -> None:
    created = _client(create_tag=_FakeCreateTag(status="created")).post(
        "/api/canonical-tags",
        json={"key": "mathematics", "display_name": "Math"},
    )
    repeated = _client(create_tag=_FakeCreateTag(status="already_exists")).post(
        "/api/canonical-tags",
        json={"key": "mathematics", "display_name": "Math"},
    )

    assert created.status_code == 201
    assert repeated.status_code == 200
    assert created.json()["status"] == "created"
    assert repeated.json()["status"] == "already_exists"


def test_tag_conflict_and_list_order() -> None:
    conflict = _client(
        create_tag=_FakeCreateTag(error=CanonicalTagDefinitionConflictError("private"))
    ).post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Mathematics"})
    listed = _client().get("/api/canonical-tags")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CANONICAL_TAG_DEFINITION_CONFLICT"
    assert listed.json()["tags"] == [
        {"key": "compression", "display_name": "Compression"},
        {"key": "mathematics", "display_name": "Math"},
    ]


def test_metadata_get_unsaved_saved_and_save_statuses() -> None:
    unsaved = _client(get_metadata=_FakeGetMetadata(persisted=False)).get(
        f"/api/media/{MEDIA_ID}/metadata"
    )
    saved = _client(get_metadata=_FakeGetMetadata(persisted=True)).get(
        f"/api/media/{MEDIA_ID}/metadata"
    )
    updater = _FakeSaveMetadata(status="unchanged")
    saved_put = _client(save_metadata=updater).put(
        f"/api/media/{MEDIA_ID}/metadata",
        json={"display_title": "Reinventing Entropy", "description": "A plain text description.", "tag_keys": ["mathematics"]},
    )

    assert unsaved.status_code == 200
    assert unsaved.json() == {
        "persisted": False,
        "display_title": None,
        "description": None,
        "tags": [],
        "collection_key": None,
        "processed_at_ms": None,
        "created_at_ms": None,
        "updated_at_ms": None,
        "content_category": "general",
        "acquisition_source": "unknown",
        "genres": [],
        "creator_attribution_kind": None,
        "creator_stable_id": None,
        "creator_handle": None,
        "creator_display_name": None,
    }
    assert saved.json()["tags"] == [{"key": "mathematics", "display_name": "Math"}]
    assert saved.json()["description"] == "A plain text description."
    assert saved_put.status_code == 200
    assert saved_put.json()["status"] == "unchanged"
    assert updater.last_display_title == "Reinventing Entropy"
    assert updater.last_description == "A plain text description."
    assert updater.last_tag_keys == ["mathematics"]


def test_title_clear_missing_media_missing_tag_and_missing_catalog(tmp_path: Path) -> None:
    clearer = _FakeSaveMetadata(status="updated")
    clear = _client(save_metadata=clearer).put(
        f"/api/media/{MEDIA_ID}/metadata",
        json={"display_title": None, "description": None, "tag_keys": []},
    )
    missing_media = _client(get_metadata=_FakeGetMetadata(error=MediaMetadataMediaNotFoundError())).get(
        f"/api/media/{MEDIA_ID}/metadata"
    )
    missing_tag = _client(save_metadata=_FakeSaveMetadata(error=CanonicalTagNotFoundError())).put(
        f"/api/media/{MEDIA_ID}/metadata",
        json={"display_title": None, "description": None, "tag_keys": ["missing"]},
    )
    database_path = tmp_path / "missing" / "catalog.sqlite3"
    missing_catalog = _client(catalog_available=False, database_path=database_path).get(
        f"/api/media/{MEDIA_ID}/metadata"
    )

    assert clear.status_code == 200
    assert clearer.last_display_title is None
    assert missing_media.status_code == 404
    assert missing_media.json()["error"]["code"] == "MEDIA_NOT_FOUND"
    assert missing_tag.status_code == 409
    assert missing_tag.json()["error"]["code"] == "CANONICAL_TAG_NOT_FOUND"
    assert missing_catalog.status_code == 503
    assert missing_catalog.json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert not database_path.exists()
    assert not database_path.parent.exists()


@pytest.mark.parametrize(
    "body",
    [
        {"key": "Bad", "display_name": "Math"},
        {"key": "mathematics", "display_name": ""},
        {"display_title": "", "description": None, "tag_keys": []},
        {"display_title": " Title", "description": None, "tag_keys": []},
        {"display_title": None, "description": None, "tag_keys": ["mathematics", "mathematics"]},
        {"display_title": None, "description": None, "tag_keys": [f"tag-{index}" for index in range(33)]},
        {"display_title": None, "description": " Leading space", "tag_keys": []},
        {"display_title": None, "description": "\x00null byte", "tag_keys": []},
        {"display_title": None, "description": "\U0001f3ac" * 10_001, "tag_keys": []},
        {"display_title": None, "description": "bad\u0085c1", "tag_keys": []},
    ],
)
def test_malformed_requests_return_422(body: dict[str, object]) -> None:
    client = _client()
    if "key" in body:
        response = client.post("/api/canonical-tags", json=body)
    else:
        response = client.put(f"/api/media/{MEDIA_ID}/metadata", json=body)
    assert response.status_code == 422


def test_source_derived_immutability_is_sanitized_409() -> None:
    immutable = _client(
        save_metadata=_FakeSaveMetadata(error=SourceDerivedMetadataImmutableError(UNDERLYING_EXCEPTION_TEXT))
    ).put(f"/api/media/{MEDIA_ID}/metadata", json={"display_title": None, "description": None, "tag_keys": []})

    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "SOURCE_DERIVED_IMMUTABLE"
    assert UNDERLYING_EXCEPTION_TEXT not in immutable.text


def test_unexpected_failures_are_sanitized() -> None:
    tag_failure = _client(
        create_tag=_FakeCreateTag(error=RuntimeError(UNDERLYING_EXCEPTION_TEXT))
    ).post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Math"})
    metadata_failure = _client(
        save_metadata=_FakeSaveMetadata(error=FrameNestMediaMetadataRepositoryError(UNDERLYING_EXCEPTION_TEXT))
    ).put(f"/api/media/{MEDIA_ID}/metadata", json={"display_title": None, "description": None, "tag_keys": []})

    assert tag_failure.status_code == 500
    assert tag_failure.json()["error"]["code"] == "CANONICAL_TAG_OPERATION_FAILED"
    assert metadata_failure.status_code == 500
    assert metadata_failure.json()["error"]["code"] == "MEDIA_METADATA_OPERATION_FAILED"
    for response in (tag_failure, metadata_failure):
        assert UNDERLYING_EXCEPTION_TEXT not in response.text
        assert PRIVATE_DATABASE_PATH not in response.text


def test_bare_canonical_tag_list_does_not_seed() -> None:
    ensure = _FakeEnsureCompanionXTag()
    listed = _client(ensure_companion_x_tag=ensure).get("/api/canonical-tags")

    assert listed.status_code == 200
    assert listed.json() == {
        "tags": [
            {"key": "compression", "display_name": "Compression"},
            {"key": "mathematics", "display_name": "Math"},
        ]
    }
    assert ensure.calls == 0


def test_companion_save_surface_seeds_then_lists_unchanged_shape() -> None:
    ensure = _FakeEnsureCompanionXTag()
    listed = _client(ensure_companion_x_tag=ensure).get(
        "/api/canonical-tags",
        params={"surface": "x-companion-save"},
    )

    assert listed.status_code == 200
    assert listed.json() == {
        "tags": [
            {"key": "compression", "display_name": "Compression"},
            {"key": "mathematics", "display_name": "Math"},
        ]
    }
    assert ensure.calls == 1


def test_unknown_canonical_tag_list_surface_returns_422() -> None:
    ensure = _FakeEnsureCompanionXTag()
    response = _client(ensure_companion_x_tag=ensure).get(
        "/api/canonical-tags",
        params={"surface": "youtube-companion-save"},
    )

    assert response.status_code == 422
    assert ensure.calls == 0


def test_companion_save_surface_seed_failure_still_lists() -> None:
    ensure = _FakeEnsureCompanionXTag(error=CanonicalTagDefinitionConflictError("conflict"))
    listed = _client(ensure_companion_x_tag=ensure).get(
        "/api/canonical-tags",
        params={"surface": "x-companion-save"},
    )

    assert listed.status_code == 200
    assert listed.json()["tags"] == [
        {"key": "compression", "display_name": "Compression"},
        {"key": "mathematics", "display_name": "Math"},
    ]
    assert ensure.calls == 1


def test_companion_save_surface_preserves_list_failure() -> None:
    ensure = _FakeEnsureCompanionXTag()
    listed = _client(
        ensure_companion_x_tag=ensure,
        list_tags=_FakeListTags(error=RuntimeError(UNDERLYING_EXCEPTION_TEXT)),
    ).get("/api/canonical-tags", params={"surface": "x-companion-save"})

    assert listed.status_code == 500
    assert listed.json()["error"]["code"] == "CANONICAL_TAG_OPERATION_FAILED"
    assert ensure.calls == 1
    assert UNDERLYING_EXCEPTION_TEXT not in listed.text


def test_canonical_tag_create_post_is_unchanged_and_does_not_seed() -> None:
    ensure = _FakeEnsureCompanionXTag()
    created = _client(
        create_tag=_FakeCreateTag(status="created"),
        ensure_companion_x_tag=ensure,
    ).post("/api/canonical-tags", json={"key": "mathematics", "display_name": "Math"})

    assert created.status_code == 201
    assert created.json()["status"] == "created"
    assert created.json()["tag"] == {"key": "mathematics", "display_name": "Math"}
    assert ensure.calls == 0
