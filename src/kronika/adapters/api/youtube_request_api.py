"""Authenticated ordinary-user YouTube request API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    SCOPE_REQUEST_ID,
)
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionInfrastructureError,
    YouTubeAcquisitionInvalidCursorError,
    YouTubeAcquisitionInvalidRequestError,
    YouTubeAcquisitionNotFoundError,
    YouTubeAcquisitionStateConflictError,
    YouTubeRequestInsufficientStorageError,
    YouTubeRequestLimitError,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identity_access import (
    CAPABILITY_YOUTUBE_REQUEST,
    IdentityContext,
)
from kronika.domain.security_audit import (
    AUDIT_OUTCOME_ALLOWED,
    SecurityAuditEvent,
)
from kronika.domain.youtube_acquisition import YouTubeConfirmationMethod
from kronika.structured_logging import get_logger

YOUTUBE_REQUEST_NOT_CONFIGURED = "YOUTUBE_REQUEST_NOT_CONFIGURED"
YOUTUBE_REQUEST_INVALID_MEDIA_TYPE = "YOUTUBE_REQUEST_INVALID_MEDIA_TYPE"
YOUTUBE_REQUEST_INVALID_REQUEST = "YOUTUBE_REQUEST_INVALID_REQUEST"
YOUTUBE_REQUEST_INVALID_URL = "YOUTUBE_REQUEST_INVALID_URL"
YOUTUBE_REQUEST_NOT_FOUND = "YOUTUBE_REQUEST_NOT_FOUND"
YOUTUBE_REQUEST_STATE_CONFLICT = "YOUTUBE_REQUEST_STATE_CONFLICT"
YOUTUBE_REQUEST_UNAVAILABLE = "YOUTUBE_REQUEST_UNAVAILABLE"
YOUTUBE_REQUEST_INSUFFICIENT_STORAGE = "YOUTUBE_REQUEST_INSUFFICIENT_STORAGE"
YOUTUBE_REQUEST_IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
YOUTUBE_REQUEST_CAPABILITY_DENIED = "CAPABILITY_DENIED"
YOUTUBE_REQUEST_AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"

_JSON_MEDIA_TYPE = b"application/json"
_MAX_REQUEST_BODY_BYTES = 4_096
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 50
LOGGER = get_logger("youtube_request_api")


class YouTubeRequestCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    confirmation_method: Literal["interactive"]


class YouTubeRequestRetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_method: Literal["interactive"]


class YouTubeRequestItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    phase: str
    submitted_url: str
    canonical_url: str
    media_id: str | None
    failure_category: str | None
    failure_code: str | None
    retry_of_request_id: str | None
    created_at_ms: int
    updated_at_ms: int


class YouTubeRequestListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[YouTubeRequestItemResponse]
    next_cursor: str | None


class YouTubeRequestErrorBody(BaseModel):
    code: str
    message: str


class YouTubeRequestErrorResponse(BaseModel):
    error: YouTubeRequestErrorBody


@dataclass(frozen=True, slots=True)
class YouTubeRequestApiDependencies:
    """Injected ordinary request behavior and supplemental audit sink."""

    service: object | None
    audit_recorder: object | None
    enabled: bool


def create_youtube_request_api_router(
    dependencies: YouTubeRequestApiDependencies,
) -> APIRouter:
    """Create ordinary requester routes separate from administrator claims."""
    router = APIRouter()

    @router.post(
        "/api/youtube/requests",
        response_model=YouTubeRequestItemResponse,
        responses={
            400: {"model": YouTubeRequestErrorResponse},
            401: {"model": YouTubeRequestErrorResponse},
            403: {"model": YouTubeRequestErrorResponse},
            413: {"model": YouTubeRequestErrorResponse},
            415: {"model": YouTubeRequestErrorResponse},
            429: {"model": YouTubeRequestErrorResponse},
            500: {"model": YouTubeRequestErrorResponse},
            503: {"model": YouTubeRequestErrorResponse},
            507: {"model": YouTubeRequestErrorResponse},
        },
    )
    async def create_request(request: Request) -> JSONResponse:
        guard = _guard(request, dependencies, require_audit=True)
        if guard is not None:
            return guard
        identity = request.scope[SCOPE_IDENTITY]
        assert isinstance(identity, IdentityContext)
        payload = await _read_json_model(request, YouTubeRequestCreateBody)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            result = dependencies.service.submit(  # type: ignore[union-attr]
                submitted_url=payload.url,
                confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
                created_by_login_key=identity.login_key,
            )
            status_code = 201 if result.created else 200
            await _record_result_classification(
                request,
                dependencies,
                action=(
                    "youtube.request.submit.new"
                    if result.created
                    else "youtube.request.submit.reuse"
                ),
                request_id=result.snapshot.request_id,
                http_status=status_code,
            )
            return JSONResponse(
                status_code=status_code,
                content=_item(result.snapshot).model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc, request=request)

    @router.get(
        "/api/youtube/requests",
        response_model=YouTubeRequestListResponse,
        responses={
            400: {"model": YouTubeRequestErrorResponse},
            401: {"model": YouTubeRequestErrorResponse},
            403: {"model": YouTubeRequestErrorResponse},
            503: {"model": YouTubeRequestErrorResponse},
        },
    )
    async def list_requests(
        request: Request,
        limit: int = Query(default=_DEFAULT_LIST_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> JSONResponse:
        guard = _guard(request, dependencies)
        if guard is not None:
            return guard
        identity = request.scope[SCOPE_IDENTITY]
        assert isinstance(identity, IdentityContext)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > _MAX_LIST_LIMIT
        ):
            return _error(
                400,
                YOUTUBE_REQUEST_INVALID_REQUEST,
                "Invalid YouTube request list parameters.",
            )
        try:
            page = dependencies.service.list_owned(  # type: ignore[union-attr]
                created_by_login_key=identity.login_key,
                limit=limit,
                cursor=cursor,
            )
            return JSONResponse(
                status_code=200,
                content=YouTubeRequestListResponse(
                    items=[_item(item) for item in page.items],
                    next_cursor=page.next_cursor,
                ).model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc, request=request)

    @router.get(
        "/api/youtube/requests/{request_id}",
        response_model=YouTubeRequestItemResponse,
        responses={
            401: {"model": YouTubeRequestErrorResponse},
            403: {"model": YouTubeRequestErrorResponse},
            404: {"model": YouTubeRequestErrorResponse},
            503: {"model": YouTubeRequestErrorResponse},
        },
    )
    async def get_request(request_id: str, request: Request) -> JSONResponse:
        guard = _guard(request, dependencies)
        if guard is not None:
            return guard
        identity = request.scope[SCOPE_IDENTITY]
        assert isinstance(identity, IdentityContext)
        parsed = _request_id(request_id)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            snapshot = dependencies.service.get_owned(  # type: ignore[union-attr]
                parsed,
                created_by_login_key=identity.login_key,
            )
            return JSONResponse(
                status_code=200,
                content=_item(snapshot).model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc, request=request)

    @router.post(
        "/api/youtube/requests/{request_id}/retry",
        response_model=YouTubeRequestItemResponse,
        responses={
            400: {"model": YouTubeRequestErrorResponse},
            401: {"model": YouTubeRequestErrorResponse},
            403: {"model": YouTubeRequestErrorResponse},
            404: {"model": YouTubeRequestErrorResponse},
            409: {"model": YouTubeRequestErrorResponse},
            429: {"model": YouTubeRequestErrorResponse},
            500: {"model": YouTubeRequestErrorResponse},
            503: {"model": YouTubeRequestErrorResponse},
            507: {"model": YouTubeRequestErrorResponse},
        },
    )
    async def retry_request(request_id: str, request: Request) -> JSONResponse:
        guard = _guard(request, dependencies, require_audit=True)
        if guard is not None:
            return guard
        identity = request.scope[SCOPE_IDENTITY]
        assert isinstance(identity, IdentityContext)
        parsed = _request_id(request_id)
        if isinstance(parsed, JSONResponse):
            return parsed
        payload = await _read_json_model(request, YouTubeRequestRetryBody)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            result = dependencies.service.retry(  # type: ignore[union-attr]
                parsed,
                confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
                created_by_login_key=identity.login_key,
            )
            status_code = 201 if result.created else 200
            await _record_result_classification(
                request,
                dependencies,
                action=(
                    "youtube.request.retry.new"
                    if result.created
                    else "youtube.request.retry.reuse"
                ),
                request_id=result.snapshot.request_id,
                http_status=status_code,
            )
            return JSONResponse(
                status_code=status_code,
                content=_item(result.snapshot).model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc, request=request)

    return router


def _guard(
    request: Request,
    dependencies: YouTubeRequestApiDependencies,
    *,
    require_audit: bool = False,
) -> JSONResponse | None:
    if not dependencies.enabled or dependencies.service is None:
        return _error(
            503,
            YOUTUBE_REQUEST_NOT_CONFIGURED,
            "YouTube requests are not configured.",
        )
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        return _error(
            401,
            YOUTUBE_REQUEST_IDENTITY_REQUIRED,
            "A verified application identity is required.",
        )
    if not identity.has_capability(CAPABILITY_YOUTUBE_REQUEST):
        return _error(
            403,
            YOUTUBE_REQUEST_CAPABILITY_DENIED,
            "The verified identity is not authorized.",
        )
    if require_audit and not request.scope.get(SCOPE_AUDIT_EVENT_ID):
        return _error(
            500,
            YOUTUBE_REQUEST_AUDIT_UNAVAILABLE,
            "The privileged action could not be recorded.",
        )
    return None


async def _read_json_model(
    request: Request,
    model_type: type[BaseModel],
) -> BaseModel | JSONResponse:
    media_types = [
        value.strip().lower()
        for name, value in request.scope.get("headers", ())
        if name.lower() == b"content-type"
    ]
    if media_types != [_JSON_MEDIA_TYPE]:
        return _error(
            415,
            YOUTUBE_REQUEST_INVALID_MEDIA_TYPE,
            "The request media type must be application/json.",
        )
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_REQUEST_BODY_BYTES:
                return _error(
                    413,
                    YOUTUBE_REQUEST_INVALID_REQUEST,
                    "The request body is too large.",
                )
    except Exception:
        return _error(
            400,
            YOUTUBE_REQUEST_INVALID_REQUEST,
            "The request body is invalid.",
        )
    try:
        return model_type.model_validate_json(bytes(body))
    except ValidationError:
        return _error(
            400,
            YOUTUBE_REQUEST_INVALID_REQUEST,
            "The request body is invalid.",
        )


def _request_id(value: str) -> object | JSONResponse:
    from kronika.domain.identities import YouTubeAcquisitionClaimId

    try:
        return YouTubeAcquisitionClaimId.from_string(value)
    except FrameNestIdentityError:
        return _error(
            404,
            YOUTUBE_REQUEST_NOT_FOUND,
            "YouTube request not found.",
        )


def _item(snapshot: object) -> YouTubeRequestItemResponse:
    return YouTubeRequestItemResponse(
        request_id=snapshot.request_id,  # type: ignore[attr-defined]
        phase=snapshot.phase,  # type: ignore[attr-defined]
        submitted_url=snapshot.submitted_url,  # type: ignore[attr-defined]
        canonical_url=snapshot.canonical_url,  # type: ignore[attr-defined]
        media_id=snapshot.media_id,  # type: ignore[attr-defined]
        failure_category=snapshot.failure_category,  # type: ignore[attr-defined]
        failure_code=snapshot.failure_code,  # type: ignore[attr-defined]
        retry_of_request_id=snapshot.retry_of_request_id,  # type: ignore[attr-defined]
        created_at_ms=snapshot.created_at_ms,  # type: ignore[attr-defined]
        updated_at_ms=snapshot.updated_at_ms,  # type: ignore[attr-defined]
    )


_YOUTUBE_REQUEST_LIMIT_MESSAGES = {
    "YOUTUBE_REQUEST_ACTIVE_LIMIT": "Active YouTube request limit reached.",
    "YOUTUBE_REQUEST_GLOBAL_QUEUE_FULL": "The YouTube request queue is full.",
    "YOUTUBE_REQUEST_RATE_LIMIT": "YouTube request rate limit reached.",
    "YOUTUBE_REQUEST_FAILED_24H_LIMIT": (
        "YouTube failed-request limit reached for the previous 24 hours."
    ),
    "YOUTUBE_REQUEST_PRIVATE_QUOTA": "Private YouTube download quota reached.",
}
_YOUTUBE_REQUEST_LIMIT_DEFAULT_MESSAGE = "YouTube request limit reached."


def _map_service_error(
    exc: Exception,
    *,
    request: Request | None = None,
) -> JSONResponse:
    if isinstance(exc, YouTubeAcquisitionInvalidCursorError):
        return _error(
            400,
            YOUTUBE_REQUEST_INVALID_REQUEST,
            "Invalid YouTube request cursor.",
        )
    if isinstance(exc, YouTubeAcquisitionInvalidRequestError):
        return _error(
            400,
            YOUTUBE_REQUEST_INVALID_URL,
            "Invalid public YouTube video URL.",
        )
    if isinstance(exc, YouTubeAcquisitionNotFoundError):
        return _error(
            404,
            YOUTUBE_REQUEST_NOT_FOUND,
            "YouTube request not found.",
        )
    if isinstance(exc, YouTubeAcquisitionStateConflictError):
        return _error(
            409,
            YOUTUBE_REQUEST_STATE_CONFLICT,
            "YouTube request state conflict.",
        )
    if isinstance(exc, YouTubeRequestLimitError):
        return _error(
            429,
            exc.code,
            _YOUTUBE_REQUEST_LIMIT_MESSAGES.get(
                exc.code, _YOUTUBE_REQUEST_LIMIT_DEFAULT_MESSAGE
            ),
        )
    if isinstance(exc, YouTubeRequestInsufficientStorageError):
        return _error(
            507,
            YOUTUBE_REQUEST_INSUFFICIENT_STORAGE,
            "Insufficient storage for YouTube request.",
        )
    if isinstance(exc, YouTubeAcquisitionInfrastructureError):
        return _error(
            503,
            YOUTUBE_REQUEST_UNAVAILABLE,
            "YouTube acquisition is unavailable.",
        )
    LOGGER.emit(
        level="ERROR",
        event="youtube_request_unexpected_failure",
        operation="map_service_error",
        error_code="YOUTUBE_REQUEST_UNAVAILABLE",
        context={"error_type": type(exc).__name__},
    )
    return _error(
        503,
        YOUTUBE_REQUEST_UNAVAILABLE,
        "YouTube acquisition is unavailable.",
    )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=YouTubeRequestErrorResponse(
            error=YouTubeRequestErrorBody(code=code, message=message)
        ).model_dump(),
        headers=_NO_STORE_HEADERS,
    )


async def _record_result_classification(
    request: Request,
    dependencies: YouTubeRequestApiDependencies,
    *,
    action: str,
    request_id: str,
    http_status: int,
) -> None:
    recorder = dependencies.audit_recorder
    identity = request.scope.get(SCOPE_IDENTITY)
    if recorder is None or not isinstance(identity, IdentityContext):
        return
    try:
        event = SecurityAuditEvent.new(
            request_id=str(request.scope.get(SCOPE_REQUEST_ID) or ""),
            actor_login=identity.login,
            actor_key=identity.login_key,
            identity_provenance=identity.provenance,
            role=identity.role,
            capability=CAPABILITY_YOUTUBE_REQUEST,
            action=action,
            target_type="youtube_request",
            target_id=request_id,
            outcome=AUDIT_OUTCOME_ALLOWED,
            http_status=http_status,
        )
        await anyio.to_thread.run_sync(recorder.record, event)
    except Exception:
        return
