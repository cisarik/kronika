"""FastAPI routes for the first durable manual cover workflow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, UUID4

from kronika.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_decision,
    load_approved_projection,
)
from kronika.domain.record_access import READ_APPROVED, READ_DENY
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.application.media_cover import (
    CoverConflictError,
    CoverFailedError,
    CoverMediaNotFoundError,
    CoverService,
    CoverSourceChangedError,
    CoverSourceUnavailableError,
    CoverTimestampInvalidError,
)
from kronika.application.media_content import (
    MEDIA_CONTENT_FAILED_MESSAGE,
    MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
    MEDIA_NOT_FOUND_MESSAGE,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.application.ports.media_repository import FrameNestMediaRepositoryError
from kronika.domain import FrameNestIdentityError, MediaId, MediaLocationId
from kronika.domain.identity_access import (
    CAPABILITY_METADATA_CANONICAL_WRITE,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
COVER_MEDIA_NOT_FOUND_CODE = "COVER_MEDIA_NOT_FOUND"
COVER_SOURCE_UNAVAILABLE_CODE = "COVER_SOURCE_UNAVAILABLE"
COVER_SOURCE_CHANGED_CODE = "COVER_SOURCE_CHANGED"
COVER_TIMESTAMP_INVALID_CODE = "COVER_TIMESTAMP_INVALID"
COVER_CONFLICT_CODE = "COVER_CONFLICT"
COVER_FAILED_CODE = "COVER_FAILED"
IDENTITY_REQUIRED_CODE = "IDENTITY_REQUIRED"
IDENTITY_REQUIRED_MESSAGE = "A verified application identity is required."
CAPABILITY_DENIED_CODE = "CAPABILITY_DENIED"
CAPABILITY_DENIED_MESSAGE = "The verified identity is not authorized."
AUDIT_UNAVAILABLE_CODE = "AUDIT_UNAVAILABLE"
AUDIT_UNAVAILABLE_MESSAGE = "The privileged action could not be recorded."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_READY_CACHE_CONTROL = "private, max-age=0, must-revalidate"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class CoverTimelineResponse(BaseModel):
    media_id: str
    location_id: str
    media_kind: str
    duration_ms: int
    source_version: str


class CoverAcceptRequest(BaseModel):
    timestamp_ms: int
    expected_revision: int
    expected_source_version: str


class CoverAcceptResponse(BaseModel):
    status: str
    revision: int
    timestamp_ms: int
    artifact_digest: str
    thumbnail_state: str


class CoverStateResponse(BaseModel):
    media_id: str
    has_cover: bool
    revision: int | None
    timestamp_ms: int | None
    artifact_digest: str | None
    source_reference: str | None
    source_kind: str | None
    accepted_at_ms: int | None
    thumbnail_state: str
    artifact_state: str


@dataclass(frozen=True, slots=True)
class CoverApiDependencies:
    """Injected behavior for the manual cover workflow routes."""

    cover_service: CoverService
    catalog_available: Callable[[], bool]
    audience_policy: ContentAudiencePolicy | None = None


def create_cover_api_router(dependencies: CoverApiDependencies) -> APIRouter:
    """Create the manual cover authoring and delivery API router."""
    router = APIRouter()

    @router.get(
        "/api/media/{media_id}/locations/{location_id}/cover-timeline",
        response_model=CoverTimelineResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_cover_timeline(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
    ) -> CoverTimelineResponse | JSONResponse:
        identity_error = _require_capability(
            request,
            CAPABILITY_METADATA_CANONICAL_WRITE,
        )
        if identity_error is not None:
            return identity_error
        if not dependencies.catalog_available():
            return _error(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)
        try:
            timeline = dependencies.cover_service.timeline(
                MediaId.from_string(str(media_id)),
                MediaLocationId.from_string(str(location_id)),
            )
        except CoverMediaNotFoundError:
            return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except CoverSourceUnavailableError:
            return _error(
                409,
                COVER_SOURCE_UNAVAILABLE_CODE,
                MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
            )
        except (FrameNestMediaRepositoryError, FrameNestLibraryRepositoryError):
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except CoverFailedError:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except Exception:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        return CoverTimelineResponse(
            media_id=timeline.media_id.to_string(),
            location_id=timeline.location_id.to_string(),
            media_kind=timeline.media_kind.value,
            duration_ms=timeline.duration_ms,
            source_version=timeline.source_version,
        )

    @router.get(
        "/api/media/{media_id}/locations/{location_id}/cover-frame",
        response_model=None,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_cover_frame(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
        timestamp_ms: int = Query(ge=0),
        source_version: str = Query(default=""),
    ) -> Response | JSONResponse:
        identity_error = _require_capability(
            request,
            CAPABILITY_METADATA_CANONICAL_WRITE,
        )
        if identity_error is not None:
            return identity_error
        if not dependencies.catalog_available():
            return _error(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)
        try:
            preview = dependencies.cover_service.preview(
                MediaId.from_string(str(media_id)),
                MediaLocationId.from_string(str(location_id)),
                timestamp_ms=timestamp_ms,
                expected_source_version=source_version,
            )
        except CoverMediaNotFoundError:
            return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except CoverSourceChangedError:
            return _error(
                409,
                COVER_SOURCE_CHANGED_CODE,
                "The cover source changed.",
            )
        except CoverTimestampInvalidError:
            return _error(
                409,
                COVER_TIMESTAMP_INVALID_CODE,
                "The selected timestamp is invalid.",
            )
        except CoverSourceUnavailableError:
            return _error(
                409,
                COVER_SOURCE_UNAVAILABLE_CODE,
                MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
            )
        except (FrameNestMediaRepositoryError, FrameNestLibraryRepositoryError):
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except CoverFailedError:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except Exception:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        return Response(
            content=preview.payload,
            status_code=200,
            media_type=preview.media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @router.put(
        "/api/media/{media_id}/locations/{location_id}/cover",
        response_model=CoverAcceptResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def put_cover(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
        accept: CoverAcceptRequest,
    ) -> CoverAcceptResponse | JSONResponse:
        identity_error = _require_capability(
            request,
            CAPABILITY_METADATA_CANONICAL_WRITE,
        )
        if identity_error is not None:
            return identity_error
        if not request.scope.get(SCOPE_AUDIT_EVENT_ID):
            return _error(500, AUDIT_UNAVAILABLE_CODE, AUDIT_UNAVAILABLE_MESSAGE)
        if not dependencies.catalog_available():
            return _error(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)
        try:
            result = dependencies.cover_service.accept(
                MediaId.from_string(str(media_id)),
                MediaLocationId.from_string(str(location_id)),
                timestamp_ms=accept.timestamp_ms,
                expected_revision=accept.expected_revision,
                expected_source_version=accept.expected_source_version,
            )
        except (CoverMediaNotFoundError, FrameNestIdentityError):
            return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except CoverConflictError:
            return _error(409, COVER_CONFLICT_CODE, "The accepted cover changed.")
        except CoverSourceChangedError:
            return _error(
                409,
                COVER_SOURCE_CHANGED_CODE,
                "The cover source changed.",
            )
        except CoverTimestampInvalidError:
            return _error(
                409,
                COVER_TIMESTAMP_INVALID_CODE,
                "The selected timestamp is invalid.",
            )
        except CoverSourceUnavailableError:
            return _error(
                409,
                COVER_SOURCE_UNAVAILABLE_CODE,
                MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
            )
        except (FrameNestMediaRepositoryError, FrameNestLibraryRepositoryError):
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except CoverFailedError:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except Exception:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        return JSONResponse(
            status_code=201 if result.status == "created" else 200,
            content=CoverAcceptResponse(
                status=result.status,
                revision=result.revision,
                timestamp_ms=result.timestamp_ms,
                artifact_digest=result.artifact_digest,
                thumbnail_state=result.thumbnail_state,
            ).model_dump(),
            headers=_NO_STORE_HEADERS,
        )

    @router.get(
        "/api/admin/media/{media_id}/cover",
        response_model=CoverStateResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_admin_cover_state(
        media_id: UUID4,
        request: Request,
    ) -> CoverStateResponse | JSONResponse:
        identity_error = _require_capability(
            request,
            CAPABILITY_METADATA_CANONICAL_WRITE,
        )
        if identity_error is not None:
            return identity_error
        if not dependencies.catalog_available():
            return _error(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)
        try:
            state = dependencies.cover_service.admin_state(
                MediaId.from_string(str(media_id))
            )
        except (FrameNestMediaRepositoryError, FrameNestLibraryRepositoryError):
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except CoverFailedError:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except Exception:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        return CoverStateResponse(
            media_id=state.media_id,
            has_cover=state.has_cover,
            revision=state.revision,
            timestamp_ms=state.timestamp_ms,
            artifact_digest=state.artifact_digest,
            source_reference=state.source_reference,
            source_kind=state.source_kind,
            accepted_at_ms=state.accepted_at_ms,
            thumbnail_state=state.thumbnail_state,
            artifact_state=state.artifact_state,
        )

    @router.get(
        "/api/media/{media_id}/cover-thumbnail",
        response_model=None,
        responses={
            304: {"description": "Not Modified"},
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_cover_thumbnail(
        media_id: UUID4,
        request: Request,
    ) -> Response | JSONResponse:
        if not dependencies.catalog_available():
            return _error(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)
        parsed_media_id = MediaId.from_string(str(media_id))
        try:
            decision = content_audience_decision(
                request=request,
                media_id=parsed_media_id,
                policy=dependencies.audience_policy,
            )
        except ContentAudienceUnavailableError:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        if decision == READ_DENY:
            return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        try:
            if decision == READ_APPROVED:
                projection = load_approved_projection(
                    policy=dependencies.audience_policy,
                    media_id=parsed_media_id,
                )
                digest = None if projection is None else projection.cover_artifact_digest
                if not digest:
                    return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
                etag = dependencies.cover_service.thumbnail_etag_for_digest(
                    parsed_media_id,
                    digest,
                )
                if etag is None:
                    return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
                opened = dependencies.cover_service.open_thumbnail_for_digest(
                    parsed_media_id,
                    digest,
                )
            else:
                etag = dependencies.cover_service.thumbnail_etag(parsed_media_id)
                if etag is None:
                    return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
                opened = dependencies.cover_service.open_thumbnail(parsed_media_id)
        except CoverMediaNotFoundError:
            return _error(404, COVER_MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except (FrameNestMediaRepositoryError, FrameNestLibraryRepositoryError):
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except CoverFailedError:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        except Exception:
            return _error(500, COVER_FAILED_CODE, MEDIA_CONTENT_FAILED_MESSAGE)
        try:
            headers = {
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": _READY_CACHE_CONTROL,
                "ETag": etag,
                "Content-Type": opened.media_type,
                "Content-Length": str(opened.byte_size),
                "Content-Disposition": "inline",
            }
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            return Response(
                content=opened.payload,
                status_code=200,
                media_type=opened.media_type,
                headers=headers,
            )
        finally:
            opened.close()

    return router


def _require_capability(
    request: Request,
    capability: str,
) -> JSONResponse | None:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        return _error(401, IDENTITY_REQUIRED_CODE, IDENTITY_REQUIRED_MESSAGE)
    if not identity.has_capability(capability):
        return _error(403, CAPABILITY_DENIED_CODE, CAPABILITY_DENIED_MESSAGE)
    return None


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
