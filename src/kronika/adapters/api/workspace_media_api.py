"""Capability-gated contributor-scoped workspace media list."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.workspace_media import (
    DEFAULT_WORKSPACE_MEDIA_LIMIT,
    MAX_WORKSPACE_MEDIA_LIMIT,
    WorkspaceMediaValidationError,
)
from kronika.application.ports.media_attribution import (
    FrameNestMediaAttributionRepositoryError,
)
from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_WORKSPACE_READ,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
INVALID_QUERY_CODE = "INVALID_WORKSPACE_MEDIA_QUERY"
INVALID_QUERY_MESSAGE = "Invalid workspace media query."
WORKSPACE_FAILED_CODE = "WORKSPACE_MEDIA_QUERY_FAILED"
WORKSPACE_FAILED_MESSAGE = "Workspace media query failed."
IDENTITY_REQUIRED_CODE = "IDENTITY_REQUIRED"
IDENTITY_REQUIRED_MESSAGE = "A verified application identity is required."
CAPABILITY_DENIED_CODE = "CAPABILITY_DENIED"
CAPABILITY_DENIED_MESSAGE = "The verified identity is not authorized."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class WorkspaceMediaResponse(BaseModel):
    media_id: str
    media_kind: str
    created_at_ms: int
    updated_at_ms: int
    display_title: str | None
    description: str | None
    content_category: str
    acquisition_source: str
    contribution_sources: list[str]
    content_publication_state: str
    publication_ready: bool
    missing_fields: list[str]


class WorkspaceMediaPageResponse(BaseModel):
    items: list[WorkspaceMediaResponse]
    total: int
    limit: int
    offset: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class WorkspaceMediaApiDependencies:
    """Injected application behavior for the workspace media list."""

    list_workspace_media: object
    catalog_available: Callable[[], bool]


def create_workspace_media_api_router(
    dependencies: WorkspaceMediaApiDependencies,
) -> APIRouter:
    """Create the capability-gated workspace media list."""
    router = APIRouter()

    @router.get(
        "/api/workspace/media",
        response_model=WorkspaceMediaPageResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_workspace_media(
        request: Request,
        limit: int = Query(
            default=DEFAULT_WORKSPACE_MEDIA_LIMIT,
            ge=1,
            le=MAX_WORKSPACE_MEDIA_LIMIT,
        ),
        offset: int = Query(default=0, ge=0),
    ) -> WorkspaceMediaPageResponse | JSONResponse:
        identity = request.scope.get(SCOPE_IDENTITY)
        if not isinstance(identity, IdentityContext):
            return _error(401, IDENTITY_REQUIRED_CODE, IDENTITY_REQUIRED_MESSAGE)
        if not identity.has_capability(CAPABILITY_MEDIA_WORKSPACE_READ):
            return _error(403, CAPABILITY_DENIED_CODE, CAPABILITY_DENIED_MESSAGE)
        if not dependencies.catalog_available():
            return _error(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        try:
            page = dependencies.list_workspace_media.execute(
                login_key=identity.login_key,
                limit=limit,
                offset=offset,
            )
        except WorkspaceMediaValidationError:
            return _error(422, INVALID_QUERY_CODE, INVALID_QUERY_MESSAGE)
        except FrameNestMediaAttributionRepositoryError:
            return _error(500, WORKSPACE_FAILED_CODE, WORKSPACE_FAILED_MESSAGE)
        except Exception:
            return _error(500, WORKSPACE_FAILED_CODE, WORKSPACE_FAILED_MESSAGE)
        return WorkspaceMediaPageResponse(
            items=[
                WorkspaceMediaResponse(
                    media_id=item.media_id,
                    media_kind=item.media_kind,
                    created_at_ms=item.created_at_ms,
                    updated_at_ms=item.updated_at_ms,
                    display_title=item.display_title,
                    description=item.description,
                    content_category=item.content_category,
                    acquisition_source=item.acquisition_source,
                    contribution_sources=list(item.contribution_sources),
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
