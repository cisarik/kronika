"""API contract for capability-gated single-item content publication."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.content_publication_api import (
    ContentPublicationApiDependencies,
    create_content_publication_api_router,
)
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.ports.content_publication_repository import (
    AdminMediaItem,
    AdminMediaPage,
    ContentPublicationMediaNotFoundError,
    PublishContentResult,
)
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaLocation,
    CatalogMediaTag,
)
from kronika.domain.content_publication import (
    ContentPublication,
    ContentPublicationOrigin,
    derive_content_publication_readiness,
)
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
LOCATION_ID = "22222222-2222-4222-8222-222222222222"
LIBRARY_ID = "33333333-3333-4333-8333-333333333333"


def _identity(role: str) -> IdentityContext:
    return IdentityContext(
        login=f"{role}@example.com",
        login_key=f"{role}@example.com",
        display_name=role.title(),
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


@dataclass
class _FakeList:
    calls: list[dict[str, object]]

    def execute(self, **kwargs: object) -> AdminMediaPage:
        self.calls.append(kwargs)
        readiness = derive_content_publication_readiness(
            display_title="Manual title",
            description="Manual description",
            canonical_tag_count=1,
        )
        return AdminMediaPage(
            items=(
                AdminMediaItem(
                    media_id=MEDIA_ID,
                    media_kind="video",
                    created_at_ms=10,
                    updated_at_ms=11,
                    display_title="Manual title",
                    description="Manual description",
                    collection_key=None,
                    processed_at_ms=None,
                    content_category="general",
                    acquisition_source="unknown",
                    tags=(
                        CatalogMediaTag(
                            key="manual",
                            display_name="Manual",
                            position=0,
                        ),
                    ),
                    locations=(
                        CatalogMediaLocation(
                            location_id=LOCATION_ID,
                            library_id=LIBRARY_ID,
                            relative_path="safe/item.mp4",
                            availability="available",
                            observed_size_bytes=10,
                            observed_mtime_ns=20,
                        ),
                    ),
                    publication=None,
                    readiness=readiness,
                    analysis_state="not_requested",
                ),
            ),
            total=1,
            limit=int(kwargs["limit"]),
            offset=int(kwargs["offset"]),
            q=kwargs["q"],  # type: ignore[arg-type]
            tag_keys=tuple(kwargs["tag_keys"]),  # type: ignore[arg-type]
            publication=kwargs["publication"],  # type: ignore[arg-type]
            readiness=kwargs["readiness"],  # type: ignore[arg-type]
            analysis=kwargs["analysis"],  # type: ignore[arg-type]
        )


@dataclass
class _FakePublish:
    result: PublishContentResult | Exception
    calls: list[tuple[str, bool]]

    def execute(self, media_id: str, *, published: bool = True) -> PublishContentResult:
        self.calls.append((media_id, published))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _client(
    *,
    identity: IdentityContext | None,
    audit_proof: bool = False,
    publication_result: PublishContentResult | Exception | None = None,
) -> tuple[TestClient, _FakeList, _FakePublish]:
    readiness = derive_content_publication_readiness(
        display_title="Manual title",
        description="Manual description",
        canonical_tag_count=1,
    )
    result = publication_result or PublishContentResult(
        status="published",
        publication=ContentPublication(
            media_id=MEDIA_ID,
            published_at_ms=100,
            publication_origin=ContentPublicationOrigin.ADMIN_EXPLICIT,
        ),
        readiness=readiness,
    )
    list_service = _FakeList(calls=[])
    publish_service = _FakePublish(result=result, calls=[])
    app = FastAPI()

    @app.middleware("http")
    async def identity_scope(request: Request, call_next):
        if identity is not None:
            request.scope[SCOPE_IDENTITY] = identity
        if audit_proof:
            request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-event"
        return await call_next(request)

    app.include_router(
        create_content_publication_api_router(
            ContentPublicationApiDependencies(
                list_admin_media=list_service,
                publish_content=publish_service,
                catalog_available=lambda: True,
            )
        )
    )
    return TestClient(app), list_service, publish_service


def test_admin_list_requires_verified_workflow_capability_and_defaults_unpublished() -> None:
    missing, _, _ = _client(identity=None)
    ordinary, _, _ = _client(identity=_identity(ROLE_USER))
    admin, service, _ = _client(identity=_identity(ROLE_ADMIN))

    assert missing.get("/api/admin/media").status_code == 401
    assert ordinary.get("/api/admin/media").status_code == 403
    response = admin.get("/api/admin/media")

    assert response.status_code == 200
    assert service.calls[0]["publication"] == "unpublished"
    assert service.calls[0]["contributor"] is None
    assert response.json()["contributor"] is None
    assert response.json()["items"][0]["contributors"] == []
    assert response.json()["items"][0]["publication_ready"] is True
    assert response.json()["items"][0]["analysis_state"] == "not_requested"
    assert "relative_path" not in response.json()["items"][0]["locations"][0]
    assert response.json()["has_previous"] is False
    assert response.json()["has_next"] is False


def test_admin_list_forwards_contributor_filter_without_changing_defaults() -> None:
    admin, service, _ = _client(identity=_identity(ROLE_ADMIN))
    response = admin.get(
        "/api/admin/media",
        params={"contributor": "Alice@Example.COM"},
    )
    assert response.status_code == 200
    assert service.calls[0]["contributor"] == "Alice@Example.COM"
    assert service.calls[0]["publication"] == "unpublished"


def test_publication_requires_audit_before_mutation_and_is_idempotent() -> None:
    no_audit, _, blocked_service = _client(identity=_identity(ROLE_ADMIN))
    blocked = no_audit.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication"
    )
    assert blocked.status_code == 500
    assert blocked.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
    assert blocked_service.calls == []

    first, _, first_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
    )
    created = first.put(f"/api/admin/media/{MEDIA_ID}/content-publication")
    assert created.status_code == 201
    assert created.json()["status"] == "published"
    assert first_service.calls == [(MEDIA_ID, True)]

    repeated_result = PublishContentResult(
        status="already_published",
        publication=ContentPublication(
            media_id=MEDIA_ID,
            published_at_ms=100,
            publication_origin=ContentPublicationOrigin.ADMIN_EXPLICIT,
        ),
        readiness=derive_content_publication_readiness(
            display_title=None,
            description=None,
            canonical_tag_count=0,
        ),
    )
    repeated, _, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        publication_result=repeated_result,
    )
    repeat_response = repeated.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication"
    )
    assert repeat_response.status_code == 200
    assert repeat_response.json()["status"] == "already_published"
    assert repeat_response.json()["publication_ready"] is False


def test_not_ready_unknown_and_failures_are_sanitized() -> None:
    not_ready_result = PublishContentResult(
        status="not_ready",
        publication=None,
        readiness=derive_content_publication_readiness(
            display_title=" ",
            description=None,
            canonical_tag_count=0,
        ),
    )
    not_ready, _, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        publication_result=not_ready_result,
    )
    conflict = not_ready.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication"
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["missing_fields"] == [
        "display_title",
        "description",
        "tags",
    ]

    unknown, _, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        publication_result=ContentPublicationMediaNotFoundError("private"),
    )
    missing = unknown.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "MEDIA_NOT_FOUND"
    assert "private" not in missing.text


def test_omitted_and_empty_bodies_remain_publish_preserving() -> None:
    omitted, _, omitted_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
    )
    empty, _, empty_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
    )
    explicit, _, explicit_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
    )

    assert omitted.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication"
    ).status_code == 201
    assert empty.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={},
    ).status_code == 201
    assert explicit.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": True},
    ).status_code == 201
    assert omitted_service.calls == [(MEDIA_ID, True)]
    assert empty_service.calls == [(MEDIA_ID, True)]
    assert explicit_service.calls == [(MEDIA_ID, True)]


def test_unpublish_is_idempotent_and_keeps_the_security_envelope() -> None:
    unpublished_result = PublishContentResult(
        status="unpublished",
        publication=None,
        readiness=derive_content_publication_readiness(
            display_title="Manual title",
            description="Manual description",
            canonical_tag_count=1,
        ),
    )
    already_result = PublishContentResult(
        status="already_unpublished",
        publication=None,
        readiness=derive_content_publication_readiness(
            display_title="Manual title",
            description="Manual description",
            canonical_tag_count=1,
        ),
    )
    no_identity, _, no_identity_service = _client(identity=None, audit_proof=True)
    ordinary, _, ordinary_service = _client(
        identity=_identity(ROLE_USER),
        audit_proof=True,
    )
    no_audit, _, blocked_service = _client(identity=_identity(ROLE_ADMIN))
    unpublished, _, unpublished_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        publication_result=unpublished_result,
    )
    already, _, already_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        publication_result=already_result,
    )
    invalid, _, invalid_service = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
    )

    missing = no_identity.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": False},
    )
    denied = ordinary.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": False},
    )
    blocked = no_audit.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": False},
    )
    first = unpublished.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": False},
    )
    repeat = already.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": False},
    )
    malformed = invalid.put(
        f"/api/admin/media/{MEDIA_ID}/content-publication",
        json={"published": "no"},
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "IDENTITY_REQUIRED"
    assert no_identity_service.calls == []
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert ordinary_service.calls == []
    assert blocked.status_code == 500
    assert blocked.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
    assert blocked.headers["cache-control"] == "no-store"
    assert blocked_service.calls == []
    assert first.status_code == 200
    assert first.json()["status"] == "unpublished"
    assert first.json()["publication"] is None
    assert first.json()["publication_ready"] is True
    assert first.headers["cache-control"] == "no-store"
    assert unpublished_service.calls == [(MEDIA_ID, False)]
    assert repeat.status_code == 200
    assert repeat.json()["status"] == "already_unpublished"
    assert repeat.json()["publication"] is None
    assert already_service.calls == [(MEDIA_ID, False)]
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "INVALID_CONTENT_PUBLICATION"
    assert malformed.json()["error"]["message"] == "Invalid content publication request."
    assert "no" not in malformed.json()["error"]["message"]
    assert invalid_service.calls == []
