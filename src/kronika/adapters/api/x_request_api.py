"""Authenticated ordinary-user X request API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.ports.media_user_alias_repository import (
    AliasTagNotFoundError,
)
from kronika.application.x_acquisition import (
    XAcquisitionCategoryConflictError,
    XAcquisitionInfrastructureError,
    XAcquisitionInvalidCursorError,
    XAcquisitionInvalidRequestError,
    XAcquisitionInvalidRequesterIdentityError,
    XAcquisitionNoRetryableAssetsError,
    XAcquisitionNotRetryableError,
    XAcquisitionNotFoundError,
    XRequestInsufficientStorageError,
    XRequestLimitError,
)
from kronika.domain.identity_access import (
    CAPABILITY_X_REQUEST,
    IdentityContext,
)
from kronika.domain.media_user_alias import (
    FrameNestMediaUserAliasError,
    parse_alias_content,
)
from kronika.domain.security_audit import (
    AUDIT_OUTCOME_ALLOWED,
    SecurityAuditEvent,
)
from kronika.domain.x_acquisition import (
    FrameNestXClaimError,
    FrameNestXUrlError,
    XPostClaimId,
    parse_x_requested_content_category,
)
from kronika.structured_logging import get_logger

X_REQUEST_NOT_CONFIGURED = "X_REQUEST_NOT_CONFIGURED"
X_REQUEST_INVALID_URL = "X_REQUEST_INVALID_URL"
X_REQUEST_NOT_FOUND = "X_REQUEST_NOT_FOUND"
X_REQUEST_STATE_CONFLICT = "X_REQUEST_STATE_CONFLICT"
X_REQUEST_UNAVAILABLE = "X_REQUEST_UNAVAILABLE"
X_REQUEST_INSUFFICIENT_STORAGE = "X_REQUEST_INSUFFICIENT_STORAGE"
X_REQUEST_IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
X_REQUEST_INVALID_CATEGORY = "X_REQUEST_INVALID_CATEGORY"
X_REQUEST_CATEGORY_CONFLICT = "X_REQUEST_CATEGORY_CONFLICT"
ALIAS_TAG_NOT_FOUND = "ALIAS_TAG_NOT_FOUND"
ALIAS_INVALID = "ALIAS_INVALID"

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 50
LOGGER = get_logger("x_request_api")


class XAliasBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_title: str | None = None
    description: str | None = None
    tag_keys: list[str] | None = None


class XRequestCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    alias: XAliasBody | None = None
    content_category: str | None = None


class XAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    ordinal: int
    media_type: str
    state: str
    acquired_bytes: int | None
    media_id: str | None
    failure_code: str | None


class XRequestItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    phase: str
    state: str
    x_post_id: str
    submitted_url: str
    canonical_url: str
    title: str | None
    failure_code: str | None
    retry_of_request_id: str | None
    created_at_ms: int
    updated_at_ms: int
    requested_content_category: str | None = None
    discovered_asset_count: int | None = None
    success_count: int | None = None
    failure_count: int | None = None
    can_retry: bool = False
    submission_result: str | None = None
    assets: list[XAssetResponse] = []


class XRequestListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[XRequestItemResponse]
    next_cursor: str | None


class XRequestErrorBody(BaseModel):
    code: str
    message: str


class XRequestErrorResponse(BaseModel):
    error: XRequestErrorBody


@dataclass(frozen=True, slots=True)
class XRequestApiDependencies:
    """Injected ordinary request behavior and supplemental audit sink."""

    service: object | None
    audit_recorder: object | None
    enabled: bool


def create_x_request_api_router(
    dependencies: XRequestApiDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/x/requests",
        response_model=XRequestItemResponse,
        responses={422: {"model": XRequestErrorResponse}},
    )
    async def submit_x_request(request: Request, body: XRequestCreateBody) -> JSONResponse:
        identity = _require_identity(request)
        if not dependencies.enabled or dependencies.service is None:
            return _error(X_REQUEST_NOT_CONFIGURED, "X acquisition is unavailable.", 503)
        try:
            alias_content = None
            if body.alias is not None:
                alias_content = parse_alias_content(
                    body.alias.display_title,
                    body.alias.description,
                    body.alias.tag_keys,
                )
            requested_category = None
            if body.content_category is not None:
                try:
                    requested_category = parse_x_requested_content_category(
                        body.content_category
                    )
                except FrameNestXClaimError:
                    return _error(
                        X_REQUEST_INVALID_CATEGORY,
                        "Invalid content category.",
                        422,
                    )
            result = dependencies.service.submit(
                body.url,
                login_key=identity.login_key,
                alias=alias_content,
                content_category=requested_category,
            )
        except FrameNestMediaUserAliasError:
            return _error(ALIAS_INVALID, "Invalid Kronika media user alias.", 422)
        except AliasTagNotFoundError:
            return _error(ALIAS_TAG_NOT_FOUND, "Canonical tag not found.", 422)
        except (XAcquisitionInvalidRequestError, FrameNestXUrlError):
            return _error(X_REQUEST_INVALID_URL, "Invalid X post URL.", 422)
        except XRequestLimitError as exc:
            return _error(
                _code_for_limit(exc.code),
                _X_REQUEST_LIMIT_MESSAGES.get(
                    exc.code, _X_REQUEST_LIMIT_DEFAULT_MESSAGE
                ),
                429,
            )
        except XRequestInsufficientStorageError:
            return _error(
                X_REQUEST_INSUFFICIENT_STORAGE,
                "X staging has insufficient free space.",
                507,
            )
        except XAcquisitionCategoryConflictError:
            return _error(
                X_REQUEST_CATEGORY_CONFLICT,
                "Requested category conflicts with the existing Kronika save.",
                409,
            )
        except XAcquisitionInfrastructureError:
            return _error(X_REQUEST_UNAVAILABLE, "X alias overlay is unavailable.", 503)
        _record_result_classification(
            dependencies, request, "x.request.submit", "allowed", result.submission_result
        )
        claim = dependencies.service.get_owned(
            XPostClaimId.from_string(result.request_id),
            login_key=identity.login_key,
        )
        return JSONResponse(
            status_code=200,
            content=_item_dict(claim, submission_result=result.submission_result),
            headers=_NO_STORE_HEADERS,
        )

    @router.get(
        "/api/x/requests",
        response_model=XRequestListResponse,
    )
    async def list_x_requests(
        request: Request,
        limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> JSONResponse:
        identity = _require_identity(request)
        if not dependencies.enabled or dependencies.service is None:
            return _error(X_REQUEST_NOT_CONFIGURED, "X acquisition is unavailable.", 503)
        try:
            page = dependencies.service.list_owned(
                login_key=identity.login_key, limit=limit, cursor=cursor
            )
        except XAcquisitionInvalidCursorError:
            return _error(X_REQUEST_INVALID_URL, "Invalid X request cursor.", 422)
        except XAcquisitionInvalidRequesterIdentityError:
            return _error(X_REQUEST_INVALID_URL, "Invalid requester identity.", 422)
        except XAcquisitionInfrastructureError:
            return _error(X_REQUEST_UNAVAILABLE, "X acquisition is unavailable.", 503)
        return JSONResponse(
            status_code=200,
            content={
                "items": [_item_dict(item) for item in page.items],
                "next_cursor": page.next_cursor,
            },
            headers=_NO_STORE_HEADERS,
        )

    @router.get("/api/x/requests/{claim_id}")
    async def get_x_request(request: Request, claim_id: str) -> JSONResponse:
        identity = _require_identity(request)
        if not dependencies.enabled or dependencies.service is None:
            return _error(X_REQUEST_NOT_CONFIGURED, "X acquisition is unavailable.", 503)
        try:
            parsed = XPostClaimId.from_string(claim_id)
        except Exception:
            return _error(X_REQUEST_NOT_FOUND, "X request not found.", 404)
        try:
            claim = dependencies.service.get_owned(parsed, login_key=identity.login_key)
        except XAcquisitionNotFoundError:
            return _error(X_REQUEST_NOT_FOUND, "X request not found.", 404)
        except XAcquisitionInfrastructureError:
            return _error(X_REQUEST_UNAVAILABLE, "X acquisition is unavailable.", 503)
        return JSONResponse(
            status_code=200, content=_item_dict(claim), headers=_NO_STORE_HEADERS
        )

    @router.post("/api/x/requests/{claim_id}/retry")
    async def retry_x_request(request: Request, claim_id: str) -> JSONResponse:
        identity = _require_identity(request)
        if not dependencies.enabled or dependencies.service is None:
            return _error(X_REQUEST_NOT_CONFIGURED, "X acquisition is unavailable.", 503)
        try:
            parsed = XPostClaimId.from_string(claim_id)
        except Exception:
            return _error(X_REQUEST_NOT_FOUND, "X request not found.", 404)
        try:
            claim = dependencies.service.retry(parsed, login_key=identity.login_key)
        except XAcquisitionNotFoundError:
            return _error(X_REQUEST_NOT_FOUND, "X request not found.", 404)
        except XAcquisitionNotRetryableError:
            return _error(
                X_REQUEST_STATE_CONFLICT,
                "X claim is not retryable in its current state.",
                409,
            )
        except XAcquisitionNoRetryableAssetsError:
            return _error(
                X_REQUEST_STATE_CONFLICT,
                "X claim has no retryable assets.",
                409,
            )
        except XAcquisitionInfrastructureError:
            return _error(X_REQUEST_UNAVAILABLE, "X acquisition is unavailable.", 503)
        _record_result_classification(
            dependencies, request, "x.request.retry", "allowed", claim.state
        )
        return JSONResponse(
            status_code=200, content=_item_dict(claim), headers=_NO_STORE_HEADERS
        )

    def _record_result_classification(
        deps: XRequestApiDependencies,
        request: Request,
        action: str,
        outcome: str,
        detail: str,
    ) -> None:
        if deps.audit_recorder is None:
            return
        identity = _identity(request)
        audit_event_id = request.scope.get(SCOPE_AUDIT_EVENT_ID)
        request_id = request.scope.get("request_id")
        deps.audit_recorder.record(
            SecurityAuditEvent(
                event_id=None,
                event_type=action,
                actor_key=None if identity is None else identity.login_key,
                outcome=AUDIT_OUTCOME_ALLOWED,
                target_type="x_request",
                target_key=None,
                capability=CAPABILITY_X_REQUEST,
                detail=detail,
                occurred_at_ms=None,
                request_id=request_id,
                audit_event_id=audit_event_id,
            )
        )

    return router


def _identity(request: Request) -> IdentityContext | None:
    identity = request.scope.get(SCOPE_IDENTITY)
    return identity if isinstance(identity, IdentityContext) else None


def _require_identity(request: Request) -> IdentityContext:
    identity = _identity(request)
    if identity is None:
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    if not identity.has_capability(CAPABILITY_X_REQUEST):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_DENIED"})
    return identity


_X_REQUEST_LIMIT_MESSAGES = {
    "X_REQUEST_ACTIVE_LIMIT": "You already have an active X request.",
    "X_REQUEST_GLOBAL_QUEUE_FULL": "X request queue is full.",
    "X_REQUEST_RATE_LIMIT": "Too many X requests this hour.",
    "X_REQUEST_FAILED_24H_LIMIT": "Too many failed X requests.",
}
_X_REQUEST_LIMIT_DEFAULT_MESSAGE = "X request limit reached."


def _code_for_limit(code: str) -> str:
    return {
        "X_REQUEST_ACTIVE_LIMIT": "X_REQUEST_ACTIVE_LIMIT",
        "X_REQUEST_GLOBAL_QUEUE_FULL": "X_REQUEST_GLOBAL_QUEUE_FULL",
        "X_REQUEST_RATE_LIMIT": "X_REQUEST_RATE_LIMIT",
        "X_REQUEST_FAILED_24H_LIMIT": "X_REQUEST_FAILED_24H_LIMIT",
    }.get(code, "X_REQUEST_LIMIT")


def _map_error(exc: Exception, code: str, message: str, status: int) -> JSONResponse:
    return _error(code, message, status)


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )


def _item_dict(claim: object, *, submission_result: str | None = None) -> dict:
    requested = getattr(claim, "requested_content_category", None)
    if requested is not None and not isinstance(requested, str):
        requested = getattr(requested, "value", requested)
    return {
        "request_id": claim.claim_id,
        "claim_id": claim.claim_id,
        "phase": claim.phase,
        "state": claim.state,
        "x_post_id": claim.x_post_id,
        "submitted_url": claim.submitted_url,
        "canonical_url": claim.canonical_url,
        "title": claim.title,
        "failure_code": claim.failure_code,
        "retry_of_request_id": claim.retry_of_claim_id,
        "requested_content_category": requested,
        "discovered_asset_count": getattr(claim, "discovered_asset_count", None),
        "success_count": getattr(claim, "success_count", None),
        "failure_count": getattr(claim, "failure_count", None),
        "can_retry": bool(getattr(claim, "can_retry", False)),
        "submission_result": submission_result,
        "created_at_ms": claim.created_at_ms,
        "updated_at_ms": claim.updated_at_ms,
        "assets": [
            {
                "asset_id": a.asset_id,
                "ordinal": a.ordinal,
                "media_type": a.media_type,
                "state": a.state,
                "acquired_bytes": a.acquired_bytes,
                "media_id": a.media_id,
                "failure_code": a.failure_code,
            }
            for a in claim.assets
        ],
    }