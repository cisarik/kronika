"""API contract for the first durable manual cover workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.cover_api import (
    CoverApiDependencies,
    create_cover_api_router,
)
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.media_cover import (
    CoverConflictError,
    CoverFailedError,
    CoverMediaNotFoundError,
    CoverPreview,
    CoverSourceChangedError,
    CoverSourceUnavailableError,
    CoverState,
    CoverTimestampInvalidError,
    CoverTimeline,
)
from kronika.application.ports.cover_storage import OpenedCoverThumbnail
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
LOCATION_ID = "22222222-2222-4222-8222-222222222222"
DISTANT_ID = "33333333-3333-4333-8333-333333333333"
ETAG = '"versioned-cover-etag"'
FRAME_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _identity(role: str) -> IdentityContext:
    return IdentityContext(
        login=f"{role}@example.com",
        login_key=f"{role}@example.com",
        display_name=role.title(),
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


def _timeline() -> CoverTimeline:
    from kronika.domain.identities import MediaId, MediaLocationId
    from kronika.domain.media import MediaKind

    return CoverTimeline(
        media_id=MediaId.from_string(MEDIA_ID),
        location_id=MediaLocationId.from_string(LOCATION_ID),
        media_kind=MediaKind.VIDEO,
        duration_ms=2000,
        source_version="a" * 64,
    )


def _state(has_cover: bool = True) -> CoverState:
    return CoverState(
        media_id=MEDIA_ID,
        has_cover=has_cover,
        revision=2 if has_cover else None,
        timestamp_ms=750 if has_cover else None,
        artifact_digest="b" * 64 if has_cover else None,
        source_reference=f"location:{LOCATION_ID}" if has_cover else None,
        source_kind="mp4" if has_cover else None,
        accepted_at_ms=100 if has_cover else None,
        thumbnail_state="ready" if has_cover else "none",
        artifact_state="available" if has_cover else "none",
    )


@dataclass
class _DefaultService:
    result: object = None
    error: Exception | None = None

    def timeline(self, media_id, location_id):
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else _timeline()

    def preview(self, media_id, location_id, *, timestamp_ms, expected_source_version):
        if self.error is not None:
            raise self.error
        return CoverPreview(media_type="image/png", payload=FRAME_BYTES)

    def accept(
        self,
        media_id,
        location_id,
        *,
        timestamp_ms,
        expected_revision,
        expected_source_version,
    ):
        if self.error is not None:
            raise self.error
        return self.result

    def admin_state(self, media_id):
        if self.error is not None:
            raise self.error
        return _state()

    def thumbnail_etag(self, media_id):
        if self.error is not None:
            raise self.error
        return ETAG

    def open_thumbnail(self, media_id):
        if self.error is not None:
            raise self.error
        return OpenedCoverThumbnail(
            media_type="image/jpeg",
            byte_size=len(FRAME_BYTES),
            payload=FRAME_BYTES,
            close=lambda: None,
        )


class _AudiencePolicy:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed
        self.calls = 0

    def may_read(self, media_id, identity) -> bool:
        self.calls += 1
        return self._allowed


def _client(
    *,
    identity: IdentityContext | None,
    audit_proof: bool = False,
    service=None,
    audience: bool | None = None,
) -> tuple[TestClient, _DefaultService | _AudiencePolicy]:
    cover_service = service or _DefaultService()
    app = FastAPI()

    @app.middleware("http")
    async def identity_scope(request: Request, call_next):
        if identity is not None:
            request.scope[SCOPE_IDENTITY] = identity
        if audit_proof:
            request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-event"
        return await call_next(request)

    from tests.support.record_access import scoped_policy

    if audience is None:
        audience_policy = scoped_policy({MEDIA_ID}) if identity is not None else None
    else:
        audience_policy = _AudiencePolicy(audience)
    app.include_router(
        create_cover_api_router(
            CoverApiDependencies(
                cover_service=cover_service,
                catalog_available=lambda: True,
                audience_policy=audience_policy,
            )
        )
    )
    return TestClient(app), cover_service if audience is None else audience_policy


TIMELINE_PATH = f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/cover-timeline"
FRAME_PATH = f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/cover-frame"
COVER_PATH = f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/cover"
STATE_PATH = f"/api/admin/media/{MEDIA_ID}/cover"
THUMB_PATH = f"/api/media/{MEDIA_ID}/cover-thumbnail"


def test_timeline_requires_canonical_write_capability() -> None:
    missing, _ = _client(identity=None)
    ordinary, _ = _client(identity=_identity(ROLE_USER))
    admin, service = _client(identity=_identity(ROLE_ADMIN))
    assert missing.get(TIMELINE_PATH).status_code == 401
    assert ordinary.get(TIMELINE_PATH).status_code == 403
    response = admin.get(TIMELINE_PATH)
    assert response.status_code == 200
    assert response.json()["duration_ms"] == 2000
    assert response.json()["source_version"] == "a" * 64
    assert "media_id" in response.json()


def test_timeline_maps_missing_and_unavailable_sources() -> None:
    for error, expected_code in (
        (CoverMediaNotFoundError(), "COVER_MEDIA_NOT_FOUND"),
        (CoverSourceUnavailableError(), "COVER_SOURCE_UNAVAILABLE"),
        (CoverFailedError(), "COVER_FAILED"),
    ):
        client, _ = _client(
            identity=_identity(ROLE_ADMIN),
            service=_DefaultService(error=error),
        )
        response = client.get(TIMELINE_PATH)
        assert response.status_code == (404 if isinstance(error, CoverMediaNotFoundError) else 409 if isinstance(error, CoverSourceUnavailableError) else 500)
        assert response.json()["error"]["code"] == expected_code
        assert "path" not in response.text.lower()


def test_timeline_reports_timeless_image_without_fabricated_duration() -> None:
    from kronika.domain.identities import MediaId, MediaLocationId
    from kronika.domain.media import MediaKind

    image_timeline = CoverTimeline(
        media_id=MediaId.from_string(MEDIA_ID),
        location_id=MediaLocationId.from_string(LOCATION_ID),
        media_kind=MediaKind.IMAGE,
        duration_ms=0,
        source_version="c" * 64,
    )
    client, _ = _client(
        identity=_identity(ROLE_ADMIN),
        service=_DefaultService(result=image_timeline),
    )
    response = client.get(TIMELINE_PATH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["media_kind"] == "image"
    assert payload["duration_ms"] == 0
    assert payload["source_version"] == "c" * 64
    assert "path" not in response.text.lower()


def test_frame_preview_is_ephemeral_and_no_store() -> None:
    client, _ = _client(identity=_identity(ROLE_ADMIN))
    response = client.get(FRAME_PATH, params={"timestamp_ms": 500, "source_version": "a" * 64})
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"


def test_frame_preview_maps_source_changed_and_invalid_timestamp() -> None:
    client, _ = _client(
        identity=_identity(ROLE_ADMIN),
        service=_DefaultService(error=CoverSourceChangedError()),
    )
    response = client.get(FRAME_PATH, params={"timestamp_ms": 500, "source_version": "a" * 64})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COVER_SOURCE_CHANGED"

    client2, _ = _client(
        identity=_identity(ROLE_ADMIN),
        service=_DefaultService(error=CoverTimestampInvalidError()),
    )
    response = client2.get(FRAME_PATH, params={"timestamp_ms": 500, "source_version": "a" * 64})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COVER_TIMESTAMP_INVALID"


def test_accept_requires_capability_and_audit_before_mutation() -> None:
    body = {
        "timestamp_ms": 500,
        "expected_revision": 0,
        "expected_source_version": "a" * 64,
    }
    no_identity, _ = _client(identity=None, audit_proof=True)
    assert no_identity.put(COVER_PATH, json=body).status_code == 401

    no_audit, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=False,
        service=_DefaultService(result="should-not-run"),
    )
    response = no_audit.put(COVER_PATH, json=body)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "AUDIT_UNAVAILABLE"

    ordinary, _ = _client(identity=_identity(ROLE_USER), audit_proof=True)
    assert ordinary.put(COVER_PATH, json=body).status_code == 403


def test_accept_created_replaced_and_conflict_statuses() -> None:
    from kronika.application.media_cover import CoverAcceptResult

    created_client, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        service=_DefaultService(
            result=CoverAcceptResult(status="created", revision=1, timestamp_ms=500, artifact_digest="b" * 64, thumbnail_state="ready")
        ),
    )
    created = created_client.put(
        COVER_PATH,
        json={
            "timestamp_ms": 500,
            "expected_revision": 0,
            "expected_source_version": "a" * 64,
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "created"

    replaced_client, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        service=_DefaultService(
            result=CoverAcceptResult(status="replaced", revision=2, timestamp_ms=900, artifact_digest="c" * 64, thumbnail_state="ready")
        ),
    )
    assert replaced_client.put(
        COVER_PATH,
        json={
            "timestamp_ms": 900,
            "expected_revision": 1,
            "expected_source_version": "a" * 64,
        },
    ).status_code == 200

    conflict_client, _ = _client(
        identity=_identity(ROLE_ADMIN),
        audit_proof=True,
        service=_DefaultService(error=CoverConflictError()),
    )
    conflict = conflict_client.put(
        COVER_PATH,
        json={
            "timestamp_ms": 900,
            "expected_revision": 1,
            "expected_source_version": "a" * 64,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "COVER_CONFLICT"


def test_admin_state_and_thumbnail_serve_without_paths() -> None:
    client, _ = _client(identity=_identity(ROLE_ADMIN))
    state = client.get(STATE_PATH)
    assert state.status_code == 200
    assert state.json()["has_cover"] is True
    assert state.json()["thumbnail_state"] == "ready"

    thumb = client.get(THUMB_PATH)
    assert thumb.status_code == 200
    assert thumb.headers["etag"] == ETAG
    assert thumb.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert "path" not in thumb.text.lower()


def test_thumbnail_matching_if_none_match_returns_304() -> None:
    client, _ = _client(identity=_identity(ROLE_ADMIN))
    response = client.get(THUMB_PATH, headers={"If-None-Match": ETAG})
    assert response.status_code == 304
    assert response.headers["etag"] == ETAG


def test_thumbnail_missing_derivative_is_sanitized_not_found() -> None:
    client, _ = _client(
        identity=_identity(ROLE_ADMIN),
        service=_DefaultService(error=CoverMediaNotFoundError()),
    )
    response = client.get(THUMB_PATH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COVER_MEDIA_NOT_FOUND"


def test_thumbnail_honors_publication_visibility() -> None:
    client, audience = _client(
        identity=_identity(ROLE_USER),
        audience=False,
    )
    response = client.get(THUMB_PATH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COVER_MEDIA_NOT_FOUND"
    assert audience.calls >= 1
    assert "/Users" not in response.text


def test_error_responses_never_expose_private_markers() -> None:
    private = "/Users/example/private/covers"
    for service in (
        _DefaultService(error=CoverSourceUnavailableError(private)),
        _DefaultService(error=CoverFailedError(private)),
        _DefaultService(error=CoverMediaNotFoundError(private)),
    ):
        client, _ = _client(identity=_identity(ROLE_ADMIN), service=service)
        response = client.get(TIMELINE_PATH)
        assert private not in response.text
