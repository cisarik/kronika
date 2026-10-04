"""Capability-gated administrator catalog-removal API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    SCOPE_REQUEST_ID,
)
from kronika.application.catalog_removal import (
    CatalogMediaRemovalService,
    CatalogRemovalInfrastructureError,
    CatalogRemovalNotFoundError,
    CatalogRemovalResult,
    CatalogRemovalStateConflictError,
    CatalogRemovalValidationError,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_CATALOG_REMOVE,
    IdentityContext,
)

MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
RECEIPT_NOT_FOUND_CODE = "CATALOG_REMOVAL_RECEIPT_NOT_FOUND"
RECEIPT_NOT_FOUND_MESSAGE = "Catalog removal receipt not found."
STATE_CONFLICT_CODE = "CATALOG_REMOVAL_STATE_CONFLICT"
STATE_CONFLICT_MESSAGE = (
    "Catalog removal consequences changed. Fetch a new preview and confirm again."
)
VALIDATION_CODE = "INVALID_CATALOG_REMOVAL_REQUEST"
VALIDATION_MESSAGE = "Invalid catalog removal request."
REMOVAL_FAILED_CODE = "CATALOG_REMOVAL_FAILED"
REMOVAL_FAILED_MESSAGE = "Catalog removal failed."
INFRASTRUCTURE_CODE = "CATALOG_REMOVAL_INFRASTRUCTURE_UNAVAILABLE"
INFRASTRUCTURE_MESSAGE = "Catalog removal cleanup infrastructure is unavailable."
IDENTITY_REQUIRED_CODE = "IDENTITY_REQUIRED"
IDENTITY_REQUIRED_MESSAGE = "A verified application identity is required."
CAPABILITY_DENIED_CODE = "CAPABILITY_DENIED"
CAPABILITY_DENIED_MESSAGE = "The verified identity is not authorized."
AUDIT_UNAVAILABLE_CODE = "AUDIT_UNAVAILABLE"
AUDIT_UNAVAILABLE_MESSAGE = "The privileged action could not be recorded."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class CatalogRemovalPreviewResponse(BaseModel):
    media_id: str
    display_title: str | None
    publication_state: str
    acquisition_source: str
    storage_class: str
    original_bytes_policy: str
    original_bytes_outcome: str
    recovery_limitations: list[str]
    provenance_effects: list[str]
    analysis_run_count: int
    provider_submission_count: int
    derived_artifact_cleanup_intent: list[str]
    consequence_fingerprint: str


class CatalogRemovalRequest(BaseModel):
    acknowledge_consequences: bool
    consequence_fingerprint: str = Field(min_length=64, max_length=64)


class CatalogRemovalReceiptResponse(BaseModel):
    receipt_id: str
    media_id: str
    catalog_outcome: str
    original_bytes_policy: str
    original_bytes_outcome: str
    youtube_claims_transitioned: int
    upload_publications_detached: int
    analysis_run_count: int
    provider_submission_count: int
    cover_cleanup_state: str
    preview_cleanup_state: str
    derived_artifacts_outcome: str
    cleanup_retry_available: bool


class CatalogRemovalMutationResponse(BaseModel):
    catalog_state: str
    receipt: CatalogRemovalReceiptResponse


@dataclass(frozen=True, slots=True)
class CatalogRemovalApiDependencies:
    service: CatalogMediaRemovalService


def create_catalog_removal_api_router(
    dependencies: CatalogRemovalApiDependencies,
) -> APIRouter:
    router = APIRouter(tags=["catalog-removal"])

    @router.get(
        "/api/admin/media/{media_id}/catalog-removal",
        response_model=CatalogRemovalPreviewResponse,
        responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def preview_catalog_removal(
        media_id: str, request: Request
    ) -> CatalogRemovalPreviewResponse | JSONResponse:
        identity_error = _require_capability(
            request, CAPABILITY_MEDIA_CATALOG_REMOVE
        )
        if identity_error is not None:
            return identity_error
        try:
            preview = dependencies.service.preview(media_id)
        except FrameNestIdentityError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except CatalogRemovalNotFoundError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except Exception:
            return _error(500, REMOVAL_FAILED_CODE, REMOVAL_FAILED_MESSAGE)
        return CatalogRemovalPreviewResponse(
            media_id=preview.media_id,
            display_title=preview.display_title,
            publication_state=preview.publication_state,
            acquisition_source=preview.acquisition_source,
            storage_class=preview.storage_class,
            original_bytes_policy=preview.original_bytes_policy,
            original_bytes_outcome=preview.original_bytes_outcome,
            recovery_limitations=list(preview.recovery_limitations),
            provenance_effects=list(preview.provenance_effects),
            analysis_run_count=preview.analysis_run_count,
            provider_submission_count=preview.provider_submission_count,
            derived_artifact_cleanup_intent=list(
                preview.derived_artifact_cleanup_intent
            ),
            consequence_fingerprint=preview.consequence_fingerprint,
        )

    @router.post(
        "/api/admin/media/{media_id}/catalog-removal",
        response_model=CatalogRemovalMutationResponse,
        responses={
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    def execute_catalog_removal(
        media_id: str,
        body: CatalogRemovalRequest,
        request: Request,
    ) -> CatalogRemovalMutationResponse | JSONResponse:
        identity_error = _require_capability(
            request, CAPABILITY_MEDIA_CATALOG_REMOVE
        )
        if identity_error is not None:
            return identity_error
        if request.scope.get(SCOPE_AUDIT_EVENT_ID) is None:
            return _error(503, AUDIT_UNAVAILABLE_CODE, AUDIT_UNAVAILABLE_MESSAGE)
        identity = request.scope[SCOPE_IDENTITY]
        assert isinstance(identity, IdentityContext)
        request_id = str(request.scope.get(SCOPE_REQUEST_ID, "unknown"))
        try:
            result = dependencies.service.execute(
                media_id=media_id,
                acknowledge_consequences=body.acknowledge_consequences,
                consequence_fingerprint=body.consequence_fingerprint,
                request_id=request_id[:64] or "unknown",
                actor_key=identity.login_key,
            )
        except CatalogRemovalValidationError:
            return _error(422, VALIDATION_CODE, VALIDATION_MESSAGE)
        except FrameNestIdentityError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except CatalogRemovalNotFoundError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except CatalogRemovalStateConflictError:
            return _error(409, STATE_CONFLICT_CODE, STATE_CONFLICT_MESSAGE)
        except CatalogRemovalInfrastructureError:
            return _error(503, INFRASTRUCTURE_CODE, INFRASTRUCTURE_MESSAGE)
        except Exception:
            return _error(500, REMOVAL_FAILED_CODE, REMOVAL_FAILED_MESSAGE)
        return CatalogRemovalMutationResponse(
            catalog_state=result.catalog_state,
            receipt=_receipt_response(result),
        )

    @router.post(
        "/api/admin/catalog-removal-receipts/{receipt_id}/cleanup-retry",
        response_model=CatalogRemovalMutationResponse,
        responses={
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def retry_catalog_removal_cleanup(
        receipt_id: str, request: Request
    ) -> CatalogRemovalMutationResponse | JSONResponse:
        identity_error = _require_capability(
            request, CAPABILITY_MEDIA_CATALOG_REMOVE
        )
        if identity_error is not None:
            return identity_error
        if request.scope.get(SCOPE_AUDIT_EVENT_ID) is None:
            return _error(503, AUDIT_UNAVAILABLE_CODE, AUDIT_UNAVAILABLE_MESSAGE)
        try:
            result = dependencies.service.retry_cleanup(receipt_id)
        except CatalogRemovalNotFoundError:
            return _error(404, RECEIPT_NOT_FOUND_CODE, RECEIPT_NOT_FOUND_MESSAGE)
        except CatalogRemovalInfrastructureError:
            return _error(503, INFRASTRUCTURE_CODE, INFRASTRUCTURE_MESSAGE)
        except Exception:
            return _error(500, REMOVAL_FAILED_CODE, REMOVAL_FAILED_MESSAGE)
        return CatalogRemovalMutationResponse(
            catalog_state=result.catalog_state,
            receipt=_receipt_response(result),
        )

    return router


def _receipt_response(result: CatalogRemovalResult) -> CatalogRemovalReceiptResponse:
    receipt = result.receipt
    return CatalogRemovalReceiptResponse(
        receipt_id=receipt.id,
        media_id=receipt.media_id,
        catalog_outcome=receipt.catalog_outcome,
        original_bytes_policy=receipt.original_bytes_policy,
        original_bytes_outcome=receipt.original_bytes_outcome,
        youtube_claims_transitioned=receipt.youtube_claims_transitioned,
        upload_publications_detached=receipt.upload_publications_detached,
        analysis_run_count=receipt.analysis_run_count,
        provider_submission_count=receipt.provider_submission_count,
        cover_cleanup_state=receipt.cover_cleanup_state,
        preview_cleanup_state=receipt.preview_cleanup_state,
        derived_artifacts_outcome=result.derived_artifacts_outcome,
        cleanup_retry_available=result.cleanup_retry_available,
    )


def _require_capability(
    request: Request, capability: str
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
