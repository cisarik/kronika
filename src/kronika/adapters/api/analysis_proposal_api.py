"""Capability-gated analysis-proposal routes. Never run analysis."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.analysis_proposal import (
    DEFAULT_ANALYSIS_PROPOSAL_LIMIT,
    MAX_ANALYSIS_PROPOSAL_LIMIT,
    AnalysisProposalLimitError,
    AnalysisProposalValidationError,
)
from kronika.application.ports.analysis_proposal import (
    AnalysisProposalMediaNotFoundError,
    FrameNestAnalysisProposalRepositoryError,
)
from kronika.domain.identity_access import (
    CAPABILITY_ANALYSIS_PROPOSE,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
INVALID_QUERY_CODE = "INVALID_ANALYSIS_PROPOSAL_QUERY"
INVALID_QUERY_MESSAGE = "Invalid analysis proposal query."
PROPOSAL_FAILED_CODE = "ANALYSIS_PROPOSAL_FAILED"
PROPOSAL_FAILED_MESSAGE = "Analysis proposal operation failed."
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


class AnalysisProposalResponse(BaseModel):
    proposal_id: str
    media_id: str
    status: str
    created_at_ms: int


class AdminAnalysisProposalResponse(BaseModel):
    proposal_id: str
    media_id: str
    proposer_login: str
    created_at_ms: int
    status: str
    display_title: str | None
    content_publication_state: str
    publication_ready: bool
    missing_fields: list[str]


class AdminAnalysisProposalPageResponse(BaseModel):
    items: list[AdminAnalysisProposalResponse]
    total: int
    limit: int
    offset: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class AnalysisProposalApiDependencies:
    """Injected application behavior for proposal routes."""

    propose_analysis: object
    list_analysis_proposals: object
    catalog_available: Callable[[], bool]


def create_analysis_proposal_api_router(
    dependencies: AnalysisProposalApiDependencies,
) -> APIRouter:
    """Create the capability-gated proposal write and administrator list."""
    router = APIRouter()

    @router.post(
        "/api/workspace/media/{media_id}/analysis-proposals",
        response_model=AnalysisProposalResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def propose_analysis(
        request: Request,
        media_id: str,
    ) -> AnalysisProposalResponse | JSONResponse:
        identity = request.scope.get(SCOPE_IDENTITY)
        if not isinstance(identity, IdentityContext):
            return _error(401, IDENTITY_REQUIRED_CODE, IDENTITY_REQUIRED_MESSAGE)
        if not identity.has_capability(CAPABILITY_ANALYSIS_PROPOSE):
            return _error(403, CAPABILITY_DENIED_CODE, CAPABILITY_DENIED_MESSAGE)
        if not request.scope.get(SCOPE_AUDIT_EVENT_ID):
            return _error(503, AUDIT_UNAVAILABLE_CODE, AUDIT_UNAVAILABLE_MESSAGE)
        if not dependencies.catalog_available():
            return _error(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        try:
            created = dependencies.propose_analysis.execute(
                media_id=media_id,
                login_key=identity.login_key,
            )
        except AnalysisProposalMediaNotFoundError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except AnalysisProposalValidationError:
            return _error(422, INVALID_QUERY_CODE, INVALID_QUERY_MESSAGE)
        except AnalysisProposalLimitError as exc:
            return _error(429, exc.code, "Too many analysis proposals this hour.")
        except FrameNestAnalysisProposalRepositoryError:
            return _error(500, PROPOSAL_FAILED_CODE, PROPOSAL_FAILED_MESSAGE)
        except Exception:
            return _error(500, PROPOSAL_FAILED_CODE, PROPOSAL_FAILED_MESSAGE)
        return JSONResponse(
            status_code=201,
            content=AnalysisProposalResponse(
                proposal_id=created.proposal_id,
                media_id=created.media_id,
                status=created.status,
                created_at_ms=created.created_at_ms,
            ).model_dump(),
            headers=_NO_STORE_HEADERS,
        )

    @router.get(
        "/api/admin/analysis-proposals",
        response_model=AdminAnalysisProposalPageResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_analysis_proposals(
        request: Request,
        limit: int = Query(
            default=DEFAULT_ANALYSIS_PROPOSAL_LIMIT,
            ge=1,
            le=MAX_ANALYSIS_PROPOSAL_LIMIT,
        ),
        offset: int = Query(default=0, ge=0),
    ) -> AdminAnalysisProposalPageResponse | JSONResponse:
        identity = request.scope.get(SCOPE_IDENTITY)
        if not isinstance(identity, IdentityContext):
            return _error(401, IDENTITY_REQUIRED_CODE, IDENTITY_REQUIRED_MESSAGE)
        if not identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ):
            return _error(403, CAPABILITY_DENIED_CODE, CAPABILITY_DENIED_MESSAGE)
        if not dependencies.catalog_available():
            return _error(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        try:
            page = dependencies.list_analysis_proposals.execute(
                limit=limit,
                offset=offset,
            )
        except AnalysisProposalValidationError:
            return _error(422, INVALID_QUERY_CODE, INVALID_QUERY_MESSAGE)
        except FrameNestAnalysisProposalRepositoryError:
            return _error(500, PROPOSAL_FAILED_CODE, PROPOSAL_FAILED_MESSAGE)
        except Exception:
            return _error(500, PROPOSAL_FAILED_CODE, PROPOSAL_FAILED_MESSAGE)
        return AdminAnalysisProposalPageResponse(
            items=[
                AdminAnalysisProposalResponse(
                    proposal_id=item.proposal_id,
                    media_id=item.media_id,
                    proposer_login=item.proposer_login,
                    created_at_ms=item.created_at_ms,
                    status=item.status,
                    display_title=item.display_title,
                    content_publication_state=item.content_publication_state,
                    publication_ready=item.publication_ready,
                    missing_fields=list(item.missing_fields),
                )
                for item in page.items
            ],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            has_previous=page.offset > 0,
            has_next=page.offset + page.limit < page.total,
        )

    return router


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
