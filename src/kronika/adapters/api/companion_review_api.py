"""Administrator companion review inbox HTTP adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID, SCOPE_IDENTITY
from kronika.application.companion_review import (
    COMPANION_REVIEW_QUERY_INVALID_MESSAGE,
    DEFAULT_COMPANION_REVIEW_LIMIT,
    MAX_COMPANION_REVIEW_LIMIT,
    ApplyCompanionReview,
    CompanionReviewApplyResult,
    CompanionReviewDetail,
    CompanionReviewFieldSource,
    CompanionReviewInboxItem,
    CompanionReviewInboxPage,
    CompanionReviewOpenedResult,
    CompanionReviewQueryError,
    GetCompanionReviewDetail,
    ListCompanionReviewInbox,
    MappedSuggestedTag,
    MarkCompanionReviewOpened,
)
from kronika.application.ports.companion_review_repository import (
    CompanionReviewAnalysisRunNotFoundError,
    CompanionReviewMediaNotFoundError,
    CompanionReviewMovieExcludedError,
    CompanionReviewRunNotEligibleError,
    CompanionReviewStaleMappingError,
    CompanionReviewStoredResultError,
    CompanionReviewTagLimitConflictError,
    FrameNestCompanionReviewRepositoryError,
)
from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_CONTENT_PUBLISH,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_METADATA_CANONICAL_WRITE,
    CAPABILITY_X_REQUEST,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
QUERY_INVALID_CODE = "INVALID_COMPANION_REVIEW_QUERY"
QUERY_FAILED_CODE = "COMPANION_REVIEW_QUERY_FAILED"
QUERY_FAILED_MESSAGE = "Companion review query failed."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "The requested media item was not found."
ANALYSIS_RUN_NOT_FOUND_CODE = "ANALYSIS_RUN_NOT_FOUND"
ANALYSIS_RUN_NOT_FOUND_MESSAGE = "The requested analysis run was not found."
MOVIE_EXCLUDED_CODE = "COMPANION_REVIEW_MOVIE_EXCLUDED"
MOVIE_EXCLUDED_MESSAGE = "Movie workflows are excluded from companion review."
RUN_NOT_ELIGIBLE_CODE = "COMPANION_REVIEW_RUN_CONFLICT"
RUN_NOT_ELIGIBLE_MESSAGE = "The requested analysis run is not eligible."
STALE_MAPPING_CODE = "COMPANION_REVIEW_STALE_MAPPING"
STALE_MAPPING_MESSAGE = "Submitted tag keys are not an eligible mapping."
TAG_LIMIT_CONFLICT_CODE = "COMPANION_REVIEW_TAG_LIMIT_CONFLICT"
TAG_LIMIT_CONFLICT_MESSAGE = (
    "Applying the selected tags would exceed the canonical tag limit."
)
RESULT_INVALID_CODE = "COMPANION_REVIEW_RESULT_INVALID"
RESULT_INVALID_MESSAGE = "Stored analysis result is invalid."
APPLY_FAILED_CODE = "COMPANION_REVIEW_APPLY_FAILED"
APPLY_FAILED_MESSAGE = "Companion review apply failed."
OPEN_FAILED_CODE = "COMPANION_REVIEW_OPEN_FAILED"
OPEN_FAILED_MESSAGE = "Companion review open failed."
AUDIT_UNAVAILABLE_CODE = "AUDIT_UNAVAILABLE"
AUDIT_UNAVAILABLE_MESSAGE = "The privileged action could not be recorded."
CAPABILITY_DENIED_CODE = "CAPABILITY_DENIED"

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class CompanionErrorBody(BaseModel):
    code: str
    message: str


class CompanionErrorResponse(BaseModel):
    error: CompanionErrorBody


class CompanionReviewInboxItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    title: str
    created_at_ms: int
    analyzed: bool
    analysis_run_id: str | None
    completed_at_ms: int | None
    unopened: bool


class CompanionReviewInboxListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CompanionReviewInboxItemResponse]
    unopened_count: int
    next_cursor: str | None


class CompanionReviewOpenedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_run_id: str


class CompanionReviewApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_run_id: str
    fields: list[str]
    tag_keys: list[str] = []


@dataclass(frozen=True, slots=True)
class CompanionReviewApiDependencies:
    """Injected companion review read and mutation behavior."""

    list_inbox: ListCompanionReviewInbox | None
    get_detail: GetCompanionReviewDetail | None
    catalog_available: Callable[[], bool]
    mark_opened: MarkCompanionReviewOpened | None = None
    apply_review: ApplyCompanionReview | None = None


def create_companion_review_api_router(
    dependencies: CompanionReviewApiDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/companion/review-inbox",
        response_model=CompanionReviewInboxListResponse,
        responses={
            422: {"model": CompanionErrorResponse},
            500: {"model": CompanionErrorResponse},
            503: {"model": CompanionErrorResponse},
        },
    )
    def list_review_inbox(
        request: Request,
        limit: int = Query(default=DEFAULT_COMPANION_REVIEW_LIMIT),
        cursor: str | None = None,
    ) -> JSONResponse:
        identity = _require_identity(request)
        if (
            not dependencies.catalog_available()
            or dependencies.list_inbox is None
        ):
            return _error(
                CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE, 503
            )
        if limit < 1 or limit > MAX_COMPANION_REVIEW_LIMIT:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        try:
            page = dependencies.list_inbox.execute(
                actor_login_key=identity.login_key,
                limit=limit,
                cursor=cursor,
            )
        except CompanionReviewQueryError:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        except CompanionReviewStoredResultError:
            return _error(RESULT_INVALID_CODE, RESULT_INVALID_MESSAGE, 500)
        except FrameNestCompanionReviewRepositoryError:
            return _error(QUERY_FAILED_CODE, QUERY_FAILED_MESSAGE, 500)
        return JSONResponse(
            status_code=200,
            content=_inbox_page_dict(page),
            headers=_NO_STORE_HEADERS,
        )

    @router.get(
        "/api/companion/own-history",
        response_model=CompanionReviewInboxListResponse,
        responses={
            422: {"model": CompanionErrorResponse},
            500: {"model": CompanionErrorResponse},
            503: {"model": CompanionErrorResponse},
        },
    )
    def list_own_history(
        request: Request,
        limit: int = Query(default=DEFAULT_COMPANION_REVIEW_LIMIT),
        cursor: str | None = None,
    ) -> JSONResponse:
        identity = _require_x_request_identity(request)
        if (
            not dependencies.catalog_available()
            or dependencies.list_inbox is None
        ):
            return _error(
                CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE, 503
            )
        if limit < 1 or limit > MAX_COMPANION_REVIEW_LIMIT:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        try:
            page = dependencies.list_inbox.execute(
                actor_login_key=identity.login_key,
                limit=limit,
                cursor=cursor,
                own_history=True,
            )
        except CompanionReviewQueryError:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        except CompanionReviewStoredResultError:
            return _error(RESULT_INVALID_CODE, RESULT_INVALID_MESSAGE, 500)
        except FrameNestCompanionReviewRepositoryError:
            return _error(QUERY_FAILED_CODE, QUERY_FAILED_MESSAGE, 500)
        return JSONResponse(
            status_code=200,
            content=_inbox_page_dict(page),
            headers=_NO_STORE_HEADERS,
        )

    @router.get(
        "/api/companion/review-inbox/{media_id}",
        responses={
            404: {"model": CompanionErrorResponse},
            409: {"model": CompanionErrorResponse},
            422: {"model": CompanionErrorResponse},
            500: {"model": CompanionErrorResponse},
            503: {"model": CompanionErrorResponse},
        },
    )
    def get_review_detail(
        media_id: str,
        request: Request,
        limit: int = Query(default=DEFAULT_COMPANION_REVIEW_LIMIT),
        cursor: str | None = None,
    ) -> JSONResponse:
        identity = _require_identity(request)
        if (
            not dependencies.catalog_available()
            or dependencies.get_detail is None
        ):
            return _error(
                CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE, 503
            )
        if limit < 1 or limit > MAX_COMPANION_REVIEW_LIMIT:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        try:
            detail = dependencies.get_detail.execute(
                media_id=media_id,
                actor_login_key=identity.login_key,
                limit=limit,
                cursor=cursor,
            )
        except CompanionReviewQueryError:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        except CompanionReviewMediaNotFoundError:
            return _error(MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE, 404)
        except CompanionReviewMovieExcludedError:
            return _error(MOVIE_EXCLUDED_CODE, MOVIE_EXCLUDED_MESSAGE, 409)
        except CompanionReviewStoredResultError:
            return _error(RESULT_INVALID_CODE, RESULT_INVALID_MESSAGE, 500)
        except FrameNestCompanionReviewRepositoryError:
            return _error(QUERY_FAILED_CODE, QUERY_FAILED_MESSAGE, 500)
        return JSONResponse(
            status_code=200,
            content=_detail_dict(detail),
            headers=_NO_STORE_HEADERS,
        )

    @router.post(
        "/api/companion/review-inbox/{media_id}/opened",
        responses={
            404: {"model": CompanionErrorResponse},
            409: {"model": CompanionErrorResponse},
            422: {"model": CompanionErrorResponse},
            500: {"model": CompanionErrorResponse},
            503: {"model": CompanionErrorResponse},
        },
    )
    def mark_review_opened(
        media_id: str,
        payload: CompanionReviewOpenedRequest,
        request: Request,
    ) -> JSONResponse:
        identity = _require_opened_identity(request)
        audit_error = _require_audit_event(request)
        if audit_error is not None:
            return audit_error
        if (
            not dependencies.catalog_available()
            or dependencies.mark_opened is None
        ):
            return _error(
                CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE, 503
            )
        try:
            result = dependencies.mark_opened.execute(
                media_id=media_id,
                actor_login_key=identity.login_key,
                analysis_run_id=payload.analysis_run_id,
                require_owner=not identity.has_capability(
                    CAPABILITY_MEDIA_WORKFLOW_READ
                ),
            )
        except CompanionReviewQueryError:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        except CompanionReviewMediaNotFoundError:
            return _error(MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE, 404)
        except CompanionReviewAnalysisRunNotFoundError:
            return _error(
                ANALYSIS_RUN_NOT_FOUND_CODE, ANALYSIS_RUN_NOT_FOUND_MESSAGE, 404
            )
        except CompanionReviewMovieExcludedError:
            return _error(MOVIE_EXCLUDED_CODE, MOVIE_EXCLUDED_MESSAGE, 409)
        except CompanionReviewRunNotEligibleError:
            return _error(RUN_NOT_ELIGIBLE_CODE, RUN_NOT_ELIGIBLE_MESSAGE, 409)
        except CompanionReviewStoredResultError:
            return _error(RESULT_INVALID_CODE, RESULT_INVALID_MESSAGE, 500)
        except FrameNestCompanionReviewRepositoryError:
            return _error(OPEN_FAILED_CODE, OPEN_FAILED_MESSAGE, 500)
        return JSONResponse(
            status_code=200,
            content=_opened_dict(result),
            headers=_NO_STORE_HEADERS,
        )

    @router.post(
        "/api/companion/review-inbox/{media_id}/apply",
        responses={
            404: {"model": CompanionErrorResponse},
            409: {"model": CompanionErrorResponse},
            422: {"model": CompanionErrorResponse},
            500: {"model": CompanionErrorResponse},
            503: {"model": CompanionErrorResponse},
        },
    )
    def apply_review(
        media_id: str,
        payload: CompanionReviewApplyRequest,
        request: Request,
    ) -> JSONResponse:
        identity = _require_apply_identity(request)
        audit_error = _require_audit_event(request)
        if audit_error is not None:
            return audit_error
        if (
            not dependencies.catalog_available()
            or dependencies.apply_review is None
        ):
            return _error(
                CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE, 503
            )
        try:
            result = dependencies.apply_review.execute(
                media_id=media_id,
                actor_login_key=identity.login_key,
                analysis_run_id=payload.analysis_run_id,
                fields=tuple(payload.fields),
                tag_keys=tuple(payload.tag_keys),
            )
        except CompanionReviewQueryError:
            return _error(
                QUERY_INVALID_CODE, COMPANION_REVIEW_QUERY_INVALID_MESSAGE, 422
            )
        except CompanionReviewMediaNotFoundError:
            return _error(MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE, 404)
        except CompanionReviewAnalysisRunNotFoundError:
            return _error(
                ANALYSIS_RUN_NOT_FOUND_CODE, ANALYSIS_RUN_NOT_FOUND_MESSAGE, 404
            )
        except CompanionReviewMovieExcludedError:
            return _error(MOVIE_EXCLUDED_CODE, MOVIE_EXCLUDED_MESSAGE, 409)
        except CompanionReviewRunNotEligibleError:
            return _error(RUN_NOT_ELIGIBLE_CODE, RUN_NOT_ELIGIBLE_MESSAGE, 409)
        except CompanionReviewStaleMappingError:
            return _error(STALE_MAPPING_CODE, STALE_MAPPING_MESSAGE, 409)
        except CompanionReviewTagLimitConflictError:
            return _error(TAG_LIMIT_CONFLICT_CODE, TAG_LIMIT_CONFLICT_MESSAGE, 409)
        except CompanionReviewStoredResultError:
            return _error(RESULT_INVALID_CODE, RESULT_INVALID_MESSAGE, 500)
        except FrameNestCompanionReviewRepositoryError:
            return _error(APPLY_FAILED_CODE, APPLY_FAILED_MESSAGE, 500)
        return JSONResponse(
            status_code=200,
            content=_apply_dict(result),
            headers=_NO_STORE_HEADERS,
        )

    return router


def _require_identity(request: Request) -> IdentityContext:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    if not identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ):
        raise HTTPException(status_code=403, detail={"code": CAPABILITY_DENIED_CODE})
    return identity


def _require_opened_identity(request: Request) -> IdentityContext:
    return _require_x_request_identity(request)


def _require_x_request_identity(request: Request) -> IdentityContext:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    if not identity.has_capability(CAPABILITY_X_REQUEST):
        raise HTTPException(status_code=403, detail={"code": CAPABILITY_DENIED_CODE})
    return identity


def _require_apply_identity(request: Request) -> IdentityContext:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    if not identity.has_capability(CAPABILITY_MEDIA_CONTENT_PUBLISH) or not (
        identity.has_capability(CAPABILITY_METADATA_CANONICAL_WRITE)
    ):
        raise HTTPException(status_code=403, detail={"code": CAPABILITY_DENIED_CODE})
    return identity


def _require_audit_event(request: Request) -> JSONResponse | None:
    if not request.scope.get(SCOPE_AUDIT_EVENT_ID):
        return _error(AUDIT_UNAVAILABLE_CODE, AUDIT_UNAVAILABLE_MESSAGE, 500)
    return None


def _inbox_page_dict(page: CompanionReviewInboxPage) -> dict:
    return {
        "items": [_inbox_item_dict(item) for item in page.items],
        "unopened_count": page.unopened_count,
        "next_cursor": page.next_cursor,
    }


def _inbox_item_dict(item: CompanionReviewInboxItem) -> dict:
    return {
        "media_id": item.media_id,
        "title": item.title,
        "created_at_ms": item.created_at_ms,
        "analyzed": item.analyzed,
        "analysis_run_id": item.analysis_run_id,
        "completed_at_ms": item.completed_at_ms,
        "unopened": item.unopened,
    }


def _detail_dict(detail: CompanionReviewDetail) -> dict:
    publication = detail.publication
    return {
        "media_id": detail.media_id,
        "canonical": {
            "display_title": detail.display_title,
            "description": detail.description,
            "tags": [
                {
                    "key": tag.key,
                    "display_name": tag.display_name,
                    "position": tag.position,
                }
                for tag in detail.tags
            ],
            "field_sources": {
                name: None if receipt is None else _receipt_dict(receipt)
                for name, receipt in detail.field_sources.items()
            },
            "tag_sources": _tag_sources_dict(detail.tag_sources),
        },
        "publication": {
            "state": "published" if publication is not None else "unpublished",
            "origin": (
                None if publication is None else publication.publication_origin.value
            ),
            "published_at_ms": (
                None if publication is None else publication.published_at_ms
            ),
            "ready": detail.readiness.ready,
            "missing_fields": list(detail.readiness.missing_fields),
        },
        "suggestions": [
            {
                "analysis_run_id": suggestion.analysis_run_id,
                "completed_at_ms": suggestion.completed_at_ms,
                "provider_id": suggestion.provider_id,
                "model_id": suggestion.model_id,
                "prompt_version": suggestion.prompt_version,
                "title": suggestion.title,
                "description": suggestion.description,
                "tags": [_tag_dict(tag) for tag in suggestion.tags],
            }
            for suggestion in detail.suggestions
        ],
        "next_cursor": detail.next_cursor,
    }


def _receipt_dict(receipt: CompanionReviewFieldSource) -> dict:
    return {
        "analysis_run_id": receipt.analysis_run_id,
        "completed_at_ms": receipt.completed_at_ms,
        "provider_id": receipt.provider_id,
        "model_id": receipt.model_id,
        "applied_at_ms": receipt.applied_at_ms,
    }


def _tag_sources_dict(
    sources: dict[str, CompanionReviewFieldSource],
) -> dict[str, dict]:
    return {key: _receipt_dict(receipt) for key, receipt in sources.items()}


def _tag_dict(tag: MappedSuggestedTag) -> dict:
    return {
        "value": tag.value,
        "status": tag.status.value,
        "key": tag.key,
        "display_name": tag.display_name,
    }


def _opened_dict(result: CompanionReviewOpenedResult) -> dict:
    return {
        "media_id": result.media_id,
        "opened_run_id": result.opened_run_id,
        "opened_at_ms": result.opened_at_ms,
        "unopened": result.unopened,
    }


def _apply_dict(result: CompanionReviewApplyResult) -> dict:
    canonical = result.canonical
    publication = result.publication
    return {
        "metadata_status": result.metadata_status,
        "canonical": {
            "display_title": canonical.display_title,
            "description": canonical.description,
            "tags": [
                {
                    "key": tag.key,
                    "display_name": tag.display_name,
                    "position": tag.position,
                }
                for tag in canonical.tags
            ],
            "field_sources": {
                name: None if receipt is None else _receipt_dict(receipt)
                for name, receipt in canonical.field_sources.items()
            },
            "tag_sources": _tag_sources_dict(canonical.tag_sources),
        },
        "publication": {
            "status": publication.status,
            "state": publication.state,
            "origin": publication.origin,
            "published_at_ms": publication.published_at_ms,
            "ready": publication.ready,
            "missing_fields": list(publication.missing_fields),
        },
    }


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
