"""Authenticated administrator X claim review API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.x_acquisition import (
    XAcquisitionInfrastructureError,
    XAcquisitionNotFoundError,
)
from kronika.domain.identity_access import (
    CAPABILITY_X_ACQUIRE,
    IdentityContext,
)
from kronika.domain.x_acquisition import XPostClaimId
from kronika.structured_logging import get_logger

X_ADMIN_NOT_CONFIGURED = "X_ADMIN_NOT_CONFIGURED"
X_ADMIN_NOT_FOUND = "X_ADMIN_NOT_FOUND"
X_ADMIN_UNAVAILABLE = "X_ADMIN_UNAVAILABLE"
X_ADMIN_IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
X_ADMIN_CAPABILITY_DENIED = "CAPABILITY_DENIED"

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
LOGGER = get_logger("x_admin_api")


class XAdminAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    ordinal: int
    media_type: str
    state: str
    acquired_bytes: int | None
    media_id: str | None
    failure_code: str | None


class XAdminClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    state: str
    phase: str
    x_post_id: str
    submitted_url: str
    canonical_url: str
    title: str | None
    source_author_handle: str | None
    source_author_display_name: str | None
    discovered_asset_count: int
    success_count: int
    failure_count: int
    failure_code: str | None
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None
    assets: list[XAdminAssetResponse] = []


class XAdminErrorBody(BaseModel):
    code: str
    message: str


class XAdminErrorResponse(BaseModel):
    error: XAdminErrorBody


@dataclass(frozen=True, slots=True)
class XAdminApiDependencies:
    """Injected administrator review behavior."""

    service: object | None
    enabled: bool


def create_x_admin_api_router(
    dependencies: XAdminApiDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/admin/x/requests/{claim_id}",
        response_model=XAdminClaimResponse,
        responses={403: {"model": XAdminErrorResponse}},
    )
    async def get_x_admin_claim(request: Request, claim_id: str) -> JSONResponse:
        identity = _require_admin(request)
        if not dependencies.enabled or dependencies.service is None:
            return _error(X_ADMIN_NOT_CONFIGURED, "X review is unavailable.", 503)
        try:
            parsed = XPostClaimId.from_string(claim_id)
        except Exception:
            return _error(X_ADMIN_NOT_FOUND, "X claim not found.", 404)
        try:
            claim = dependencies.service.get(parsed)
        except XAcquisitionNotFoundError:
            return _error(X_ADMIN_NOT_FOUND, "X claim not found.", 404)
        except XAcquisitionInfrastructureError:
            return _error(X_ADMIN_UNAVAILABLE, "X review is unavailable.", 503)
        return JSONResponse(
            status_code=200, content=_claim_dict(claim), headers=_NO_STORE_HEADERS
        )

    return router


def _require_admin(request: Request) -> IdentityContext:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    if not identity.has_capability(CAPABILITY_X_ACQUIRE):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_DENIED"})
    return identity


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )


def _claim_dict(claim: object) -> dict:
    return {
        "claim_id": claim.claim_id,
        "state": claim.state,
        "phase": claim.phase,
        "x_post_id": claim.x_post_id,
        "submitted_url": claim.submitted_url,
        "canonical_url": claim.canonical_url,
        "title": claim.title,
        "source_author_handle": claim.source_author_handle,
        "source_author_display_name": claim.source_author_display_name,
        "discovered_asset_count": claim.discovered_asset_count,
        "success_count": claim.success_count,
        "failure_count": claim.failure_count,
        "failure_code": claim.failure_code,
        "created_at_ms": claim.created_at_ms,
        "updated_at_ms": claim.updated_at_ms,
        "completed_at_ms": claim.completed_at_ms,
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