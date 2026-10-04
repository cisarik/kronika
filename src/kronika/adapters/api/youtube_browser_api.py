"""Authenticated administrator browser API for bounded YouTube claims."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    SCOPE_REQUEST_ID,
)
from kronika.application.ports.content_publication_repository import (
    FrameNestContentPublicationRepositoryError,
)
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionInfrastructureError,
    YouTubeAcquisitionInvalidRequestError,
    YouTubeAcquisitionNotFoundError,
    YouTubeAcquisitionStateConflictError,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identity_access import (
    CAPABILITY_YOUTUBE_ACQUIRE,
    IdentityContext,
)
from kronika.domain.security_audit import (
    AUDIT_OUTCOME_ALLOWED,
    SecurityAuditEvent,
)
from kronika.domain.youtube_acquisition import YouTubeConfirmationMethod
from kronika.structured_logging import get_logger

YOUTUBE_BROWSER_NOT_CONFIGURED = "YOUTUBE_BROWSER_NOT_CONFIGURED"
YOUTUBE_BROWSER_INVALID_MEDIA_TYPE = "YOUTUBE_BROWSER_INVALID_MEDIA_TYPE"
YOUTUBE_BROWSER_INVALID_REQUEST = "YOUTUBE_BROWSER_INVALID_REQUEST"
YOUTUBE_BROWSER_INVALID_URL = "YOUTUBE_BROWSER_INVALID_URL"
YOUTUBE_BROWSER_CLAIM_NOT_FOUND = "YOUTUBE_BROWSER_CLAIM_NOT_FOUND"
YOUTUBE_BROWSER_STATE_CONFLICT = "YOUTUBE_BROWSER_STATE_CONFLICT"
YOUTUBE_BROWSER_UNAVAILABLE = "YOUTUBE_BROWSER_UNAVAILABLE"
YOUTUBE_BROWSER_IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
YOUTUBE_BROWSER_CAPABILITY_DENIED = "CAPABILITY_DENIED"
YOUTUBE_BROWSER_AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"

_JSON_MEDIA_TYPE = b"application/json"
_MAX_REQUEST_BODY_BYTES = 4_096
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
LOGGER = get_logger("youtube_browser_api")


class YouTubeBrowserClaimCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    confirmation_method: Literal["interactive"]


class YouTubeBrowserClaimRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_method: Literal["interactive"]


class YouTubeBrowserFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    code: str


class YouTubeBrowserClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    state: str
    phase: str
    submission_result: Literal[
        "new", "active_reuse", "terminal_duplicate_reuse"
    ] | None
    media_id: str | None
    catalog_state: Literal["not_cataloged", "cataloged"]
    metadata_state: Literal["unknown", "incomplete", "complete"]
    missing_metadata_fields: list[str]
    publication_state: Literal["unknown", "unpublished", "published"]
    failure: YouTubeBrowserFailureResponse | None
    retry_of_claim_id: str | None
    requester_login_key: str | None = None


class YouTubeBrowserErrorBody(BaseModel):
    code: str
    message: str


class YouTubeBrowserErrorResponse(BaseModel):
    error: YouTubeBrowserErrorBody


@dataclass(frozen=True, slots=True)
class YouTubeBrowserApiDependencies:
    """Injected bounded browser behavior and its supplemental audit sink."""

    service: object | None
    workflow_status: object | None
    audit_recorder: object | None
    enabled: bool


def create_youtube_browser_api_router(
    dependencies: YouTubeBrowserApiDependencies,
) -> APIRouter:
    """Create browser routes separate from the local operator router."""
    router = APIRouter()

    @router.post(
        "/api/admin/youtube/claims",
        response_model=YouTubeBrowserClaimResponse,
        responses={
            400: {"model": YouTubeBrowserErrorResponse},
            401: {"model": YouTubeBrowserErrorResponse},
            403: {"model": YouTubeBrowserErrorResponse},
            413: {"model": YouTubeBrowserErrorResponse},
            415: {"model": YouTubeBrowserErrorResponse},
            500: {"model": YouTubeBrowserErrorResponse},
            503: {"model": YouTubeBrowserErrorResponse},
        },
    )
    async def create_claim(request: Request) -> JSONResponse:
        guard = _guard(request, dependencies, require_audit=True)
        if guard is not None:
            return guard
        payload = await _read_json_model(request, YouTubeBrowserClaimCreateRequest)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            result = dependencies.service.submit(  # type: ignore[union-attr]
                submitted_url=payload.url,
                confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
            )
            response = _browser_response(
                result.snapshot,
                submission_result=(
                    "active_reuse"
                    if not result.created
                    else (
                        "terminal_duplicate_reuse"
                        if result.snapshot.state == "duplicate_resolved"
                        else "new"
                    )
                ),
                workflow_status=dependencies.workflow_status,
            )
            terminal_duplicate = result.snapshot.state == "duplicate_resolved"
            status_code = 200 if terminal_duplicate or not result.created else 201
            await _record_result_classification(
                request,
                dependencies,
                action=(
                    "youtube.claim.submit.terminal_duplicate_reuse"
                    if result.created and result.snapshot.state == "duplicate_resolved"
                    else (
                        "youtube.claim.submit.new"
                        if result.created
                        else "youtube.claim.submit.active_reuse"
                    )
                ),
                claim_id=result.snapshot.id,
                http_status=status_code,
            )
            return JSONResponse(
                status_code=status_code,
                content=response.model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc)

    @router.get(
        "/api/admin/youtube/claims/{claim_id}",
        response_model=YouTubeBrowserClaimResponse,
        responses={
            401: {"model": YouTubeBrowserErrorResponse},
            403: {"model": YouTubeBrowserErrorResponse},
            404: {"model": YouTubeBrowserErrorResponse},
            503: {"model": YouTubeBrowserErrorResponse},
        },
    )
    async def get_claim(claim_id: str, request: Request) -> JSONResponse:
        guard = _guard(request, dependencies)
        if guard is not None:
            return guard
        parsed = _claim_id(claim_id)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            snapshot = dependencies.service.get(parsed)  # type: ignore[union-attr]
            response = _browser_response(
                snapshot,
                submission_result=None,
                workflow_status=dependencies.workflow_status,
            )
            return JSONResponse(
                status_code=200,
                content=response.model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc)

    @router.post(
        "/api/admin/youtube/claims/{claim_id}/retry",
        response_model=YouTubeBrowserClaimResponse,
        responses={
            400: {"model": YouTubeBrowserErrorResponse},
            401: {"model": YouTubeBrowserErrorResponse},
            403: {"model": YouTubeBrowserErrorResponse},
            404: {"model": YouTubeBrowserErrorResponse},
            409: {"model": YouTubeBrowserErrorResponse},
            413: {"model": YouTubeBrowserErrorResponse},
            415: {"model": YouTubeBrowserErrorResponse},
            500: {"model": YouTubeBrowserErrorResponse},
            503: {"model": YouTubeBrowserErrorResponse},
        },
    )
    async def retry_claim(claim_id: str, request: Request) -> JSONResponse:
        guard = _guard(request, dependencies, require_audit=True)
        if guard is not None:
            return guard
        parsed = _claim_id(claim_id)
        if isinstance(parsed, JSONResponse):
            return parsed
        payload = await _read_json_model(request, YouTubeBrowserClaimRetryRequest)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            result = dependencies.service.retry(  # type: ignore[union-attr]
                parsed,
                confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
            )
            response = _browser_response(
                result.snapshot,
                submission_result="active_reuse" if not result.created else "new",
                workflow_status=dependencies.workflow_status,
            )
            status_code = 201 if result.created else 200
            await _record_result_classification(
                request,
                dependencies,
                action=(
                    "youtube.claim.retry.new"
                    if result.created
                    else "youtube.claim.retry.active_reuse"
                ),
                claim_id=result.snapshot.id,
                http_status=status_code,
            )
            return JSONResponse(
                status_code=status_code,
                content=response.model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        except Exception as exc:
            return _map_service_error(exc)

    return router


def _guard(
    request: Request,
    dependencies: YouTubeBrowserApiDependencies,
    *,
    require_audit: bool = False,
) -> JSONResponse | None:
    if not dependencies.enabled or dependencies.service is None:
        return _error(
            503,
            YOUTUBE_BROWSER_NOT_CONFIGURED,
            "YouTube acquisition is not configured.",
        )
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        return _error(
            401,
            YOUTUBE_BROWSER_IDENTITY_REQUIRED,
            "A verified application identity is required.",
        )
    if not identity.has_capability(CAPABILITY_YOUTUBE_ACQUIRE):
        return _error(
            403,
            YOUTUBE_BROWSER_CAPABILITY_DENIED,
            "The verified identity is not authorized.",
        )
    if require_audit and not request.scope.get(SCOPE_AUDIT_EVENT_ID):
        return _error(
            500,
            YOUTUBE_BROWSER_AUDIT_UNAVAILABLE,
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
            YOUTUBE_BROWSER_INVALID_MEDIA_TYPE,
            "The request media type must be application/json.",
        )
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_REQUEST_BODY_BYTES:
                return _error(
                    413,
                    YOUTUBE_BROWSER_INVALID_REQUEST,
                    "The browser request is invalid.",
                )
        return model_type.model_validate_json(bytes(body))
    except (UnicodeDecodeError, ValidationError, ValueError, json.JSONDecodeError):
        return _error(
            400,
            YOUTUBE_BROWSER_INVALID_REQUEST,
            "The browser request is invalid.",
        )


def _claim_id(value: str):
    from kronika.domain.identities import YouTubeAcquisitionClaimId

    try:
        return YouTubeAcquisitionClaimId.from_string(value)
    except FrameNestIdentityError:
        return _error(
            404,
            YOUTUBE_BROWSER_CLAIM_NOT_FOUND,
            "YouTube acquisition claim was not found.",
        )


def _browser_response(
    snapshot: object,
    *,
    submission_result: Literal["new", "active_reuse", "terminal_duplicate_reuse"]
    | None,
    workflow_status: object | None,
) -> YouTubeBrowserClaimResponse:
    media_id = snapshot.media_id
    metadata_state: Literal["unknown", "incomplete", "complete"] = "unknown"
    missing_metadata_fields: list[str] = []
    publication_state: Literal["unknown", "unpublished", "published"] = "unknown"
    if media_id is not None:
        if workflow_status is None:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            )
        try:
            status = workflow_status.execute(media_id)
        except FrameNestContentPublicationRepositoryError as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        except Exception as exc:
            raise YouTubeAcquisitionInfrastructureError(
                "YouTube acquisition is unavailable."
            ) from exc
        metadata_state = status.metadata_state
        missing_metadata_fields = list(status.missing_metadata_fields)
        publication_state = status.publication_state
    failure = None
    if snapshot.failure_stage is not None and snapshot.failure_code is not None:
        failure = YouTubeBrowserFailureResponse(
            stage=snapshot.failure_stage,
            code=snapshot.failure_code,
        )
    return YouTubeBrowserClaimResponse(
        claim_id=snapshot.id,
        state=snapshot.state,
        phase=_phase(snapshot.state),
        submission_result=submission_result,
        media_id=media_id,
        catalog_state="cataloged" if media_id is not None else "not_cataloged",
        metadata_state=metadata_state,
        missing_metadata_fields=missing_metadata_fields,
        publication_state=publication_state,
        failure=failure,
        retry_of_claim_id=snapshot.retry_of_claim_id,
        requester_login_key=getattr(snapshot, "requester_login_key", None),
    )


def _phase(state: str) -> str:
    if state == "claimed":
        return "queued"
    if state == "inspecting":
        return "inspecting"
    if state in {"download_pending", "downloading"}:
        return "downloading"
    if state in {"downloaded", "handoff", "handed_off"}:
        return "handoff"
    if state in {"duplicate_resolved", "cataloged"}:
        return "cataloged"
    if state == "failed":
        return "failed"
    return "unknown"


def _map_service_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, YouTubeAcquisitionInvalidRequestError):
        return _error(
            400,
            YOUTUBE_BROWSER_INVALID_URL,
            "The public YouTube video URL is invalid.",
        )
    if isinstance(exc, YouTubeAcquisitionNotFoundError):
        return _error(
            404,
            YOUTUBE_BROWSER_CLAIM_NOT_FOUND,
            "YouTube acquisition claim was not found.",
        )
    if isinstance(exc, YouTubeAcquisitionStateConflictError):
        return _error(
            409,
            YOUTUBE_BROWSER_STATE_CONFLICT,
            "The YouTube acquisition state conflicts with this operation.",
        )
    if isinstance(exc, YouTubeAcquisitionInfrastructureError):
        return _error(
            503,
            YOUTUBE_BROWSER_UNAVAILABLE,
            "YouTube acquisition is unavailable.",
        )
    return _error(
        503,
        YOUTUBE_BROWSER_UNAVAILABLE,
        "YouTube acquisition is unavailable.",
    )


async def _record_result_classification(
    request: Request,
    dependencies: YouTubeBrowserApiDependencies,
    *,
    action: str,
    claim_id: str,
    http_status: int,
) -> None:
    recorder = dependencies.audit_recorder
    identity = request.scope.get(SCOPE_IDENTITY)
    if recorder is None or not isinstance(identity, IdentityContext):
        _log_result_audit_failure("missing_audit_context")
        return
    try:
        event = SecurityAuditEvent.new(
            request_id=str(request.scope.get(SCOPE_REQUEST_ID, "browser")),
            actor_login=identity.login,
            actor_key=identity.login_key,
            identity_provenance=identity.provenance,
            role=identity.role,
            capability=CAPABILITY_YOUTUBE_ACQUIRE,
            action=action,
            target_type="youtube_claim",
            target_id=claim_id,
            outcome=AUDIT_OUTCOME_ALLOWED,
            http_status=http_status,
        )
        await anyio.to_thread.run_sync(getattr(recorder, "record"), event)
    except Exception:
        _log_result_audit_failure("record_result_classification")


def _log_result_audit_failure(operation: str) -> None:
    LOGGER.emit(
        level="CRITICAL",
        event="security_audit_write_failed",
        operation=operation,
        error_code="SECURITY_AUDIT_WRITE_FAILED",
        retryable=False,
    )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
